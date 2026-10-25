from __future__ import annotations

import pytest

from pipeline_agent.config import AgentConfig, PipelinePolicy, load_agent_config
from pipeline_agent.notify.webhook import Notification, slack_payload, teams_payload
from pipeline_agent.policy import p95
from pipeline_agent.sdk import ErrorClass, ErrorClassifier
from pipeline_agent.sdk.registry import PluginError, check_api_version, discover_plugins, load_plugin
from pipeline_agent.secrets import EnvSecretResolver, SecretError
from pipeline_agent.security import redact


# redaction ------------------------------------------------------------------
@pytest.mark.parametrize(
    "secret,label",
    [
        ("dapiTESTnotARealTokenXXXXXXXXXXXXXX", "DATABRICKS_TOKEN"),
        ("ghp_TESTnotArealTokenXXXXXXXXXXXX", "GITHUB_TOKEN"),
        ("sk-or-v1-TESTnotArealKeyXXXXXX", "OPENROUTER_KEY"),
        ("AKIAABCDEFGHIJKLMNOP", "AWS_KEY"),
        ("someone@example.com", "EMAIL"),
        ("4111 1111 1111 1111", "NUMBER"),
    ],
)
def test_redaction_masks_secrets(secret, label):
    result = redact(f"failed with {secret} in the log")
    assert secret not in result.text
    assert f"[REDACTED:{label}]" in result.text
    assert result.count == 1


def test_redaction_keeps_ordinary_text_and_masks_assignments():
    assert redact("CAST_INVALID_INPUT on column qty").count == 0
    out = redact("password=hunter2 next").text
    assert "hunter2" not in out and out.startswith("password=[REDACTED")


# classification ---------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Connection reset by peer", ErrorClass.TRANSIENT),
        ("HTTP 503 Service Unavailable", ErrorClass.TRANSIENT),
        ("User does not have permission: PERMISSION_DENIED", ErrorClass.INFRA),
        ("ModuleNotFoundError: No module named 'common'", ErrorClass.CODE),
        ("something odd happened", ErrorClass.UNKNOWN),
    ],
)
def test_generic_classifier(text, expected):
    assert ErrorClassifier.with_defaults().classify(text)[0] == expected


# config -----------------------------------------------------------------------
def test_policy_merge_falls_back_to_defaults():
    cfg = AgentConfig(
        defaults=PipelinePolicy(max_duration_seconds=600, max_reruns_per_day=3),
        policies=[PipelinePolicy(match="sales_*", max_duration_seconds=300)],
    )
    p = cfg.policy_for("dbx", "sales_report")
    assert p.max_duration_seconds == 300 and p.max_reruns_per_day == 3
    assert cfg.policy_for("dbx", "orders").max_duration_seconds == 600
    assert cfg.policy_for("dbx", "sales_report").matches("dbx", "sales_report")


def test_repo_config_files_load():
    cfg = load_agent_config(__import__("pathlib").Path(__file__).parents[1] / "config")
    assert cfg.platforms and cfg.policies


# secrets ----------------------------------------------------------------------
def test_secret_resolver():
    r = EnvSecretResolver({"TOKEN": "abc"})
    assert r.get("env://TOKEN") == "abc"
    with pytest.raises(SecretError):
        r.get("env://MISSING")
    with pytest.raises(SecretError):
        r.get("plain-value")
    with pytest.raises(SecretError):
        r.get("kv://name")


# registry ---------------------------------------------------------------------
def test_installed_plugins_are_discovered():
    assert {"simulator", "databricks"} <= set(discover_plugins())
    assert load_plugin("pipeline_agent_simulator:SimulatorPlugin").manifest.name == "simulator"


def test_incompatible_plugin_api_is_refused():
    from pipeline_agent_simulator import SimulatorPlugin

    class Future(SimulatorPlugin):
        manifest = SimulatorPlugin.manifest.model_copy(update={"plugin_api_version": "2.0"})

    with pytest.raises(PluginError):
        check_api_version(Future)


# misc -------------------------------------------------------------------------
def test_p95():
    assert p95([10.0] * 19 + [100.0]) == 10.0
    assert p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]) == 20


def test_notification_payloads_are_redacted():
    n = Notification("Failed", "token dapiTESTnotARealTokenXXXXXXXXXXXXXX", facts={"run": "1"}, url="https://x")
    assert "dapiTEST" not in str(teams_payload(n.redacted()))
    assert "dapiTEST" not in slack_payload(n.redacted())["text"]
