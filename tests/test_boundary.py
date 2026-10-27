"""Trust boundary: signatures, expiry, idempotent submit, claim, ledger."""

from __future__ import annotations

from datetime import timedelta

import pytest

from pipeline_agent.boundary import ActionQueue, ActionRequest, ActionResult, Approval, BoundaryError, CapabilityId, SignedActionRequest
from pipeline_agent.data_plane.ledger import ExecutionLedger
from pipeline_agent.leases import Leases
from pipeline_agent.sdk.models import utcnow
from pipeline_agent.security.signing import Signer, Verifier


def make_request(**over) -> ActionRequest:
    fields = dict(
        idempotency_key="incident-1:rerun:1", capability=CapabilityId.RERUN, platform_id="dbx", env="prod",
        target={"pipeline_id": "42", "run_id": "7"}, agent="recovery", incident_id=1, level=4, policy_bundle="abc",
        approval=Approval(kind="policy", ref="transient_rerun_prod"),
    )
    fields.update(over)
    return ActionRequest.new(**fields)


@pytest.fixture
def signer():
    return Signer.generate()


def test_signed_request_verifies_and_round_trips(signer):
    req = make_request()
    signed = SignedActionRequest.sign(req, signer)
    back = signed.verify(Verifier([signer.public_key]))
    assert back == req


@pytest.mark.parametrize(
    "tamper",
    [
        lambda p: p.update(env="dev"),
        lambda p: p["target"].update(run_id="8"),
        lambda p: p.update(level=5),
        lambda p: p.update(expires_at="2099-01-01T00:00:00Z"),
    ],
    ids=["env", "target", "level", "expiry"],
)
def test_tampered_request_is_rejected(signer, tamper):
    signed = SignedActionRequest.sign(make_request(), signer)
    tamper(signed.payload)
    with pytest.raises(BoundaryError, match="signature"):
        signed.verify(Verifier([signer.public_key]))


def test_unknown_key_and_expired_request_are_rejected(signer):
    signed = SignedActionRequest.sign(make_request(), signer)
    with pytest.raises(BoundaryError, match="unknown signing key"):
        signed.verify(Verifier([Signer.generate().public_key]))
    old = SignedActionRequest.sign(make_request(issued_at=utcnow() - timedelta(hours=2)), signer)
    with pytest.raises(BoundaryError, match="expired"):
        old.verify(Verifier([signer.public_key]))


def test_keys_written_to_disk_load_back(tmp_path, signer):
    private, public = signer.write(tmp_path)
    again = Signer.from_file(private)
    assert again.key_id == signer.key_id
    signed = SignedActionRequest.sign(make_request(), again)
    signed.verify(Verifier.from_files([public]))


def test_submit_is_idempotent_on_the_key(store, signer):
    queue = ActionQueue(store)
    first_id, created = queue.submit(SignedActionRequest.sign(make_request(), signer))
    replay_id, created_again = queue.submit(SignedActionRequest.sign(make_request(), signer))  # new id, same key
    assert created and not created_again
    assert replay_id == first_id
    assert len(queue.pending()) == 1


def test_claim_once_then_complete(store, signer):
    queue = ActionQueue(store)
    request_id, _ = queue.submit(SignedActionRequest.sign(make_request(), signer))
    claimed = queue.claim_next("worker-a")
    assert claimed is not None and claimed.payload["id"] == request_id
    assert queue.claim_next("worker-b") is None
    claimed.verify(Verifier([signer.public_key]))  # the stored payload still verifies
    queue.complete(ActionResult(request_id=request_id, status="success", output={"new_run_id": "9"}))
    assert queue.result(request_id).output == {"new_run_id": "9"}
    assert queue.get(request_id).status == "done"
    assert queue.pending() == []


def test_ledger_executes_a_key_once(store):
    ledger = ExecutionLedger(store)
    first = ledger.reserve("incident-1:rerun:1", "r1", "pipeline.rerun", "dbx:42:7")
    assert first.created
    ledger.finish("incident-1:rerun:1", "SUCCESS", {"new_run_id": "9"})
    replay = ledger.reserve("incident-1:rerun:1", "r2", "pipeline.rerun", "dbx:42:7")
    assert not replay.created and replay.state == "SUCCESS" and replay.result == {"new_run_id": "9"}
    with pytest.raises(ValueError):
        ledger.finish("incident-1:rerun:1", "FAILED")


def test_ledger_marks_a_crashed_execution_unknown(store):
    ledger = ExecutionLedger(store)
    now = utcnow()
    ledger.reserve("incident-2:repair:1", "r1", "pipeline.repair", "dbx:42:8", now=now - timedelta(minutes=30))
    assert ledger.sweep_stale(timedelta(minutes=10), now=now) == ["incident-2:repair:1"]
    again = ledger.reserve("incident-2:repair:1", "r2", "pipeline.repair", "dbx:42:8")
    assert not again.created and again.state == "UNKNOWN"  # never retried automatically


def test_lease_has_one_holder_until_it_expires(store):
    leases = Leases(store)
    now = utcnow()
    assert leases.acquire("graph-runner", "a", timedelta(seconds=60), now=now)
    assert not leases.acquire("graph-runner", "b", timedelta(seconds=60), now=now + timedelta(seconds=30))
    assert leases.acquire("graph-runner", "a", timedelta(seconds=60), now=now + timedelta(seconds=30))  # renew
    assert leases.acquire("graph-runner", "b", timedelta(seconds=60), now=now + timedelta(seconds=120))  # expired
    assert leases.holder("graph-runner", now=now + timedelta(seconds=121)) == "b"
