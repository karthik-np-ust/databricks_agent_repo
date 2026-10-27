"""Stable capability ids shared by the control plane (policy, agents) and the data plane (router, adapters).

A capability is what an agent may *ask for*; the data plane maps it to a platform adapter.
Descriptors carry the platform-neutral semantics the policy and executor rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityId(StrEnum):
    GET_RUN = "pipeline.get_run"
    CANCEL = "pipeline.cancel"
    RERUN = "pipeline.rerun"
    REPAIR = "pipeline.repair"  # re-run only the failed tasks of a run
    SMOKE_RUN = "pipeline.smoke_run"  # run a job in a non-prod env after a code change
    CODE_MERGE = "code.merge"  # merge a fix PR into an environment branch (GitHub, control plane)
    CODE_SYNC = "code.sync"  # pull an environment's Git folder to its branch head
    TESTS_RUN = "tests.run"  # run test notebooks from a branch, one-time
    DATA_PROFILE = "data.profile"  # read-only counts / nulls / duplicate keys


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: CapabilityId
    description: str
    side_effects: bool  # False: read-only, never changes platform state
    risk: str  # low | medium | high
    preconditions: tuple[str, ...] = ()


CAPABILITIES: dict[CapabilityId, CapabilityDescriptor] = {
    d.id: d
    for d in [
        CapabilityDescriptor(CapabilityId.GET_RUN, "Read a run's state and failure details", False, "low"),
        CapabilityDescriptor(CapabilityId.CANCEL, "Cancel an active run", True, "medium", ("run_active",)),
        CapabilityDescriptor(
            CapabilityId.RERUN, "Start a new run of the pipeline", True, "medium", ("run_terminal", "no_newer_success", "definition_unchanged")
        ),
        CapabilityDescriptor(
            CapabilityId.REPAIR, "Re-run only the failed tasks of a run", True, "medium", ("run_failed", "no_newer_success", "definition_unchanged")
        ),
        CapabilityDescriptor(CapabilityId.SMOKE_RUN, "Run a non-prod job to prove a change", True, "low", ("non_prod_env",)),
        CapabilityDescriptor(CapabilityId.CODE_MERGE, "Merge a fix PR into an environment branch", True, "high", ("checks_green",)),
        CapabilityDescriptor(CapabilityId.CODE_SYNC, "Update an environment's Git folder to its branch head", True, "high", ("branch_head_known",)),
        CapabilityDescriptor(CapabilityId.TESTS_RUN, "Run test notebooks from a branch in isolated schemas", True, "low"),
        CapabilityDescriptor(CapabilityId.DATA_PROFILE, "Row counts, null and duplicate-key checks", False, "low"),
    ]
}
