"""Contract test kit every plugin must pass.

Usage in a plugin's test module::

    from pipeline_agent.testing import PluginContractSuite

    class TestMyPluginContract(PluginContractSuite):
        @pytest.fixture
        def plugin(self):
            return MyPlugin()

        @pytest.fixture
        def instance(self):
            return <a PlatformInstance backed by recorded/mocked API responses>

        @pytest.fixture
        def failed_run(self, instance):      # optional, needed when LOGS is supported
            return <a Run with status FAILED>

The suite has no ``Test`` prefix, so pytest only collects it when subclassed.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from .. import PLUGIN_API_VERSION
from ..sdk.capabilities import Capability
from ..sdk.models import ErrorClass, FailureContext, Pipeline, Run, RunRef, RunStatus, utcnow
from ..sdk.plugin import PlatformInstance, PlatformPlugin


class PluginContractSuite:
    # Secret values used by the fixture instance. The suite checks none leak.
    secret_values: tuple[str, ...] = ()

    @pytest.fixture
    def failed_run(self, instance: PlatformInstance) -> Run | None:
        return None

    # manifest ------------------------------------------------------------
    def test_manifest_is_valid(self, plugin: PlatformPlugin) -> None:
        m = plugin.manifest
        assert m.name and m.version and m.display_name
        assert Capability.MONITOR in m.capabilities
        assert m.plugin_api_version.split(".")[0] == PLUGIN_API_VERSION.split(".")[0]
        json.dumps(m.config_schema)  # the console renders forms from this

    def test_config_schema_matches_config_model(self, plugin: PlatformPlugin) -> None:
        assert plugin.manifest.config_schema == plugin.config_model.model_json_schema()

    def test_instance_capabilities_are_declared(self, plugin: PlatformPlugin, instance: PlatformInstance) -> None:
        assert instance.capabilities <= plugin.manifest.capabilities

    # monitor -------------------------------------------------------------
    def test_list_pipelines(self, instance: PlatformInstance) -> None:
        pipelines = instance.run_source.list_pipelines()
        assert all(isinstance(p, Pipeline) for p in pipelines)
        assert all(p.platform_id == instance.instance_id for p in pipelines)
        ids = [p.pipeline_id for p in pipelines]
        assert len(ids) == len(set(ids)), "pipeline ids must be unique"

    def test_list_runs(self, instance: PlatformInstance) -> None:
        runs = instance.run_source.list_runs(since=utcnow() - timedelta(days=1))
        assert all(isinstance(r, Run) for r in runs)
        for r in runs:
            assert r.platform_id == instance.instance_id
            assert isinstance(r.status, RunStatus)
            if r.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):
                assert r.end_time is not None, f"terminal run {r.run_id} needs end_time"
            if r.start_time and r.end_time:
                assert r.end_time >= r.start_time
            assert r.start_time is None or r.start_time.tzinfo is not None, "times must be timezone-aware (UTC)"

    def test_active_only_returns_active_runs(self, instance: PlatformInstance) -> None:
        runs = instance.run_source.list_runs(since=utcnow() - timedelta(days=1), active_only=True)
        assert all(r.status.is_active for r in runs)

    # control -------------------------------------------------------------
    def test_rerun_returns_ref(self, instance: PlatformInstance) -> None:
        if not instance.supports(Capability.RERUN):
            pytest.skip("plugin has no RERUN capability")
        pipelines = instance.run_source.list_pipelines()
        if not pipelines:
            pytest.skip("fixture instance has no pipelines")
        ref = instance.controller.rerun(pipelines[0])
        assert isinstance(ref, RunRef) and ref.run_id
        assert ref.pipeline_id == pipelines[0].pipeline_id

    # logs ----------------------------------------------------------------
    def test_failure_context(self, instance: PlatformInstance, failed_run: Run | None) -> None:
        if not instance.supports(Capability.LOGS):
            pytest.skip("plugin has no LOGS capability")
        if failed_run is None:
            pytest.fail("plugins with LOGS must provide a failed_run fixture")
        ctx = instance.logs.failure_context(failed_run)
        assert isinstance(ctx, FailureContext)
        assert isinstance(ctx.error_class, ErrorClass)
        assert ctx.full_text(), "a failure context must carry some error text"

    # security ------------------------------------------------------------
    def test_no_secret_in_repr_or_manifest(self, plugin: PlatformPlugin, instance: PlatformInstance) -> None:
        exposed = repr(instance) + plugin.manifest.model_dump_json()
        for value in self.secret_values:
            assert value not in exposed
