"""Command line: ``pipeline-agent {run,once,check,plugins,incidents,audit}``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, time as dtime, timezone

from dotenv import load_dotenv

from .config import Settings, load_agent_config
from .net import use_system_trust_store
from .runtime import build_gateway, build_github, build_instances, build_monitor
from .sdk.registry import discover_plugins
from .store.db import StateStore


def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings()
    monitor = build_monitor(settings)
    logging.info("Monitoring %d platform instance(s) every %ss", len(monitor.instances), settings.poll_interval_seconds)
    while True:
        _print_report(monitor.tick())
        time.sleep(settings.poll_interval_seconds)


def _cmd_once(args: argparse.Namespace) -> int:
    report = build_monitor(Settings()).tick()
    _print_report(report)
    return 1 if report.errors else 0


def _print_report(report) -> None:
    if report.skipped:
        print("Kill switch on: poll skipped")
        return
    print(f"{report.started_at:%Y-%m-%d %H:%M:%S}Z  runs seen: {report.runs_seen}  decisions: {len(report.decisions)}")
    for key, action, reason in report.decisions:
        print(f"  {action:<18} {key}  {reason}")
    for err in report.errors:
        print(f"  ERROR {err}")


def _cmd_check(args: argparse.Namespace) -> int:
    settings = Settings()
    config = load_agent_config(settings.config_dir)
    print(f"Config dir: {settings.config_dir}  dry_run={settings.dry_run}  enabled={settings.enabled}")
    ok = True
    for iid, inst in build_instances(config).items():
        caps = ", ".join(sorted(c.value for c in inst.capabilities))
        try:
            status = inst.healthcheck()
        except Exception as exc:
            status, ok = f"FAILED: {exc}", False
        print(f"  {iid:<20} plugin={inst.manifest.name:<12} caps=[{caps}]  health={status}")
    return 0 if ok else 1


def _cmd_plugins(args: argparse.Namespace) -> int:
    for name, cls in sorted(discover_plugins().items()):
        m = cls.manifest
        print(f"  {name:<12} v{m.version}  api={m.plugin_api_version}  caps={sorted(c.value for c in m.capabilities)}")
    return 0


def _cmd_incidents(args: argparse.Namespace) -> int:
    store = StateStore(Settings().database_url)
    if args.show:
        inc = store.get_incident(args.show)
        if inc is None:
            print(f"No incident #{args.show}")
            return 1
        print(f"Incident #{inc.id}  {inc.status}  {inc.error_class} ({inc.matched_rule or 'no rule'})  {inc.env}  {inc.pipeline_name}")
        print(f"  run: {inc.run_key}  {inc.url or ''}")
        print(f"  fix eligible: {bool(inc.fix_eligible)}  suspicious content: {bool(inc.suspicious_content)}")
        print(f"  LLM: {inc.llm_tokens} tokens, ${inc.llm_cost_usd:.4f}")
        print("  error (redacted):\n    " + "\n    ".join(inc.error_message.splitlines()[:15]))
        if inc.diagnosis:
            print("  diagnosis:")
            for key in ("summary", "root_cause", "error_class", "confidence", "failing_component", "fix_type", "suggested_fix", "evidence", "model"):
                print(f"    {key}: {inc.diagnosis.get(key)}")
        return 0
    for inc in store.list_incidents(args.status):
        flag = " [SUSPICIOUS]" if inc.suspicious_content else ""
        summary = (inc.diagnosis or {}).get("summary", "")
        print(f"  #{inc.id:<4} {inc.status:<10} {inc.error_class:<10} {inc.env:<5} {inc.pipeline_name}  {inc.matched_rule or ''}{flag}")
        if summary:
            print(f"        {summary}")
    return 0


def _cmd_triage(args: argparse.Namespace) -> int:
    settings = Settings()
    if not settings.llm_enabled:
        print("LLM is disabled. Set AGENT_LLM_ENABLED=true in .env")
        return 1
    monitor = build_monitor(settings)
    inc = monitor.store.get_incident(args.incident_id)
    if inc is None:
        print(f"No incident #{args.incident_id}")
        return 1
    diag = monitor.run_triage(inc.id, inc.error_message)
    if diag is None:
        print("No diagnosis (see `pipeline-agent audit` for llm_budget_stop / llm_unavailable)")
        return 1
    print(json.dumps(diag.model_dump(mode="json"), indent=2))
    return 0


def _cmd_llm(args: argparse.Namespace) -> int:
    settings = Settings()
    config = load_agent_config(settings.config_dir)
    store = StateStore(settings.database_url)
    now = datetime.now(timezone.utc)
    day_calls, day_tokens, day_cost = store.llm_usage(since=datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc))
    b = config.budgets
    print(f"LLM enabled: {settings.llm_enabled}  endpoint: {settings.llm_base_url}")
    print(f"Models (triage): {', '.join(config.llm.triage_models) or 'none'}")
    print(f"Today: {day_calls}/{b.per_day_requests} requests, {day_tokens} tokens, ${day_cost:.4f}/${b.per_day_usd}")
    print(f"Per incident: {b.per_incident_tokens} tokens, ${b.per_incident_usd}")
    if settings.llm_enabled or args.remote:
        status = build_gateway(settings, config, store).key_status()
        if status:
            keep = ("limit", "limit_remaining", "usage", "usage_daily", "is_free_tier", "free_model_daily_requests")
            print("Provider key: " + ", ".join(f"{k}={status.get(k)}" for k in keep if k in status))
    print("Recent calls:")
    for c in store.list_llm_calls(args.limit):
        state = "ok" if c.success else f"ERR {c.error[:80] if c.error else ''}"
        print(f"  {c.ts:%m-%d %H:%M:%S} inc={c.incident_id} {c.purpose:<7} {c.model:<40} "
              f"{c.prompt_tokens}+{c.completion_tokens} tok ${c.cost_usd:.4f} {c.latency_ms}ms redactions={c.redactions} {state}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    for row in StateStore(Settings().database_url).list_audit(args.limit):
        print(f"  {row.ts:%Y-%m-%d %H:%M:%S}  {row.actor:<8} {row.action:<20} {row.target}  {row.details}")
    return 0


def _cmd_fixes(args: argparse.Namespace) -> int:
    store = StateStore(Settings().database_url)
    cfg = load_agent_config(Settings().config_dir).fix
    for f in store.list_fixes():
        target = cfg.promotion[min(f.stage, len(cfg.promotion) - 1)]
        where = f"-> {target}" if f.state in ("pr_open", "deploying") else ""
        print(f"  fix #{f.id:<3} incident #{f.incident_id:<4} {f.state:<10} {where:<9} {f.branch or ''}")
        if args.verbose_history:
            for h in f.history or []:
                print(f"        {h['ts'][:19]}  {h['state']:<10} {h.get('note', '')}")
        for branch, url in (f.prs or {}).items():
            print(f"        PR -> {branch}: {url}")
        if f.error:
            print(f"        error: {f.error[:300]}")
    return 0


def _cmd_fix(args: argparse.Namespace) -> int:
    settings = Settings()
    if not (settings.fix_enabled and settings.llm_enabled):
        print("Set AGENT_LLM_ENABLED=true and AGENT_FIX_ENABLED=true in .env")
        return 1
    monitor = build_monitor(settings)
    inc = monitor.store.get_incident(args.incident_id)
    if inc is None:
        print(f"No incident #{args.incident_id}")
        return 1
    if inc.diagnosis is None:
        print("Incident has no diagnosis yet; running triage first")
        monitor.run_triage(inc.id, inc.error_message)
    fix = monitor.fixer.start(inc.id)
    if fix is None:
        print("Deferred: another fix is in flight (fix.max_concurrent)")
        return 1
    print(f"fix #{fix.id} {fix.state}; advancing one step now (writing the patch can take a minute)...")
    for fix_id, before, after in monitor.fixer.advance_all():
        print(f"  fix #{fix_id}: {before} -> {after}")
    return 0


def _cmd_github(args: argparse.Namespace) -> int:
    settings = Settings()
    config = load_agent_config(settings.config_dir)
    gh = build_github(settings, config)
    login = gh.whoami()
    print(f"Token user: {login}   repo: {config.fix.repo}")
    for branch in config.fix.promotion:
        print(f"  {branch:<5} {gh.branch_sha(branch)[:7]}")
    return 0


def _cmd_console(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print('Install the console extra: pip install -e ".[console]"')
        return 1
    from .api.app import create_app, find_dist

    settings = Settings()
    host = args.host or settings.console_host
    port = args.port or settings.console_port
    if not (find_dist(settings.console_dist) / "index.html").exists():
        print("Frontend not built (frontend/dist missing): API only. Build with: cd frontend; npm install; npm run build")
    if settings.console_auth == "demo":
        print("Console auth: DEMO mode (any name + role). Not for production; use AGENT_CONSOLE_AUTH=oidc.")
    print(f"Ops Console: http://{host}:{port}   API docs: http://{host}:{port}/api/docs")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline-agent", description="Pipeline production support agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="poll continuously").set_defaults(func=_cmd_run)
    sub.add_parser("once", help="run a single poll and exit").set_defaults(func=_cmd_once)
    sub.add_parser("check", help="validate config and test every platform connection").set_defaults(func=_cmd_check)
    sub.add_parser("plugins", help="list installed plugins").set_defaults(func=_cmd_plugins)
    p_inc = sub.add_parser("incidents", help="list incidents")
    p_inc.add_argument("--status", choices=["open", "escalated", "resolved"])
    p_inc.add_argument("--show", type=int, metavar="ID", help="show one incident with its diagnosis")
    p_inc.set_defaults(func=_cmd_incidents)
    p_tri = sub.add_parser("triage", help="(re)run LLM diagnosis for an incident")
    p_tri.add_argument("incident_id", type=int)
    p_tri.set_defaults(func=_cmd_triage)
    p_llm = sub.add_parser("llm", help="LLM budgets, usage and recent calls")
    p_llm.add_argument("--limit", type=int, default=10)
    p_llm.add_argument("--remote", action="store_true", help="query provider key status even if LLM is disabled")
    p_llm.set_defaults(func=_cmd_llm)
    p_fixes = sub.add_parser("fixes", help="list automated fix attempts")
    p_fixes.add_argument("--history", dest="verbose_history", action="store_true")
    p_fixes.set_defaults(func=_cmd_fixes)
    p_fix = sub.add_parser("fix", help="start the fix flow for an incident now")
    p_fix.add_argument("incident_id", type=int)
    p_fix.set_defaults(func=_cmd_fix)
    sub.add_parser("github", help="check the agent's GitHub token and branches").set_defaults(func=_cmd_github)
    p_console = sub.add_parser("console", help="start the Ops Console (web UI + API)")
    p_console.add_argument("--host")
    p_console.add_argument("--port", type=int)
    p_console.set_defaults(func=_cmd_console)
    p_audit = sub.add_parser("audit", help="show the audit log")
    p_audit.add_argument("--limit", type=int, default=50)
    p_audit.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    # .env feeds env:// secret references (e.g. DATABRICKS_TOKEN); real env vars win.
    load_dotenv(".env", override=False)
    use_system_trust_store()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
