"""Observe terminal Codex errors in the original journal; never retry input.

Only a native task_complete event after the campaign freeze is a terminal
failure. Screen text, tool errors, old failures and quoted examples are not.
The in-memory cursor bounds repeated reading; a restart safely rereads evidence.
"""
from __future__ import annotations

import uuid

from orchestration_common import ContractError, parse_time, require
from orchestration_native import NativeReader


class CodexFailureWatch:
    def __init__(self, session, *, fixtures=False):
        self.reader = NativeReader(session, fixtures=fixtures)
        require(session["provider"] == "codex", "Codex failure watch requires Codex")
        self.fence = session["freeze_fence"]
        self.offset = self.reader.verify_pin(self.fence)
        self.previous = None
        self.failure = None
        self.pending = True

    def verify_saved(self, saved):
        """A crash may reuse the first observation, including its original EOF."""
        require(self.failure is not None and isinstance(saved, dict), "native failure evidence required")
        require(saved == dict(self.failure, observed_eof=saved.get("observed_eof")),
                "original native failure evidence changed")
        self.reader.verify_pin(saved["observed_eof"])
        require(saved["observed_eof"]["offset"] >= self.offset,
                "saved native EOF precedes terminal error")

    def observe(self, max_records=256):
        require(type(max_records) is int and 0 < max_records <= 256,
                "observation budget must be between 1 and 256 records")
        self.reader.verify_pin(self.fence)
        if self.previous is not None:
            self.reader.record(self.previous)
        if self.failure is not None:
            return self.failure
        self.pending = True
        try:
            end = self.reader.snapshot()
        except ContractError as exc:
            if str(exc) == "native EOF is not a record boundary; observe later":
                return None
            raise
        require(end["offset"] >= self.offset, "native failure observation was truncated")
        for index, (ref, record) in enumerate(self.reader.records(self.offset, end["offset"]), 1):
            # A provider's explanatory text is never parsed as another record.
            payload = record.get("payload")
            if record.get("type") == "event_msg" and isinstance(payload, dict):
                error = payload.get("error")
                message = error.get("message") if isinstance(error, dict) else error
                if (payload.get("type") == "task_complete"
                        and isinstance(message, str) and message.strip()):
                    turn = payload.get("turn_id")
                    require(isinstance(turn, str) and str(uuid.UUID(turn)) == turn,
                            "terminal error lacks a canonical turn id")
                    parse_time(record.get("timestamp"))
                    self.failure = dict(status="NATIVE_CODEX_TURN_FAILED", record=ref,
                        session_id=self.reader.session["session_id"], turn_id=turn,
                        occurred_at=record["timestamp"], message=message[:2000],
                        codex_error_info=(error.get("codex_error_info") if isinstance(error, dict) else None),
                        freeze_fence=self.fence, observed_eof=end, input_operations=0)
            self.offset = ref["offset"] + ref["length"]
            self.previous = ref
            if self.failure is not None or index >= max_records:
                break
        self.pending = self.failure is None and self.offset < end["offset"]
        return self.failure
