"""Read-only lifecycle/native observations; missing measurements stay missing.

Native timestamps and operation timestamps are wall observations, not monotonic
durations. Reading a report here does not prove that a model read it. This module
never runs a checker, settles a callback, constructs transport or writes state.
"""
from datetime import datetime, timezone

from orchestration_campaign import control_script, native_control
from orchestration_common import file_ref, json_ref, parse_time
from orchestration_coordinator import State
from orchestration_evidence import (marker_for, original_callback_evidence, verify_dispatched,
                                    verify_prepared, verify_protocol)
from orchestration_native import NativeReader, require


BOUNDARIES = ("dependencies_ready", "claimed", "handshake_received", "acknowledgement",
              "pack_finalized", "dispatch_intent", "task_input_intent", "task_received", "body_read",
              "first_successful_action", "last_completed_action", "report_frozen", "report_hash_bound",
              "report_discovered", "report_read", "callback_input_intent", "callback_received",
              "candidate_ready", "acceptance_native", "accepted_recorded", "lease_released",
              "disarmed", "cleanup_complete")
INTERVALS = (("dependencies_ready", "claimed"), ("handshake_received", "acknowledgement"),
             ("acknowledgement", "pack_finalized"), ("pack_finalized", "task_input_intent"),
             ("task_input_intent", "task_received"), ("task_received", "first_successful_action"),
             ("first_successful_action", "last_completed_action"), ("last_completed_action", "callback_received"),
             ("callback_input_intent", "callback_received"), ("callback_received", "candidate_ready"),
             ("candidate_ready", "acceptance_native"), ("accepted_recorded", "lease_released"),
             ("lease_released", "cleanup_complete"))


def boundary(at, kind, *evidence):
    return dict(at=parse_time(at).isoformat(), kind=kind, evidence=list(evidence))


def wall_interval(start, end):
    value = dict(start_at=start["at"] if start else None, end_at=end["at"] if end else None,
                 recorded_wall_seconds=None, monotonic_seconds=None, clock_status="missing_endpoint")
    if start and end:
        seconds = (parse_time(end["at"]) - parse_time(start["at"])).total_seconds()
        value["clock_status"] = "recorded_wall_only" if seconds >= 0 else "wall_clock_discontinuity_unmeasured"
        if seconds >= 0:
            value["recorded_wall_seconds"] = seconds
    return value


def _epoch(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _input(observation, bundle):
    event = next(e for e in observation["attempt"]["events"]
                 if e["phase"] in ("PASTE_INTENT", "NATIVE_INPUT_INTENT"))
    return boundary(_epoch(event["at_epoch"]), event["phase"].lower(), bundle["attempts"][-1])


def node_timeline(co, key, info, events, files, lease, snapshots):
    points = dict.fromkeys(BOUNDARIES)
    node, campaign = co.nodes[key], co.campaign
    own = {e["state"]: e for e in events if e["node_id"] == key}

    def event_point(event):
        return boundary(event["at"], "recorded_lifecycle", file_ref(co.root / "events" / f"{event['seq']:06d}.json"))

    dependencies = [e for e in events if e["node_id"] in node["depends_on"] and e["state"] == State.ACCEPTED]
    if not node["depends_on"]:
        points["dependencies_ready"] = boundary(co.header["created_at"], "run_created", file_ref(co.root / "run.json"))
    elif len(dependencies) == len(node["depends_on"]):
        points["dependencies_ready"] = event_point(max(dependencies, key=lambda e: e["seq"]))
    for name, choices in (("claimed", (State.PREPARING, State.LOCAL_RUNNING)),
                          ("candidate_ready", (State.EVIDENCE_READY,)), ("accepted_recorded", (State.ACCEPTED,))):
        event = next((own[s] for s in choices if s in own), None)
        if event:
            points[name] = event_point(event)

    preparation = dispatched = protocol = actions = None
    native_ends = snapshots
    if info:
        # Once acceptance closes a node, later tasks in the same original
        # session are outside its measured native range.
        if "accepted_evidence" in info:
            accepted = json_ref(info["accepted_evidence"])
            native_ends = accepted["native_end"]
            reader = NativeReader(campaign["sessions"]["codex"], fixtures=co.fixtures)
            script = control_script(campaign, co.campaign_ref, "accept", co.spec["run_id"],
                                    node_id=key, marker=marker_for(info, accepted))
            at = native_control(reader, accepted["acceptance_native"], script)
            ref = accepted["acceptance_native"]
            require(ref["offset"] >= accepted["candidate_native_end"]["codex"]["offset"] and
                    ref["offset"] + ref["length"] <= reader.verify_pin(native_ends["codex"]),
                    "observed acceptance outside original candidate/acceptance fences")
            points["acceptance_native"] = boundary(at.isoformat(), "native_control", ref)
        elif "candidate" in info:
            native_ends = json_ref(info["candidate"])["candidate_native_end"]
        for lane, pin in native_ends.items():
            require(NativeReader(campaign["sessions"][lane], fixtures=co.fixtures).verify_pin(pin)
                    <= snapshots[lane]["offset"], "observation predates recorded native boundary")

        if "delivery" in info:
            evidence = original_callback_evidence(campaign, co.header, node, info)
            if evidence:
                protocol = verify_protocol(campaign, co.header, node, info, evidence,
                    native_ends[node["executor"]]["offset"], native_ends["codex"]["offset"], fixtures=co.fixtures)
                dispatched = protocol["delivery"]
                returned, bundle = protocol["callback_delivery"], evidence["protocol"]["callback"]
                points["callback_received"] = boundary(returned["received_at"], "native_user_message", returned["native_proof"])
                points["callback_input_intent"] = _input(returned, bundle)
                points["report_hash_bound"] = boundary(_epoch(returned["attempt"]["native_binding"]["bound_at_epoch"]),
                    "callback_binding_observed", bundle["attempts"][-1], evidence["report"])
            else:
                dispatched = verify_dispatched(campaign, co.header, node, info, info["delivery"], fixtures=co.fixtures)
            preparation = dispatched["preparation"]
            points["task_received"] = boundary(dispatched["received_at"], "native_user_message", dispatched["native_proof"])
            points["task_input_intent"] = _input(dispatched, info["delivery"])
        elif "prepared" in info:
            preparation = verify_prepared(campaign, co.header, node, info, info["prepared"], fixtures=co.fixtures)
        if preparation:
            handshake = preparation["handshake_delivery"]
            points["handshake_received"] = boundary(handshake["received_at"], "native_user_message", handshake["native_proof"])
            points["acknowledgement"] = boundary(preparation["ack_at"].isoformat(), "exact_native_ack", info["prepared"]["handshake"]["ack_native"])
            points["pack_finalized"] = boundary(preparation["pack"]["finalized_at"], "recorded_pack", info["prepared"]["pack"])
            for name in ("handshake_received", "acknowledgement", "task_received"):
                if points[name]:
                    ref = points[name]["evidence"][0]
                    require(ref["offset"] + ref["length"] <= native_ends[node["executor"]]["offset"],
                            "native message after observation fence")
        if "dispatch-intent.json" in files:
            # Bound_record already checked identity and pins. A file published
            # just before its event remains intent, never evidence of input.
            ref = files["dispatch-intent.json"]
            value = json_ref(ref)
            points["dispatch_intent"] = boundary(value["created_at"], "recorded_dispatch_intent", ref)
        if info["mode"] == "local_cli" or dispatched:
            reader = NativeReader(campaign["sessions"][node["executor"]], fixtures=co.fixtures)
            actions = reader.action_summary(reader.verify_pin(info["native_start"]),
                reader.verify_pin(native_ends[node["executor"]]), task_id=info["task_id"],
                output_root=info["output_root"], allow_empty=True, allow_incomplete=True, include_evidence=True)
            completed = [a for a in actions["actions"] if a["status"] != "PENDING"]
            successes = [a for a in completed if a["status"] == "COMPLETED"]
            if successes:
                first = min(successes, key=lambda a: parse_time(a["started_at"]))
                points["first_successful_action"] = boundary(first["started_at"], "completed_task_call_start", first["call"], first["result"])
            if completed:
                last = max(completed, key=lambda a: parse_time(a["completed_at"]))
                points["last_completed_action"] = boundary(last["completed_at"], "task_call_result", last["result"])
        if lease["released_at"]:
            points["lease_released"] = boundary(lease["released_at"], "recorded_lease_release", lease["released_by"])
            if info["mode"] == "local_cli":
                points["cleanup_complete"] = points["lease_released"]
            elif "disarm.json" in files:
                from orchestration_transport import verify_disarm
                ref = verify_disarm(co, key, info, lease["released_by"])
                disarm = json_ref(ref)
                points["disarmed"] = boundary(disarm["proof"]["observed_at"], "verified_reservation_release", ref)
                ends = [points["lease_released"], points["disarmed"]]
                if disarm["operation_result"]:
                    result = json_ref(disarm["operation_result"])
                    ends.append(boundary(result["completed_at"], "recorded_operation_return", disarm["operation_result"]))
                points["cleanup_complete"] = max(ends, key=lambda p: parse_time(p["at"]))
    return dict(schema="orchestration-timeline-v1", evidence_class="recorded_advisory", boundaries=points,
                native_actions=actions, native_observation_ends=native_ends,
                intervals={a + "_to_" + b: wall_interval(points[a], points[b]) for a, b in INTERVALS},
                missing_boundaries=[k for k, v in points.items() if v is None],
                acceptance_revalidated=False, dispatch_authorized=False)
