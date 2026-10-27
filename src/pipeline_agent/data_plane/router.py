"""Capability router: stable capability ids -> the configured platform adapter.

Preconditions are checked against *live* platform state right before executing (the control
plane decided on a snapshot that may be minutes old); post-conditions confirm the platform
accepted the action. Neither ever retries anything.
"""

from __future__ import annotations

from typing import Any, Callable

from ..boundary.capabilities import CAPABILITIES, CapabilityId
from ..boundary.models import ActionRequest
from ..sdk.capabilities import Capability
from ..sdk.models import Env, Pipeline, Run, RunStatus
from ..sdk.plugin import PlatformInstance


class RouteError(Exception):
    """The request can't be executed here (unknown platform, missing capability, bad target)."""


class PreconditionFailed(Exception):
    pass


class CapabilityRouter:
    def __init__(self, instances: dict[str, PlatformInstance]):
        self.instances = instances
        self._handlers: dict[CapabilityId, Callable[[PlatformInstance, ActionRequest], dict[str, Any]]] = {
            CapabilityId.GET_RUN: self._get_run,
            CapabilityId.CANCEL: self._cancel,
            CapabilityId.RERUN: self._rerun,
            CapabilityId.REPAIR: self._repair,
            CapabilityId.SMOKE_RUN: self._rerun,
            CapabilityId.CODE_SYNC: self._sync,
            CapabilityId.TESTS_RUN: self._tests,
            CapabilityId.DATA_PROFILE: self._profile,
        }

    # lookups ----------------------------------------------------------------
    def instance(self, req: ActionRequest) -> PlatformInstance:
        inst = self.instances.get(req.platform_id)
        if inst is None:
            raise RouteError(f"unknown platform {req.platform_id!r}")
        if req.capability not in self._handlers:
            raise RouteError(f"{req.capability} is not a data-plane capability")
        needs = {CapabilityId.CANCEL: Capability.CANCEL, CapabilityId.RERUN: Capability.RERUN, CapabilityId.SMOKE_RUN: Capability.RERUN}
        cap = needs.get(req.capability, Capability.ACTIONS)
        if not inst.supports(cap):
            raise RouteError(f"{req.platform_id} ({inst.manifest.name}) does not support {req.capability}")
        return inst

    @staticmethod
    def _target(req: ActionRequest, key: str) -> str:
        value = req.target.get(key)
        if not value:
            raise RouteError(f"{req.capability} needs target.{key}")
        return str(value)

    def _run(self, inst: PlatformInstance, req: ActionRequest) -> Run:
        return inst.actions.get_run(self._target(req, "run_id"))

    def _pipeline(self, inst: PlatformInstance, req: ActionRequest) -> Pipeline:
        pipeline_id = self._target(req, "pipeline_id")
        for p in inst.run_source.list_pipelines():
            if p.pipeline_id == pipeline_id:
                return p
        raise RouteError(f"pipeline {pipeline_id} not found on {req.platform_id}")

    # preconditions (names come from the capability descriptor + the request) ---
    def check_preconditions(self, req: ActionRequest) -> list[str]:
        inst = self.instance(req)
        names = list(dict.fromkeys([*CAPABILITIES[req.capability].preconditions, *req.preconditions]))
        checked = []
        for name in names:
            check = getattr(self, f"_pre_{name}", None)
            if check is None:
                raise PreconditionFailed(f"unknown precondition {name!r}")
            check(inst, req)
            checked.append(name)
        return checked

    def _pre_run_active(self, inst, req):
        run = self._run(inst, req)
        if not run.status.is_active:
            raise PreconditionFailed(f"run {run.run_id} is {run.status}, not active")

    def _pre_run_terminal(self, inst, req):
        if "run_id" not in req.target:
            return  # a plain pipeline rerun has no run to check
        run = self._run(inst, req)
        if not run.status.is_terminal:
            raise PreconditionFailed(f"run {run.run_id} is still {run.status}")

    def _pre_run_failed(self, inst, req):
        run = self._run(inst, req)
        if run.status != RunStatus.FAILED:
            raise PreconditionFailed(f"run {run.run_id} is {run.status}, not failed")

    def _pre_no_newer_success(self, inst, req):
        if "run_id" not in req.target:
            return
        run = self._run(inst, req)
        since = run.start_time or run.end_time
        if since is None:
            return
        for other in inst.run_source.list_runs(since):
            if (
                other.pipeline_id == run.pipeline_id
                and other.run_id != run.run_id
                and other.status == RunStatus.SUCCEEDED
                and other.start_time
                and other.start_time > since
            ):
                raise PreconditionFailed(f"a newer run {other.run_id} already succeeded")

    def _pre_definition_unchanged(self, inst, req):
        expected = req.params.get("definition_hash")
        if not expected or inst.actions is None:
            return  # the control plane didn't pin a definition
        current = inst.actions.definition_hash(self._target(req, "pipeline_id"))
        if current != expected:
            raise PreconditionFailed("pipeline definition changed since the decision")

    def _pre_non_prod_env(self, inst, req):
        if req.env == Env.PROD.value or self._pipeline(inst, req).env == Env.PROD:
            raise PreconditionFailed("smoke runs are for dev/qa only")

    def _pre_branch_head_known(self, inst, req):
        if not req.params.get("expected_head"):
            raise PreconditionFailed("code.sync needs params.expected_head (the merged commit)")

    # handlers ------------------------------------------------------------------
    def execute(self, req: ActionRequest) -> dict[str, Any]:
        return self._handlers[req.capability](self.instance(req), req)

    def _get_run(self, inst, req):
        run = self._run(inst, req)
        return {"run_id": run.run_id, "status": run.status.value, "url": run.url}

    def _cancel(self, inst, req):
        inst.controller.cancel(self._run(inst, req))
        return {"run_id": self._target(req, "run_id"), "cancel_requested": True}

    def _rerun(self, inst, req):
        ref = inst.controller.rerun(self._pipeline(inst, req), {str(k): str(v) for k, v in req.params.get("job_parameters", {}).items()} or None)
        return {"run_id": ref.run_id, "pipeline_id": ref.pipeline_id}

    def _repair(self, inst, req):
        return inst.actions.repair(self._run(inst, req))

    def _sync(self, inst, req):
        return inst.actions.sync_code(req.env)

    def _tests(self, inst, req):
        notebooks = req.params.get("notebooks") or []
        if not notebooks or not req.params.get("branch"):
            raise RouteError("tests.run needs params.branch and params.notebooks")
        return inst.actions.run_tests(req.params["branch"], list(notebooks), {str(k): str(v) for k, v in req.params.get("notebook_params", {}).items()})

    def _profile(self, inst, req):
        return inst.actions.profile(self._target(req, "table"), list(req.params.get("keys", [])), list(req.params.get("not_null", [])))

    # post-conditions -------------------------------------------------------------
    def verify(self, req: ActionRequest, output: dict[str, Any]) -> None:
        """Confirm the platform accepted the action; raises if it can't be confirmed."""
        inst = self.instance(req)
        if req.capability in (CapabilityId.RERUN, CapabilityId.SMOKE_RUN, CapabilityId.REPAIR, CapabilityId.TESTS_RUN):
            run_id = output.get("run_id")
            if not run_id:
                raise PreconditionFailed("platform returned no run id")
            if inst.actions is not None:
                inst.actions.get_run(str(run_id))  # raises if the platform doesn't know the run
        elif req.capability == CapabilityId.CODE_SYNC:
            expected = req.params.get("expected_head", "")
            head = str(output.get("head", ""))
            if expected and not head.startswith(expected[:7]) and not expected.startswith(head[:7] or "-"):
                raise PreconditionFailed(f"Git folder head {head[:8]} is not the merged commit {expected[:8]}")
