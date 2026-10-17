"""Strict reservation observations and bounded original-controller recovery.

The immutable marker is captured before the original disarm intent. Recovery
has at most four numbered slots, at most one action and a mandatory observation
after that action. It never edits reservations, recreates markers or sends input.
Episode locks can create .lock files; every observation records that limitation.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import os
import re
import stat
import time
from pathlib import Path

from orchestration_common import (ContractError, checked_path, digest, evidence_names, file_ref, json_ref,
                                  object_sha, parse_time, read_json, read_regular, strict_json, utc_now, write_json)
from orchestration_native import require

MAX_MARKERS = 64
MAX_CALLERS = 16
MAX_JSON_BYTES = 1024 * 1024
MAX_SLOTS = 4


def component(value, label):
    require(isinstance(value, str) and value not in {"", ".", ".."} and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value), "invalid " + label)
    return value


def runtime_root(path, *, platform_tmp=False):
    """Normalize only the trusted /tmp prefix, never an untrusted child alias."""
    path = Path(path)
    if platform_tmp and path.is_relative_to("/tmp"):
        path = Path("/tmp").resolve() / path.relative_to("/tmp")
    return checked_path(path)


def snapshot(path):
    """ENOENT alone means absent; malformed, oversized and replaced files do not."""
    row = dict(path=str(path))
    try:
        path = checked_path(path)
        try:
            path.lstat()
        except FileNotFoundError:
            return dict(row, status="ABSENT")
        raw = read_regular(path, MAX_JSON_BYTES)
        value = strict_json(raw)
        require(isinstance(value, dict), "JSON object required")
        return dict(row, status="READABLE", bytes=len(raw), sha256=digest(raw), utf8=raw.decode("utf-8"))
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        return dict(row, status="UNREADABLE", error=str(exc)[:1000])


def snapshot_value(row, path):
    require(isinstance(row, dict) and row.get("path") == str(path), "snapshot path changed")
    status = row.get("status")
    if status == "ABSENT":
        require(set(row) == {"path", "status"}, "invalid absence snapshot")
        return None
    if status == "UNREADABLE":
        require(set(row) == {"path", "status", "error"} and isinstance(row["error"], str), "invalid unreadable snapshot")
        raise ContractError("unreadable original marker/reservation")
    require(status == "READABLE" and set(row) == {"path", "status", "bytes", "sha256", "utf8"},
            "invalid readable snapshot")
    raw = row["utf8"].encode("utf-8")
    require(type(row["bytes"]) is int and row["bytes"] == len(raw) <= MAX_JSON_BYTES and
            digest(raw) == row["sha256"], "snapshot bytes changed")
    value = strict_json(raw)
    require(isinstance(value, dict), "snapshot JSON object required")
    return value


def marker_callers(marker):
    participants = marker.get("participants")
    require(isinstance(participants, list) and participants and all(isinstance(p, dict) for p in participants),
            "original marker participants required")
    ids = [p.get("surface_uuid") for p in participants]
    require(all(isinstance(s, str) and s.strip() == s and s for s in ids) and len(ids) == len(set(ids)),
            "invalid original participant UUIDs")
    callers = sorted(p["surface_uuid"] for p in participants if p.get("role") == "executor")
    require(0 < len(callers) <= MAX_CALLERS, "bounded original executor callers required")
    return callers


def same_marker(left, right):
    return ({k: v for k, v in left.items() if k != "last_activity_at"} ==
            {k: v for k, v in right.items() if k != "last_activity_at"})


def marker_paths(frozen):
    root = checked_path(frozen["active_root"])
    marker = snapshot_value(frozen["marker"], frozen["marker"]["path"])
    workspace = component(frozen["workspace_uuid"], "workspace")
    collaboration = component(marker["collaboration_id"], "collaboration id")
    return root / workspace / (collaboration + ".json"), root / (workspace + ".json")


def validate_frozen(frozen, task_id, workspace, controller_sha256):
    require(set(frozen) == {"version", "task_id", "workspace_uuid", "supervisor_uuid", "controller_sha256",
                           "active_root", "state_root", "marker", "callers", "frozen_at"} and
            type(frozen["version"]) is int and frozen["version"] == 1 and frozen["task_id"] == task_id and
            frozen["workspace_uuid"] == workspace and frozen["controller_sha256"] == controller_sha256,
            "frozen disarm identity/controller changed")
    component(workspace, "workspace")
    checked_path(frozen["active_root"])
    checked_path(frozen["state_root"])
    marker = snapshot_value(frozen["marker"], frozen["marker"]["path"])
    require(isinstance(marker, dict) and marker.get("task_id") == task_id and marker.get("workspace_uuid") == workspace,
            "frozen marker target changed")
    collaboration = marker.get("collaboration_id")
    component(collaboration, "collaboration id")
    require(frozen["marker"]["path"] == str(marker_paths(frozen)[0]), "original v2 marker path changed")
    require(frozen["callers"] == marker_callers(marker), "original caller spelling/order changed")
    owners = [p["surface_uuid"] for p in marker["participants"] if p.get("role") == "supervisor"]
    require(owners == [frozen["supervisor_uuid"]], "original marker supervisor changed")
    parse_time(frozen["frozen_at"])
    return marker


def workspace_snapshots(active_root, workspace, *, required=()):
    root = checked_path(active_root)
    component(workspace, "workspace")
    directory = root / workspace
    paths = set(required) | {root / (workspace + ".json")}
    try:
        checked_path(directory)
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                require(index < MAX_MARKERS, "workspace marker capacity exceeded")
                if entry.name.endswith(".json") and not entry.name.startswith("."):
                    paths.add(directory / entry.name)
    except FileNotFoundError:
        pass
    require(len(paths) <= MAX_MARKERS + 2, "workspace marker capacity exceeded")
    # The evidence contract sorts full path strings. Path compares components,
    # which orders workspace/marker.json before workspace.json on this layout.
    return [snapshot(path) for path in sorted(paths, key=str)]


def freeze_original(campaign, task_id, harness, lifecycle, reask):
    workspace = campaign["sessions"]["codex"]["workspace_uuid"]
    require(harness._workspace_key() == workspace, "scoped disarm workspace changed")
    # /tmp itself is a trusted platform alias; child aliases remain forbidden.
    active_root = runtime_root(harness._ACTIVE_DIR, platform_tmp=True)
    state_root = runtime_root(reask.root())
    rows = workspace_snapshots(active_root, workspace)
    matches = []
    for row in rows:
        marker = snapshot_value(row, row["path"])
        if marker and marker.get("task_id") == task_id:
            matches.append((row, marker))
    v2 = [(row, marker) for row, marker in matches if Path(row["path"]).parent == active_root / workspace]
    require(len(v2) == 1, "one original v2 marker must be frozen before disarm")
    row, marker = v2[0]
    require(all(same_marker(marker, other) for _, other in matches), "another marker uses the original task id")
    value = dict(version=1, task_id=task_id, workspace_uuid=workspace,
                 supervisor_uuid=campaign["sessions"]["codex"]["surface_uuid"],
                 controller_sha256=object_sha(campaign["controller"]), active_root=str(active_root),
                 state_root=str(state_root), marker=row, callers=marker_callers(marker), frozen_at=utc_now())
    validate_frozen(value, task_id, workspace, object_sha(campaign["controller"]))
    require(lifecycle.marker_callers(marker) == value["callers"], "original controller caller semantics changed")
    return value


def reservation_key(marker):
    return dict(collaboration_id=marker["collaboration_id"], task_id=marker["task_id"])


def paused(state):
    value = state.get("paused_tasks", [])
    require(isinstance(value, list) and all(isinstance(p, dict) and set(p) == {"collaboration_id", "task_id"} and
            all(isinstance(v, str) and v for v in p.values()) for p in value), "invalid paused_tasks")
    return value


def state_paths(frozen, caller):
    # UUID spelling is deliberately case-sensitive, exactly like the controller.
    root = checked_path(frozen["state_root"])
    key = digest(caller.encode("utf-8"))
    return root / (key + ".json"), root / (key + ".stop.json"), root / (key + ".lock")


def observe_original(campaign, frozen, harness, lifecycle, reask):
    marker = validate_frozen(frozen, frozen["task_id"], campaign["sessions"]["codex"]["workspace_uuid"],
                             object_sha(campaign["controller"]))
    require(harness._workspace_key() == frozen["workspace_uuid"] and
            str(runtime_root(harness._ACTIVE_DIR, platform_tmp=True)) == frozen["active_root"] and
            str(runtime_root(reask.root())) == frozen["state_root"], "original disarm runtime roots changed")
    require(lifecycle.marker_callers(marker) == frozen["callers"], "original controller caller semantics changed")
    locks = []
    for caller in frozen["callers"]:
        state, stop, lock = state_paths(frozen, caller)
        require(runtime_root(reask.state_path(caller)) == state and runtime_root(reask.stop_file(caller)) == stop,
                "original reservation path changed")
        try:
            checked_path(lock)
            st = lock.lstat()
            require(stat.S_ISREG(st.st_mode), "episode lock is not regular")
            existed = True
        except FileNotFoundError:
            existed = False
        locks.append(dict(path=str(lock), existed_before=existed))
    value = dict(version=1, frozen_sha256=object_sha(frozen), markers=[], reservations=[],
                 locks=locks, lock_files_may_be_created=True, status="OBSERVED", error=None)
    try:
        with lifecycle.marker_locks(marker), contextlib.ExitStack() as stack:
            # Participants first, then marker directories, matching original order.
            for directory in sorted({p.parent for p in marker_paths(frozen)}):
                checked_path(directory)
                try:
                    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    continue
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            value["markers"] = workspace_snapshots(frozen["active_root"], frozen["workspace_uuid"], required=marker_paths(frozen))
            now = time.time()
            for caller in frozen["callers"]:
                path, stop, _ = state_paths(frozen, caller)
                row = dict(caller=caller, state=snapshot(path), stop=snapshot(stop), preview=None)
                value["reservations"].append(row)
                state = snapshot_value(row["state"], path)
                snapshot_value(row["stop"], stop)
                if state is not None:
                    require(state.get("caller_surface_uuid") == caller and state.get("workspace_uuid") == frozen["workspace_uuid"],
                            "reservation caller/workspace changed")
                    paused(state)
                    after = copy.deepcopy(state)
                    changed = lifecycle.apply_boundary(after, marker, "RELEASED", now, publish=False)
                    row["preview"] = dict(changed=bool(changed), state=after,
                                          releases=reservation_key(marker) not in paused(after))
    except BlockingIOError as exc:
        value.update(status="BUSY", error=str(exc)[:1000])
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        value.update(status="UNREADABLE", error=str(exc)[:1000])
    return dict(value, observed_at=utc_now())


def classify(observation, frozen):
    """Recompute release from archived exact snapshots, never from removal counts."""
    marker = validate_frozen(frozen, frozen["task_id"], frozen["workspace_uuid"], frozen["controller_sha256"])
    fields = {"version", "frozen_sha256", "markers", "reservations", "locks", "lock_files_may_be_created",
              "status", "error", "observed_at"}
    require(set(observation) == fields and type(observation["version"]) is int and observation["version"] == 1 and
            observation["frozen_sha256"] == object_sha(frozen), "disarm observation binding changed")
    parse_time(observation["observed_at"])
    expected_locks = [str(state_paths(frozen, c)[2]) for c in frozen["callers"]]
    require(observation["lock_files_may_be_created"] is True and
            [r.get("path") for r in observation["locks"]] == expected_locks and
            all(set(r) == {"path", "existed_before"} and type(r["existed_before"]) is bool for r in observation["locks"]),
            "episode-lock observation changed")
    if observation["status"] in ("BUSY", "UNREADABLE"):
        require(isinstance(observation["error"], str), "missing observation error")
        return "UNPROVEN_BUSY" if observation["status"] == "BUSY" else "UNPROVEN_UNREADABLE"
    require(observation["status"] == "OBSERVED" and observation["error"] is None, "invalid observation status")
    rows = observation["markers"]
    require(isinstance(rows, list) and len(rows) <= MAX_MARKERS + 2, "marker snapshot capacity exceeded")
    paths = [r["path"] for r in rows]
    require(paths == sorted(set(paths)) and {str(p) for p in marker_paths(frozen)} <= set(paths),
            "original v2/legacy marker observations missing")
    remaining = []
    try:
        for row in rows:
            path = Path(row["path"])
            require(path in marker_paths(frozen) or path.parent == Path(frozen["active_root"]) / frozen["workspace_uuid"],
                    "marker observation escapes original workspace")
            current = snapshot_value(row, path)
            if current and (current.get("task_id") == marker["task_id"] or
                            current.get("collaboration_id") == marker["collaboration_id"]):
                if path not in marker_paths(frozen) or not same_marker(current, marker):
                    return "UNPROVEN_CHANGED"
                remaining.append(row)
        reservations = observation["reservations"]
        require([r.get("caller") for r in reservations] == frozen["callers"], "reservation callers missing/reordered")
        held = []
        for row in reservations:
            require(set(row) == {"caller", "state", "stop", "preview"}, "reservation snapshot fields changed")
            path, stop, _ = state_paths(frozen, row["caller"])
            state = snapshot_value(row["state"], path)
            snapshot_value(row["stop"], stop)
            if state is None:
                require(row["preview"] is None, "absent reservation has a preview")
                continue
            require(state.get("caller_surface_uuid") == row["caller"] and state.get("workspace_uuid") == frozen["workspace_uuid"],
                    "reservation identity changed")
            preview = row["preview"]
            require(isinstance(preview, dict) and set(preview) == {"changed", "state", "releases"} and
                    type(preview["changed"]) is bool and type(preview["releases"]) is bool and
                    isinstance(preview["state"], dict) and
                    preview["releases"] == (reservation_key(marker) not in paused(preview["state"])),
                    "invalid release preview")
            if reservation_key(marker) in paused(state):
                continuation = state.get("continuation")
                eligible = state.get("state") == "WAITING_REPLY" or (isinstance(continuation, dict) and
                    continuation.get("enabled") is True and state.get("state") in ("ANSWERED", "CONSUMED"))
                held.append(eligible and state.get("supervisor") == frozen["supervisor_uuid"] and preview["releases"])
    except ContractError:
        return "UNPROVEN_UNREADABLE"
    if held and not all(held):
        return "UNPROVEN_RESERVATION"
    if remaining:
        return "ORIGINAL_MARKER_PRESENT"
    return "LEAKED_RESERVATION" if held else "RELEASED_PROVEN_NOW"


def validate_result(result, frozen):
    require(set(result) == {"version", "task_id", "workspace_uuid", "removed_markers", "before", "after", "observed_at",
                           "observation_atomic_with_action"} and result["observation_atomic_with_action"] is False and
            type(result["version"]) is int and result["version"] == 1 and result["task_id"] == frozen["task_id"] and
            result["workspace_uuid"] == frozen["workspace_uuid"] and
            type(result["removed_markers"]) is int and result["removed_markers"] >= 0, "invalid scoped disarm result")
    require(classify(result["before"], frozen) in ("ORIGINAL_MARKER_PRESENT", "RELEASED_PROVEN_NOW"),
            "original disarm precondition unproven")
    require(classify(result["after"], frozen) == "RELEASED_PROVEN_NOW", "original marker/reservation release unproven")
    marker = snapshot_value(frozen["marker"], frozen["marker"]["path"])
    count = sum(bool((m := snapshot_value(r, r["path"])) and m.get("task_id") == marker["task_id"])
                for r in result["before"]["markers"])
    require(result["removed_markers"] == count, "scoped disarm removal count changed")
    require(parse_time(frozen["frozen_at"]) <= parse_time(result["before"]["observed_at"]) <=
            parse_time(result["after"]["observed_at"]) <= parse_time(result["observed_at"]), "disarm observation chronology changed")


def invoke_disarm(campaign, frozen, harness, lifecycle, reask):
    """Internal synchronous action; caller already holds Coordinator.node_locks.

    Do not reacquire those nonblocking locks here. The public Transport route
    holds them through the original operation, recovery and final publication.
    No mutating descendant may outlive this call or inherit a separate budget.
    """
    before = observe_original(campaign, frozen, harness, lifecycle, reask)
    status = classify(before, frozen)
    require(status in ("ORIGINAL_MARKER_PRESENT", "RELEASED_PROVEN_NOW"),
            "BLOCKED_MAINTAINER: original disarm precondition " + status)
    # The original controller rechecks its own observation under participant and
    # directory locks. Our earlier observation is NOT in that atomic boundary.
    # Do not monkeypatch its locks or claim that a same-task rearm cannot race.
    removed = harness.disarm_task(frozen["task_id"]) if status == "ORIGINAL_MARKER_PRESENT" else 0
    return dict(version=1, task_id=frozen["task_id"], workspace_uuid=frozen["workspace_uuid"],
                removed_markers=removed, before=before,
                after=observe_original(campaign, frozen, harness, lifecycle, reask),
                observed_at=utc_now(), observation_atomic_with_action=False)


def invoke_recovery(campaign, frozen, action, harness, lifecycle, reask):
    """Internal action under the caller's existing node locks; never spawn work."""
    require(action in ("DISARM", "RELEASE"), "unknown disarm recovery action")
    if action == "DISARM":
        return invoke_disarm(campaign, frozen, harness, lifecycle, reask)
    before = observe_original(campaign, frozen, harness, lifecycle, reask)
    require(classify(before, frozen) == "LEAKED_RESERVATION", "leaked reservation release no longer eligible")
    marker = validate_frozen(frozen, frozen["task_id"], campaign["sessions"]["codex"]["workspace_uuid"],
                             object_sha(campaign["controller"]))
    value = lifecycle.task_boundary(marker, "RELEASED")
    return dict(action=action, before=before, controller_result=value, observation_atomic_with_action=False)


def original_result(intent_ref):
    """Pin a returned/error result or its exact absence; never synthesize one."""
    intent = json_ref(intent_ref)
    path = checked_path(intent_ref["path"]).parent / "result.json"
    names = evidence_names(path.parent, {"intent.json", "result.json"})
    require(names in ({"intent.json"}, {"intent.json", "result.json"}), "unexpected disarm operation evidence")
    if "result.json" not in names:
        return None
    ref = file_ref(checked_path(path))
    validate_envelope(json_ref(ref), intent_ref, parse_time(intent["created_at"]))
    return ref


def validate_envelope(result, intent_ref, started):
    require(isinstance(result, dict) and result.get("intent") == intent_ref and
            result.get("status") in ("RETURNED", "ERROR"), "disarm result differs from original intent")
    fields = {"intent", "status", "completed_at"} | (
        {"value"} if result["status"] == "RETURNED" else {"error_type", "error"})
    require(set(result) == fields, "disarm result fields changed")
    if result["status"] == "ERROR":
        require(all(isinstance(result[k], str) for k in ("error_type", "error")), "invalid disarm error")
    completed = parse_time(result["completed_at"])
    require(started <= completed, "disarm result chronology changed")
    return completed


class Recovery:
    """Four immutable intent/result slots; an interrupted action never replays.

    Creating a numbered directory consumes its slot. If intent publication then
    crashes, even an empty directory is BLOCKED_MAINTAINER, never recycled.
    Only one BUSY observation may be retried. All actions require a subsequent
    observation, including actions with an ERROR or absent result.

    Internal precondition: the caller owns Coordinator.node_locks for the entire
    run, including the original synchronous action and receipt publication.
    Recovery does not acquire or reenter them. Separate processes are excluded
    only while those same lock-file inodes remain intact; never unlink/replace
    them. A fork/exec descendant can survive its holder, so asynchronous mutating
    actions are unsupported without separate descendant-lifetime evidence.
    Read-only verify may observe an incomplete concurrent operation and refuse.
    """

    def __init__(self, root, frozen_ref, intent_ref, original_result_ref, runtime=None):
        self.root = checked_path(root)
        self.frozen_ref, self.frozen = frozen_ref, json_ref(frozen_ref)
        self.intent_ref, self.original_result_ref, self.runtime = intent_ref, original_result_ref, runtime
        self.binding = dict(version=1, frozen=frozen_ref, original_intent=intent_ref, original_result=original_result_ref)

    def boundary(self):
        require(json_ref(self.frozen_ref) == self.frozen, "frozen marker changed")
        validate_frozen(self.frozen, self.frozen["task_id"], self.frozen["workspace_uuid"],
                        self.frozen["controller_sha256"])
        intent = json_ref(self.intent_ref)
        require(intent.get("operation") == "scoped-disarm" and
                intent.get("arguments", {}).get("frozen") == self.frozen_ref and
                intent.get("task_id") == self.frozen["task_id"] and
                intent.get("controller_sha256") == self.frozen["controller_sha256"],
                "recovery original intent binding changed")
        started = parse_time(intent["created_at"])
        require(parse_time(self.frozen["frozen_at"]) <= started, "marker was not frozen before original intent")
        require(original_result(self.intent_ref) == self.original_result_ref,
                "original result or original absence changed")
        return (validate_envelope(json_ref(self.original_result_ref), self.intent_ref, started)
                if self.original_result_ref is not None else started)

    def next_slot(self, rows):
        if not rows:
            return "observe", None
        last = rows[-1]
        if last["kind"] == "act":
            require(len(rows) < MAX_SLOTS, "BLOCKED_MAINTAINER: no post-action observation slot")
            return "observe", None
        result = last["result"]
        if result is None or result["status"] != "RETURNED":
            return None, "UNPROVEN_OBSERVATION"
        classification = classify(result["value"], self.frozen)
        if classification == "RELEASED_PROVEN_NOW":
            return None, classification
        if classification == "UNPROVEN_BUSY":
            busy = sum(r["kind"] == "observe" and r["result"] is not None and
                       r["result"]["status"] == "RETURNED" and
                       classify(r["result"]["value"], self.frozen) == "UNPROVEN_BUSY" for r in rows)
            if busy == 1 and len(rows) < MAX_SLOTS:
                return "observe", None
        action = {"ORIGINAL_MARKER_PRESENT": "DISARM", "LEAKED_RESERVATION": "RELEASE"}.get(classification)
        if action and not any(r["kind"] == "act" for r in rows) and len(rows) + 2 <= MAX_SLOTS:
            return "act", action
        return None, classification

    def history(self):
        previous = self.boundary()
        checked_path(self.root)
        if not self.root.exists():
            return []
        paths = sorted(self.root.iterdir())
        require(len(paths) <= MAX_SLOTS and all(p.is_dir() and not p.is_symlink() for p in paths),
                "BLOCKED_MAINTAINER: invalid/excess disarm recovery slots")
        rows = []
        for number, path in enumerate(paths, 1):
            match = re.fullmatch(r"([0-9]{4})-(observe|act)", path.name)
            require(match and int(match[1]) == number, "disarm recovery slots are not contiguous")
            kind = match[2]
            expected_kind, expected_action = self.next_slot(rows)
            require(kind == expected_kind, "recovery slot after terminal/unrecoverable observation or wrong transition")
            names = evidence_names(path, {"intent.json", "result.json"})
            require(names in ({"intent.json"}, {"intent.json", "result.json"}),
                    "BLOCKED_MAINTAINER: incomplete recovery slot intent; slot cannot be recycled")
            ref = file_ref(checked_path(path / "intent.json"))
            intent = json_ref(ref)
            require(set(intent) == set(self.binding) | {"slot", "kind", "action", "created_at"} and
                    all(intent.get(k) == v for k, v in self.binding.items()) and
                    type(intent["slot"]) is int and intent["slot"] == number and intent["kind"] == kind and
                    intent["action"] == expected_action,
                    "recovery intent binding/action changed")
            started = parse_time(intent["created_at"])
            require(previous <= started, "recovery chronology changed")
            previous = started
            result, result_ref = None, None
            if "result.json" in names:
                result_ref = file_ref(checked_path(path / "result.json"))
                result = json_ref(result_ref)
                completed = validate_envelope(result, ref, started)
                if kind == "observe" and result["status"] == "RETURNED":
                    classify(result["value"], self.frozen)
                    require(started <= parse_time(result["value"]["observed_at"]) <= completed,
                            "recovery observation chronology changed")
                previous = completed
            rows.append(dict(kind=kind, intent=ref, result=result, result_ref=result_ref))
        return rows

    def slot(self, kind, action=None):
        rows = self.history()
        require(len(rows) < MAX_SLOTS and (kind, action) == self.next_slot(rows),
                "BLOCKED_MAINTAINER: disarm recovery transition/budget refused")
        require(self.runtime is not None, "recovery runtime required for action")
        number = len(rows) + 1
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root / f"{number:04d}-{kind}"
        root.mkdir()  # Crash before intent publication still consumes this slot.
        ref = write_json(root / "intent.json", dict(self.binding, slot=number, kind=kind,
                         action=action, created_at=utc_now()), exclusive=True)
        try:
            value = (self.runtime.observe_disarm(self.frozen) if kind == "observe" else
                     self.runtime.recover_disarm(self.frozen, action))
            result = dict(intent=ref, status="RETURNED", value=value, completed_at=utc_now())
        except Exception as exc:
            result = dict(intent=ref, status="ERROR", error_type=type(exc).__name__, error=str(exc)[:4000], completed_at=utc_now())
        write_json(root / "result.json", result, exclusive=True)

    def verify(self):
        rows = self.history()
        require(rows and self.next_slot(rows) == (None, "RELEASED_PROVEN_NOW"),
                "disarm recovery needs a final proven release observation")
        return dict(version=1, **{k: v for k, v in self.binding.items() if k != "version"},
                    slots=[dict(kind=r["kind"], intent=r["intent"], result=r["result_ref"]) for r in rows],
                    observed_at=rows[-1]["result"]["value"]["observed_at"])

    def run(self):
        while True:
            rows = self.history()
            kind, action = self.next_slot(rows)
            if kind is None:
                require(action == "RELEASED_PROVEN_NOW", "BLOCKED_MAINTAINER: " + action)
                return self.verify()
            self.slot(kind, action)
