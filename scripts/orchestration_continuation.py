"""Immutable callback continuation, derived from the original lifecycle.

This is a durable pointer to verified reception, not a second task database or
an input queue. It never wakes a client, resends input or accepts business work.
Consumption is observed from the existing terminal event, without running the
business checker. Publication requires the caller to hold the node runner lock.
"""
from pathlib import Path

from orchestration_common import (checked_path, contract_errors, count, exclusive_lock,
                                  file_ref, json_ref, parse_time, utc_now, write_json)
from orchestration_coordinator import State
from orchestration_evidence import original_callback_evidence, task_identity, verify_protocol
from orchestration_native import NativeReader, require


def continuation_path(co, key):
    require(key in co.nodes, "unknown continuation node")
    return checked_path(co.root / "nodes" / key / "continuation.json")


def event_ref(co, event):
    return file_ref(checked_path(co.root / "events" / f"{event['seq']:06d}.json"))


@contract_errors("callback continuation")
def observe_continuation(co, key, info, events, ref=None):
    """Validate original pins read-only; terminal evidence remains unrevalidated."""
    path = continuation_path(co, key)
    if not path.exists():
        require(ref is None, "original continuation disappeared")
        return None
    ref = file_ref(path) if ref is None else ref
    require(ref["path"] == str(path), "continuation path changed")
    value = json_ref(ref, "original continuation")
    identity = task_identity(co.campaign, co.header, co.nodes[key])
    fields = {"schema", "campaign", "executor_id", "original_sessions", "event_frontier",
              "protocol", "report", "native_end", "callback_native", "callback_received_at", "published_at"}
    require(set(value) == fields | set(identity), "continuation fields changed")
    require(value["schema"] == "orchestration-continuation-v1" and value["campaign"] == co.campaign_ref and
            all(value[k] == v for k, v in identity.items()), "continuation campaign/task/attempt changed")
    lane = co.nodes[key]["executor"]
    require(info.get("mode") == "delegated_cli" and "delivery" in info and value["executor_id"] == lane,
            "continuation requires its original delegated task delivery")
    expected_sessions = {k: co.campaign["sessions"][k] for k in ("codex", lane)}
    require(value["original_sessions"] == expected_sessions, "continuation original sessions changed")
    frontier = value["event_frontier"]
    require(set(frontier) == {"count", "record"}, "continuation event frontier fields changed")
    size = count(frontier["count"], "continuation event count")
    require(0 < size <= len(events), "continuation has no original event frontier")
    last = events[size - 1]
    require(frontier["record"] == event_ref(co, last) and json_ref(frontier["record"]) == last,
            "continuation event-chain frontier changed")
    own = [e for e in events[:size] if e["node_id"] == key]
    require(own and own[-1]["state"] == State.DISPATCHED.value,
            "continuation was not published against original dispatched work")
    require(set(value["native_end"]) == set(co.campaign["sessions"]), "continuation native lanes changed")
    ends = {k: NativeReader(s, fixtures=co.fixtures).verify_pin(value["native_end"][k])
            for k, s in co.campaign["sessions"].items()}
    original = original_callback_evidence(co.campaign, co.header, co.nodes[key], info)
    require(original is not None and value["protocol"] == original["protocol"] and value["report"] == original["report"],
            "continuation differs from its original callback/report")
    proof = verify_protocol(co.campaign, co.header, co.nodes[key], info, value,
                            ends[lane], ends["codex"], fixtures=co.fixtures)
    require(value["callback_native"] == proof["callback_record"] and
            parse_time(value["callback_received_at"]) == proof["callback_at"], "continuation native callback changed")
    require(parse_time(value["published_at"]) >= max(proof["callback_at"], parse_time(last["at"])),
            "continuation publication predates original evidence")
    # A candidate can be published before its lifecycle event. Bind it too, but
    # do not run validate_candidate: that would execute the business checker.
    candidate = checked_path(co.root / "nodes" / key / "candidate.json")
    if candidate.exists():
        frozen = json_ref(file_ref(candidate))
        require(all(frozen.get(k) == v for k, v in identity.items()) and
                frozen.get("protocol") == value["protocol"] and frozen.get("report") == value["report"],
                "candidate differs from its original continuation")
    own = [e for e in events if e["node_id"] == key]
    state = State(own[-1]["state"])
    terminal = own[-1] if state in (State.ACCEPTED, State.FAILED_PRESERVED) else None
    progress = {State.DISPATCHED: "AWAITING_CANDIDATE", State.EVIDENCE_READY: "AWAITING_ACCEPTANCE",
                State.ACCEPTED: "ACCEPTED_RECORDED", State.BLOCKED: "BLOCKED_RECORDED",
                State.FAILED_PRESERVED: "FAILURE_RECORDED"}.get(state)
    require(progress is not None, "continuation has an incompatible current lifecycle")
    return dict(schema="orchestration-continuation-observation-v1", publication=ref,
                callback_native=value["callback_native"], callback_received_at=value["callback_received_at"],
                published_at=value["published_at"], progress=progress,
                consumed_by=event_ref(co, terminal) if terminal else None,
                acceptance_revalidated=False, dispatch_authorized=False, input_operations=0)


def publish_continuation_locked(co, key, info, *, protocol=None):
    """Publish once after native callback verification; never repeat any input.

    Caller holds runner.lock. Optional callback settlement must finish before
    entering this function, preserving runner -> transport -> coordinator order.
    """
    path = continuation_path(co, key)
    with co.state_lock():
        events = co.events()
        if path.exists():
            return observe_continuation(co, key, info, events)
        co._gate()
        own = [e for e in events if e["node_id"] == key]
        require(own and own[-1]["state"] == State.DISPATCHED.value,
                "new continuation requires original dispatched work")
        original = original_callback_evidence(co.campaign, co.header, co.nodes[key], info)
        require(original is not None, "original callback receipt is absent; no continuation or input")
        require(protocol is None or protocol == original["protocol"], "continuation callback bundle changed")
        lane = co.nodes[key]["executor"]
        snapshots = {k: NativeReader(s, fixtures=co.fixtures).snapshot() for k, s in co.campaign["sessions"].items()}
        proof = verify_protocol(co.campaign, co.header, co.nodes[key], info, original,
                                snapshots[lane]["offset"], snapshots["codex"]["offset"], fixtures=co.fixtures)
        value = dict(schema="orchestration-continuation-v1", campaign=co.campaign_ref,
                     **task_identity(co.campaign, co.header, co.nodes[key]), executor_id=lane,
                     original_sessions={k: co.campaign["sessions"][k] for k in ("codex", lane)},
                     event_frontier=dict(count=len(events), record=event_ref(co, events[-1])),
                     **original, native_end=snapshots, callback_native=proof["callback_record"],
                     callback_received_at=proof["callback_at"].isoformat(), published_at=utc_now())
        write_json(path, value, exclusive=True)
        return observe_continuation(co, key, info, events)
