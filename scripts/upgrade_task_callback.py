"""Zero-input callback settlement by the original enrolled campaign actor.

The pinned controller still validates its own pack, report, attempt and native
EOF fence. This adapter supplies explicit task authority instead of borrowing
the display TUI's foreground identity or CODEX_THREAD_ID. It never sends input.
"""
import fcntl
import os
from pathlib import Path
import stat

from orchestration_common import file_ref, json_ref, require
from orchestration_native import NativeReader, delivery_identity
from upgrade_task_control import native_invocation

SCHEMA = "upgrade-task-callback-receiver-v1"


def _callback_operation(payload):
    return (payload.get("action") in ("callback", "candidate")
            or payload.get("action") == "failure-candidate" and payload.get("boundary") == "callback_boundary")


def _pack_path(campaign, payload):
    return (Path(campaign["root"]) / "runs" / payload["run_id"] / "nodes"
            / payload["node_id"] / "preflight/task-pack.json")


def _context(campaign, pack_path):
    from upgrade_task_enrollment import _active, TaskCampaignGate
    from orchestration_binding import claim_lane
    context = _active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,
            "TASK_CALLBACK_LIVE_ACTOR_REQUIRED")
    owner, message, record = context
    require(owner.campaign == campaign, "TASK_CALLBACK_FOREIGN_CAMPAIGN")
    owner(owner.registration, message, record)
    require(message.get("method") == "item/tool/call", "TASK_CALLBACK_ORIGINAL_CALL_REQUIRED")
    operation = message["params"]["arguments"]["operation_id"]
    payload = owner.operations[operation]
    require(_callback_operation(payload) and Path(pack_path) == _pack_path(campaign, payload),
            "TASK_CALLBACK_OPERATION_SCOPE_CHANGED")
    row = owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"] == "INTENDED" and row["request"] == message,
            "TASK_CALLBACK_ORIGINAL_INTENT_REQUIRED")
    invocation = file_ref(Path(campaign["task_controls"]["evidence_directory"]) / (operation + ".json"))
    evidence = json_ref(invocation)
    require(evidence["received"] == record, "TASK_CALLBACK_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"]), invocation,
                      evidence["request"], allow_intended=True)
    co = owner.runner.co(payload["run_id"])
    co._gate()
    _, data = co.state()
    info = data[payload["node_id"]]
    require(info.get("mode") == "delegated_cli" and "delivery" in info
            and info.get("prepared", {}).get("pack") == file_ref(pack_path),
            "TASK_CALLBACK_ORIGINAL_DISPATCH_REQUIRED")
    lane = claim_lane(co.nodes[payload["node_id"]], info)
    sender, receiver = campaign["sessions"][lane], campaign["sessions"]["codex"]
    require(sender["provider"] == "claude", "TASK_CALLBACK_ORIGINAL_CLAUDE_REQUIRED")
    proof = dict(schema=SCHEMA, enrollment=owner.proof, received=record,
                 invocation=invocation, pack=info["prepared"]["pack"])
    return owner, proof, sender, receiver


def verify_receiver(receipt, receiver, sender):
    """Historical provenance check; reading a receipt never enrolls an actor."""
    proof = receipt.get("receiver_task_actor")
    require(isinstance(proof, dict) and set(proof) ==
            {"schema", "enrollment", "received", "invocation", "pack"} and proof["schema"] == SCHEMA,
            "TASK_CALLBACK_RECEIVER_PROOF_REQUIRED")
    require(receipt.get("receiver_authentication") == SCHEMA
            and receipt.get("reconciled_read_only") is True and receipt.get("input_operations") == 0,
            "TASK_CALLBACK_RECEIVER_CONTRACT_CHANGED")
    enrollment = json_ref(proof["enrollment"])
    require(enrollment.get("schema") == "upgrade-task-campaign-enrollment-v1",
            "TASK_CALLBACK_ORIGINAL_ENROLLMENT_REQUIRED")
    campaign = json_ref(enrollment["campaign"])
    controls = campaign["task_controls"]
    require(proof["enrollment"]["path"] == str(Path(controls["registration"]["path"]).parent / "campaign-enrollment.json")
            and enrollment["registration"] == controls["registration"]
            and enrollment["authority"] == campaign["authorization"]
            and enrollment["runtime"] == campaign["runtime"]
            and enrollment["adoption"] == campaign["client_isolation"]
            and receiver == campaign["sessions"]["codex"]
            and receiver.get("private_native") == dict(runtime=campaign["runtime"], adoption=campaign["client_isolation"]),
            "TASK_CALLBACK_ENROLLED_RECEIVER_CHANGED")
    invocation = json_ref(proof["invocation"])
    payload = invocation["operation"]["payload"]
    require(_callback_operation(payload) and invocation["received"] == proof["received"]
            and invocation["request"]["registration"] == controls["registration"]
            and invocation["ledger"] == enrollment["ledger"]
            and invocation["request"]["evidence_directory"] == controls["evidence_directory"]
            and payload["campaign_id"] == campaign["campaign_id"] and payload["campaign_root"] == campaign["root"]
            and proof["pack"]["path"] == str(_pack_path(campaign, payload)),
            "TASK_CALLBACK_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(receiver), proof["invocation"], invocation["request"], allow_intended=True)
    pack = json_ref(proof["pack"])
    require(proof["pack"]["sha256"] == receipt["task_pack_sha256"]
            and pack["task_id"] == receipt["task_id"]
            and pack["completion_callback"] == receipt["completion_callback"]
            and sender in campaign["sessions"].values() and sender["provider"] == "claude"
            and sender["surface_uuid"].upper() == pack["executor_uuid"].upper()
            and receipt["receiver_identity"] == delivery_identity(sender, receiver),
            "TASK_CALLBACK_ORIGINAL_PARTICIPANTS_CHANGED")


def reconcile(campaign, pack_path, bridge, callback, receipts, availability, *, transcript, line):
    """Publish under the original sender's lock, without calling its sender."""
    owner, proof, sender, receiver = _context(campaign, pack_path)
    require(str(transcript) == receiver["transcript"]["path"], "TASK_CALLBACK_ORIGINAL_JOURNAL_REQUIRED")
    pack = bridge.validate_task_pack_contract(Path(pack_path))
    availability.require_action(pack["task_id"], "callback", pack)
    receipt = Path(pack["completion_receipt"])
    journal = receipt.with_name(receipt.stem + "-attempts")
    require(not Path(str(receipt) + ".pending.json").exists(), "TASK_CALLBACK_LEGACY_PENDING_REFUSED")
    lock_path = journal / "delivery.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def check_lock():
            held, current = os.fstat(fd), lock_path.lstat()
            require(stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                    and (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino),
                    "TASK_CALLBACK_ORIGINAL_LOCK_CHANGED")
        check_lock()
        require(not receipt.exists(), "TASK_CALLBACK_RECEIPT_ALREADY_EXISTS")
        attempts = sorted(journal.glob("attempt-*.json"))
        require(attempts, "TASK_CALLBACK_ORIGINAL_ATTEMPT_REQUIRED")
        attempt = json_ref(file_ref(attempts[-1]))
        bound = attempt["native_binding"]
        require(bound.get("schema") == "native-delivery-task-v1", "TASK_CALLBACK_EXPLICIT_TARGET_REQUIRED")
        from orchestration_native import check_native_binding
        check_native_binding(bound, receiver, sender, pack["completion_callback"], uuid_semantics=True)
        evidence = callback.validate(pack_path, attempts[-1], transcript, line, receiver["session_id"])
        value = {k: v for k, v in attempt["binding"].items() if k != "identity"}
        value.update(confirmed=True, confirmation_source="native_user_message_v1", reconciled_read_only=True,
            attempt=str(attempts[-1]), native_evidence=evidence, receiver_identity=delivery_identity(sender, receiver),
            receiver_authentication=SCHEMA, receiver_task_actor=proof, input_operations=0,
            native_binding=bound, native_proof=evidence["native_proof"])
        verify_receiver(value, receiver, sender)
        # Recheck actual owner, original peer, all lanes, STOP and exact evidence
        # while holding the same lock. No foreground-shaped identity is supplied.
        require(_context(campaign, pack_path) == (owner, proof, sender, receiver),
                "TASK_CALLBACK_LIVE_SCOPE_CHANGED")
        require(callback.validate(pack_path, attempts[-1], transcript, line, receiver["session_id"]) == evidence
                and sorted(journal.glob("attempt-*.json")) == attempts,
                "TASK_CALLBACK_ORIGINAL_EVIDENCE_CHANGED")
        availability.require_action(pack["task_id"], "callback", pack)
        check_lock()
        receipts.publish(receipt, value)
        return value
    finally:
        os.close(fd)
