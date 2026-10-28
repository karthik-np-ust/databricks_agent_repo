# 09 · Plugin guide: adding a new ETL tool

A plugin is a small Python package. The core discovers it through an entry point, so nothing in `src/pipeline_agent/` changes. Use [plugins/agent-plugin-simulator](../plugins/agent-plugin-simulator) as the reference implementation.

## 1. Package layout
```
plugins/agent-plugin-<tool>/
├─ pyproject.toml
├─ src/pipeline_agent_<tool>/
│  ├─ __init__.py
│  ├─ plugin.py
│  └─ knowledge/errors.yaml
└─ tests/test_<tool>_contract.py
```

`pyproject.toml`:
```toml
[project]
name = "agent-plugin-<tool>"
dependencies = ["pipeline-agent>=0.1", "<tool-sdk>"]

[project.entry-points."pipeline_agent.plugins"]
<tool> = "pipeline_agent_<tool>.plugin:<Tool>Plugin"

[tool.setuptools.package-data]
pipeline_agent_<tool> = ["knowledge/*.yaml"]
```

## 2. Implement only the capabilities your tool supports
```python
from pipeline_agent import PLUGIN_API_VERSION
from pipeline_agent.sdk import (Capability, PlatformInstance, PlatformPlugin, PluginManifest,
                                Pipeline, Run, RunRef, RunStatus, Env, FailureContext)

class ToolConfig(BaseModel):          # validated config; its JSON Schema drives the console form
    account: str
    warehouse: str | None = None

class ToolBackend:
    def __init__(self, instance_id, cfg, client): ...
    # MONITOR (required)
    def list_pipelines(self) -> list[Pipeline]: ...
    def list_runs(self, since, active_only=False) -> list[Run]: ...   # active + ended >= since
    # CANCEL / RERUN (optional)
    def cancel(self, run): ...
    def rerun(self, pipeline, params=None) -> RunRef: ...
    # LOGS (optional)
    def failure_context(self, run) -> FailureContext: ...

class ToolPlugin(PlatformPlugin):
    config_model = ToolConfig
    manifest = PluginManifest(
        name="<tool>", version="0.1.0", plugin_api_version=PLUGIN_API_VERSION,
        display_name="<Tool>", capabilities={Capability.MONITOR, Capability.RERUN, Capability.LOGS},
        config_schema=ToolConfig.model_json_schema(), secrets=["password"],
    )
    def build(self, instance_id, config, secrets, client=None) -> PlatformInstance:
        cfg = ToolConfig.model_validate(config)
        backend = ToolBackend(instance_id, cfg, client or make_client(cfg, secrets))
        return PlatformInstance(instance_id, self.manifest, run_source=backend,
                                controller=backend, logs=backend, error_rules=load_my_rules())
```

**Rules every plugin must follow** (the contract suite checks most of them):
- All datetimes are timezone-aware UTC.
- Terminal runs (succeeded/failed) have `end_time`.
- `list_runs(active_only=True)` returns only queued/running runs.
- Map native statuses into `RunStatus`. Anything ambiguous becomes `UNKNOWN`, never a guess.
- Set `env` from the tool's naming or tagging convention (see `env_prefixes` in the Databricks plugin).
- Never put secret values in `repr`, the manifest, exceptions or `raw`.
- Accept an injectable client in `build(...)` so tests can use fakes.
- Keep `raw` small: only the native fields that are useful for debugging.

## 2b. Data-plane actions (`ActionProvider`, plugin API 1.1, optional)
Implement these to let the data plane repair runs, sync code, run tests and profile tables for your tool. Then pass `actions=backend` to `PlatformInstance` and add `Capability.ACTIONS` to the manifest:

| Method | Capability it serves | Contract |
|---|---|---|
| `get_run(run_id) -> Run` | `pipeline.get_run`, preconditions, watches | Raise if unknown |
| `repair(run) -> {"run_id", ...}` | `pipeline.repair` | Re-run only the failed parts; return as soon as accepted (the observer watches the run) |
| `sync_code(env) -> {"head", ...}` | `code.sync` | Update the env's checked-out code to its branch head; `head` must be the commit now deployed |
| `run_tests(branch, notebooks, params) -> {"run_id", ...}` | `tests.run` | One-time run from `branch`; return as soon as submitted |
| `profile(table, keys, not_null) -> {"rows", "duplicate_keys", "nulls"}` | `data.profile` | Read-only; validate identifiers |
| `definition_hash(pipeline_id) -> str` | `definition_unchanged` precondition | Stable fingerprint of the job definition |

Without `ActionProvider` the tool still gets monitoring, triage, reruns (via `RunController`) and escalation. Code fixes and reconciliation checks need it.

## 3. Knowledge pack (`knowledge/errors.yaml`)
Map the tool's error codes to `transient`, `code`, `infra` or `unknown`. Plugin rules run before the generic rules, and the first match wins.
```yaml
rules:
  - name: object_does_not_exist
    pattern: "002003|does not exist or not authorized"
    class: infra
  - name: numeric_value_not_recognized
    pattern: "100038|Numeric value .* is not recognized"
    class: code
```
From Phase 3 the pack also holds fix playbooks, allowed code paths and the CI deploy template.

## 4. Contract tests
```python
from pipeline_agent.testing import PluginContractSuite

class TestToolContract(PluginContractSuite):
    secret_values = ("test-password",)

    @pytest.fixture
    def plugin(self): return ToolPlugin()

    @pytest.fixture
    def instance(self, plugin): return plugin.build("t", CONFIG, {"password": "test-password"}, client=FakeClient())

    @pytest.fixture
    def failed_run(self, instance):   # required when LOGS is supported
        return next(r for r in instance.run_source.list_runs(SINCE) if r.status == RunStatus.FAILED)
```
Run it with `pytest plugins/agent-plugin-<tool>`. Then install the plugin (`pip install -e plugins/agent-plugin-<tool>`), add an instance to `config/platforms.yaml` and run `pipeline-agent check`.

## 5. Effort guide
| Scope | Typical effort |
|---|---|
| Monitor + alerts via OpenLineage or the generic REST plugin (Phase 6) | hours |
| Full plugin: monitor, rerun, logs, cost | 2–4 days |
| Auto-fix: CODE, knowledge pack, CI template | another 3–5 days |

## Snowflake sketch (Phase 6)
- **MONITOR:** `SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY` (up to 45 min latency) or `INFORMATION_SCHEMA.TASK_HISTORY()` (real time). Task = pipeline, `QUERY_ID`/`RUN_ID` = run.
- **CANCEL / RERUN:** `SELECT SYSTEM$CANCEL_QUERY(<query_id>)`, `EXECUTE TASK <name>`.
- **LOGS:** `ERROR_CODE`, `ERROR_MESSAGE` from task history.
- **COST:** `WAREHOUSE_METERING_HISTORY` (credits).
- **Env mapping:** database or schema per env (`DEV_`, `QA_`, `PROD_`), or task name prefixes.
