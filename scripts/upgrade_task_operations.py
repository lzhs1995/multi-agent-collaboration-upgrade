"""Durable one-attempt operations for a future authenticated dynamic-tool actor.

Only a trusted controller opens this ledger; all experimental agents must be
kernel-denied writes to its directory. The caller must verify the actual server
peer on receipt before calling receive/complete, and authenticate task enrollment
separately. A request supplied to this library is NOT itself caller proof.

The model selects a controller-declared operation ID, never arbitrary commands,
paths or another task. An intent consumes the operation even after a crash.
No public method retries execution or a response. Recovery reads the original
ledger and original external receipts. Native completion is separate from
subsequent model consumption and from business acceptance.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from orchestration_common import (canonical, checked_path, contract_errors,
                                  object_sha, read_ref, require, strict_json, utc_now)
from upgrade_dynamic_route import NAMESPACE, envelope, request_key

TOOL = "execute"
MAX_BYTES = 1024 * 1024


def _uuid(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value, "TASK_OPERATION_UUID_INVALID")
    return value


def _name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
            "TASK_OPERATION_NAME_INVALID")
    return value


def _result(value):
    require(isinstance(value, dict) and set(value) == {"success", "contentItems"}
            and type(value["success"]) is bool and isinstance(value["contentItems"], list)
            and 0 < len(value["contentItems"]) <= 32 and len(canonical(value)) <= MAX_BYTES,
            "TASK_OPERATION_RESULT_INVALID")
    require(all(isinstance(x, dict) and set(x) == {"type", "text"}
                and x["type"] == "inputText" and isinstance(x["text"], str)
                for x in value["contentItems"]), "TASK_OPERATION_TEXT_RESULT_REQUIRED")
    return value


def tool_definition():
    return {"namespace": NAMESPACE, "name": TOOL,
            "description": "Execute one explicitly registered task operation by its operation_id. Each ID has one attempt; unknown outcomes must be inspected, never retried.",
            "inputSchema": {"type": "object", "properties": {"operation_id": {"type": "string"}},
                            "required": ["operation_id"], "additionalProperties": False}}


class OperationLedger:
    """SQLite transactions serialize independent controller contenders.

    Holds no network connection and starts no processes. Creation is explicit
    and exclusive; a partially created database is preserved, never repaired by
    replay. The enclosing directory must be private and protected from agents.
    """
    @classmethod
    @contract_errors("TASK_OPERATION_CREATE_REFUSED")
    def create(cls, path, registration):
        path = checked_path(path)
        require(isinstance(registration, dict) and set(registration) == {
            "schema", "task_id", "thread_id", "turn_id", "controller_id", "authority_ref", "operations"}
            and registration["schema"] == "upgrade-task-operations-v1", "TASK_OPERATION_REGISTRATION_INVALID")
        _name(registration["task_id"]); _uuid(registration["thread_id"]); _uuid(registration["turn_id"])
        _name(registration["controller_id"])
        read_ref(registration["authority_ref"], "new task authority")
        ops = registration["operations"]
        require(isinstance(ops, dict) and 0 < len(ops) <= 1024 and len(canonical(registration)) <= MAX_BYTES,
                "TASK_OPERATION_BUDGET_INVALID")
        for key, spec in ops.items():
            _name(key)
            require(isinstance(spec, dict) and set(spec) == {"kind", "payload"}
                    and isinstance(spec["payload"], dict), "TASK_OPERATION_SPEC_INVALID")
            _name(spec["kind"])
        st = path.parent.stat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid()
                and stat.S_IMODE(st.st_mode) == 0o700, "TASK_OPERATION_PRIVATE_DIRECTORY_REQUIRED")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.executescript('''
                CREATE TABLE registration (id INTEGER PRIMARY KEY CHECK(id=1), body BLOB NOT NULL);
                CREATE TABLE calls (call_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
                    request_key TEXT NOT NULL UNIQUE, request BLOB NOT NULL, phase TEXT NOT NULL,
                    result BLOB, receipt BLOB);
                CREATE TABLE events (sequence INTEGER PRIMARY KEY, call_id TEXT NOT NULL,
                    kind TEXT NOT NULL, body BLOB NOT NULL);
            ''')
            db.execute("INSERT INTO registration VALUES (1,?)", (canonical(registration),))
            db.commit()
        finally:
            db.close()
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return cls(path, object_sha(registration))

    @contract_errors("TASK_OPERATION_OPEN_REFUSED")
    def __init__(self, path, registration_sha256):
        self.path = checked_path(path)
        st = self.path.lstat()
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
                and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1,
                "TASK_OPERATION_LEDGER_IDENTITY_INVALID")
        self.identity = (st.st_dev, st.st_ino)
        self.registration_sha256 = registration_sha256
        self.db = sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True, timeout=5, isolation_level=None)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            row = self.db.execute("SELECT body FROM registration WHERE id=1").fetchone()
            require(row is not None, "TASK_OPERATION_INCOMPLETE_LEDGER")
            self.registration = strict_json(row[0])
            require(object_sha(self.registration) == registration_sha256, "TASK_OPERATION_REGISTRATION_CHANGED")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def _transaction(self):
        st = self.path.lstat()
        require((st.st_dev, st.st_ino) == self.identity and stat.S_IMODE(st.st_mode) == 0o600,
                "TASK_OPERATION_LEDGER_REPLACED")
        require(object_sha(self.registration) == self.registration_sha256,
                "TASK_OPERATION_REGISTRATION_CHANGED")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, call, kind, body):
        self.db.execute("INSERT INTO events(call_id,kind,body) VALUES (?,?,?)",
                        (call, kind, canonical({"at": utc_now(), **body})))

    @contract_errors("TASK_OPERATION_RECEIVE_REFUSED")
    def receive(self, message):
        value = envelope(message)
        require(value.get("method") == "item/tool/call" and "id" in value,
                "TASK_OPERATION_DYNAMIC_REQUEST_REQUIRED")
        params = value.get("params")
        require(isinstance(params, dict) and set(params) == {
            "threadId", "turnId", "callId", "namespace", "tool", "arguments"}, "TASK_OPERATION_FIELDS_INVALID")
        require(_uuid(params["threadId"]) == self.registration["thread_id"], "TASK_OPERATION_FOREIGN_THREAD")
        require(_uuid(params["turnId"]) == self.registration["turn_id"], "TASK_OPERATION_FOREIGN_TURN")
        require(params["namespace"] == NAMESPACE and params["tool"] == TOOL, "TASK_OPERATION_TOOL_NOT_OWNED")
        call = _name(params["callId"])
        args = params["arguments"]
        require(isinstance(args, dict) and set(args) == {"operation_id"}, "TASK_OPERATION_ARGUMENTS_INVALID")
        op = _name(args["operation_id"])
        require(op in self.registration["operations"], "TASK_OPERATION_UNREGISTERED")
        key = json.dumps(request_key(value["id"]))
        with self._transaction():
            row = self.db.execute("SELECT call_id FROM calls WHERE call_id=? OR operation_id=? OR request_key=?",
                                  (call, op, key)).fetchone()
            require(row is None, "TASK_OPERATION_ALREADY_INTENDED_NO_REPLAY")
            self.db.execute("INSERT INTO calls VALUES (?,?,?,?,?,NULL,NULL)",
                            (call, op, key, canonical(value), "INTENDED"))
            self._event(call, "INTENDED", {"request": value, "operation": self.registration["operations"][op]})
        # Only this first return permits the controller to attempt its declared
        # operation. A crash from this point leaves INTENDED/unknown effects.
        return strict_json(canonical(self.registration["operations"][op]))

    def inspect(self, call):
        row = self.db.execute("SELECT operation_id,request,phase,result,receipt FROM calls WHERE call_id=?", (call,)).fetchone()
        require(row is not None, "TASK_OPERATION_UNKNOWN_CALL")
        return dict(operation_id=row[0], request=strict_json(row[1]), phase=row[2],
                    result=strict_json(row[3]) if row[3] else None,
                    receipt=strict_json(row[4]) if row[4] else None)

    @contract_errors("TASK_OPERATION_RESULT_REFUSED")
    def operation_result(self, call, result, receipt):
        _result(result); read_ref(receipt, "original external operation receipt")
        with self._transaction():
            row = self.inspect(call)
            require(row["phase"] == "INTENDED", "TASK_OPERATION_RESULT_PHASE_INVALID")
            self.db.execute("UPDATE calls SET phase='RESULT_RECORDED',result=?,receipt=? WHERE call_id=?",
                            (canonical(result), canonical(receipt), call))
            self._event(call, "RESULT_RECORDED", {"result": result, "receipt": receipt})

    @contract_errors("TASK_OPERATION_RESPONSE_REFUSED")
    def response_intent(self, call):
        with self._transaction():
            row = self.inspect(call)
            require(row["phase"] == "RESULT_RECORDED", "TASK_OPERATION_RESPONSE_ALREADY_INTENDED_OR_CLOSED")
            read_ref(row["receipt"], "original external operation receipt")
            response = {"id": row["request"]["id"], "result": row["result"]}
            self.db.execute("UPDATE calls SET phase='RESPONSE_INTENDED' WHERE call_id=?", (call,))
            self._event(call, "RESPONSE_INTENDED", {"response": response})
        return response

    @contract_errors("TASK_OPERATION_STOP_REFUSED")
    def stop(self, call, reason):
        require(reason in ("UNKNOWN", "CANCELLED"), "TASK_OPERATION_STOP_REASON_INVALID")
        with self._transaction():
            row = self.inspect(call)
            require(row["phase"] in ("INTENDED", "RESULT_RECORDED", "RESPONSE_INTENDED"),
                    "TASK_OPERATION_ALREADY_CLOSED")
            self.db.execute("UPDATE calls SET phase=? WHERE call_id=?", (reason, call))
            self._event(call, reason, {"previous_phase": row["phase"]})

    @contract_errors("TASK_OPERATION_COMPLETION_REFUSED")
    def complete(self, message, evidence):
        """Caller supplies an authenticated native item/completed, never a UI echo.

        Evidence is a persisted raw original message. Its source authenticity
        remains the controller's responsibility. UNKNOWN may be resolved read-only
        by an exact native result, but no execution/send is reopened.
        """
        require(strict_json(read_ref(evidence, "native completion")) == message,
                "TASK_OPERATION_COMPLETION_EVIDENCE_CHANGED")
        require(isinstance(message, dict) and message.get("method") == "item/completed"
                and "id" not in message, "TASK_OPERATION_NATIVE_COMPLETION_REQUIRED")
        params = message["params"]; item = params["item"]; call = _name(item["id"])
        with self._transaction():
            row = self.inspect(call)
            require(row["phase"] in ("RESPONSE_INTENDED", "UNKNOWN", "CANCELLED"),
                    "TASK_OPERATION_NO_RESPONSE_INTENT")
            origin = row["request"]["params"]
            # A result recorded before cancellation is not enough: proof of
            # response intent must exist, including after an UNKNOWN stop.
            sent = self.db.execute("SELECT 1 FROM events WHERE call_id=? AND kind='RESPONSE_INTENDED'", (call,)).fetchone()
            require(sent is not None, "TASK_OPERATION_NO_RESPONSE_INTENT")
            match = (params.get("threadId") == origin["threadId"] and params.get("turnId") == origin["turnId"]
                and item.get("type") == "dynamicToolCall" and item.get("namespace") == NAMESPACE
                and item.get("tool") == TOOL and item.get("arguments") == origin["arguments"]
                and type(item.get("success")) is bool and item["success"] == row["result"]["success"]
                and item.get("contentItems") == row["result"]["contentItems"]
                and item.get("status") == ("completed" if row["result"]["success"] else "failed"))
            phase = "NATIVE_COMPLETED" if match and row["phase"] != "CANCELLED" else "CONFLICT"
            self.db.execute("UPDATE calls SET phase=? WHERE call_id=?", (phase, call))
            self._event(call, phase, {"evidence": evidence, "native": message, "model_consumption_proven": False})
        return {"phase": phase, "model_consumption_proven": False, "execution_retry_allowed": False}
