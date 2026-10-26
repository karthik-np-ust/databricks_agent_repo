"""Deploy sample_pipelines to a target WITHOUT the Databricks CLI (Python SDK only).

Why: on networks that block downloading the CLI binary, this script deploys
the same bundle definition (databricks.yml + resources/*.yml) for local
demos. CI keeps using the real `databricks bundle deploy`.

It supports the subset of bundle features the demo uses: variables, targets,
presets.name_prefix / trigger_pause_status, workspace.root_path,
job parameters, schedules, serverless environments and spark_python_task.

Jobs it creates are tagged `deployed_by: deploy_sdk`. Before switching to CI
bundle deploys, remove them with --destroy (bundles don't adopt existing jobs).

    python scripts/deploy_sdk.py -t dev
    python scripts/deploy_sdk.py -t prod
    python scripts/deploy_sdk.py -t dev --destroy
"""

from __future__ import annotations

import argparse
import copy
import io
import re
from pathlib import Path

import yaml

from _common import REPO_ROOT, client

BUNDLE_DIR = REPO_ROOT / "sample_pipelines"
TAG = {"deployed_by": "deploy_sdk"}
_VAR = re.compile(r"\$\{(var|bundle|workspace)\.([A-Za-z0-9_]+)\}")


def load_bundle(target: str) -> tuple[dict, dict, dict]:
    bundle = yaml.safe_load((BUNDLE_DIR / "databricks.yml").read_text(encoding="utf-8"))
    tgt = bundle["targets"][target]
    variables = {k: v.get("default") for k, v in bundle.get("variables", {}).items()}
    variables.update(tgt.get("variables", {}))
    missing = [k for k, v in variables.items() if v is None]
    if missing:
        raise SystemExit(f"variables without a value for target {target}: {missing}")
    jobs: dict = {}
    for pattern in bundle.get("include", []):
        for f in sorted(BUNDLE_DIR.glob(pattern)):
            jobs.update((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("resources", {}).get("jobs", {}))
    return bundle, tgt, {"var": variables, "jobs": jobs}


def substitute(value, ctx: dict):
    if isinstance(value, str):
        return _VAR.sub(lambda m: str(ctx[m.group(1)][m.group(2)]), value)
    if isinstance(value, list):
        return [substitute(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, ctx) for k, v in value.items()}
    return value


def job_settings(key: str, spec: dict, tgt: dict, root: str, ctx: dict) -> dict:
    s = substitute(copy.deepcopy(spec), ctx)
    presets = tgt.get("presets", {})
    s["name"] = presets.get("name_prefix", "") + s.get("name", key)
    s["tags"] = {**s.get("tags", {}), **TAG}
    if "schedule" in s and presets.get("trigger_pause_status"):
        s["schedule"]["pause_status"] = presets["trigger_pause_status"]
    for task in s.get("tasks", []):
        spt = task.get("spark_python_task")
        if spt:
            # bundle paths are relative to resources/; files are uploaded under <root>/files
            rel = (BUNDLE_DIR / "resources" / spt["python_file"]).resolve().relative_to(BUNDLE_DIR)
            spt["python_file"] = f"{root}/files/{rel.as_posix()}"
            spt["source"] = "WORKSPACE"
    return s


def upload_sources(w, root: str) -> int:
    from databricks.sdk.service.workspace import ImportFormat

    count = 0
    for path in sorted((BUNDLE_DIR / "src").rglob("*.py")):
        rel = path.relative_to(BUNDLE_DIR).as_posix()
        dest = f"{root}/files/{rel}"
        w.workspace.mkdirs(dest.rsplit("/", 1)[0])
        w.workspace.upload(dest, io.BytesIO(path.read_bytes()), format=ImportFormat.AUTO, overwrite=True)
        count += 1
    return count


def existing_jobs(w) -> dict[str, int]:
    found = {}
    for job in w.jobs.list():
        if job.settings and (job.settings.tags or {}).get("deployed_by") == TAG["deployed_by"]:
            found[job.settings.name] = job.job_id
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-t", "--target", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--destroy", action="store_true")
    args = parser.parse_args()

    bundle, tgt, ctx = load_bundle(args.target)
    ctx["bundle"] = {"name": bundle["bundle"]["name"]}
    root = substitute(tgt["workspace"]["root_path"], ctx)
    ctx["workspace"] = {"root_path": root, "file_path": f"{root}/files"}
    w = client()
    current = existing_jobs(w)
    wanted = {key: job_settings(key, spec, tgt, root, ctx) for key, spec in ctx["jobs"].items()}

    if args.destroy:
        for s in wanted.values():
            if s["name"] in current:
                w.jobs.delete(current[s["name"]])
                print(f"  deleted job {s['name']}")
        return

    print(f"target {args.target}: uploaded {upload_sources(w, root)} source files to {root}/files")
    for key, s in wanted.items():
        if s["name"] in current:
            w.api_client.do("POST", "/api/2.2/jobs/reset", body={"job_id": current[s["name"]], "new_settings": s})
            print(f"  updated job {s['name']} ({current[s['name']]})")
        else:
            resp = w.api_client.do("POST", "/api/2.2/jobs/create", body=s)
            print(f"  created job {s['name']} ({resp['job_id']})")


if __name__ == "__main__":
    main()
