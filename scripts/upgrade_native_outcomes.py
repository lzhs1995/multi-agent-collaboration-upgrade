"""Pure original native terminal grammar, shared by continuity and startup.

These parsers grant no native-root, input, task, adoption or availability authority.
"""
from __future__ import annotations

import uuid
from orchestration_common import parse_time, require


def native_text(record, provider, role):
    if not isinstance(record, dict) or provider not in ("claude", "codex"):
        return None
    if provider == "codex":
        message = record.get("payload", {})
        if not isinstance(message, dict) or record.get("type") != "response_item" or message.get("type") != "message":
            return None
    else:
        if record.get("type") != role or record.get("isSidechain") is True:
            return None
        message = record.get("message", {})
    if not isinstance(message, dict) or message.get("role") != role:
        return None
    content = message.get("content")
    if isinstance(content, str) and provider == "claude":
        return content
    if not isinstance(content, list):
        return None
    kind = ("input_text" if role == "user" else "output_text") if provider == "codex" else "text"
    texts = []
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") == kind and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif role == "assistant" and block.get("type") in ("thinking", "redacted_thinking"):
            # Ignore reasoning contents, never expose them as receipt evidence.
            if not isinstance(block.get("thinking", block.get("data")), str):
                return None
        else:
            return None
    return texts[0] if len(texts) == 1 else None



def canonical_uuid(value, label):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value,
            label + " must be an original canonical UUID")
    return value


def outcome(session, task_id, ref, record, attempt_id, now, *, kind="API_SUCCESS", http_status=None):
    epoch = parse_time(record["timestamp"]).timestamp()
    require(0 <= epoch <= now, "native outcome is outside the observation clock")
    value = dict(task_id=task_id, surface_uuid=session["surface_uuid"],
                session_id=session["session_id"], attempt_id=attempt_id,
                kind=kind, terminal=True, at_epoch=epoch, native_record=ref)
    if http_status is not None:
        require(type(http_status) is int and 100 <= http_status <= 599,
                "native HTTP status must be a concrete integer")
        value["http_status"] = http_status
    return value


def codex_outcomes(session, records, task_id, now, *, outcome_after=0):
    active, model, final = None, None, None
    active_offset, active_observed = None, False
    values = []

    def append(ref, record, attempt_id, **fields):
        if ref["offset"] >= outcome_after:
            values.append(outcome(session, task_id, ref, record, attempt_id, now, **fields))

    for ref, record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "event_msg" and payload.get("type") == "task_started":
            # A crash can leave an unclosed historical turn. It is not current
            # availability or an API error. A fresh start may replace only that
            # pre-fence state, never a turn with observed post-fence activity.
            require(active is None or (active_offset < outcome_after and not active_observed),
                    "overlapping original native turns")
            active = canonical_uuid(payload.get("turn_id"), "native turn id")
            active_offset, active_observed = ref["offset"], ref["offset"] >= outcome_after
            model, final = None, None
            continue
        # Thread configuration/goal events and session metadata may sit between
        # a crashed historical turn and its next start. They are not activity
        # of that turn. Native response items have implicit open-turn ownership;
        # context and event records must carry the matching original turn id.
        turn_activity = (record.get("type") == "response_item" or
            record.get("type") in ("turn_context", "event_msg") and payload.get("turn_id") == active)
        if active is not None and ref["offset"] >= outcome_after and turn_activity:
            active_observed = True
        if (record.get("type") == "turn_context" and active is not None
              and payload.get("turn_id") == active):
            require(payload.get("model") == session["model"], "native continuity model changed")
            model = payload["model"]
        elif (record.get("type") == "response_item" and active is not None
              and payload.get("phase") == "final_answer"):
            final = native_text(record, "codex", "assistant")
        elif (record.get("type") == "event_msg" and payload.get("type") == "task_complete"
              and active is not None and payload.get("turn_id") == active):
            error = payload.get("error")
            if (model == session["model"] and payload.get("error") is None
                    and isinstance(final, str) and final.strip()
                    and payload.get("last_agent_message") == final):
                append(ref, record, "codex-turn:" + active)
            elif model == session["model"] and isinstance(error, dict):
                info = error.get("codex_error_info")
                status = info.get("http_status_code") if isinstance(info, dict) else None
                # The actual original Codex CLI serializes this enum as an
                # externally tagged object. Keep the older flat contract,
                # but never infer an HTTP result from arbitrary nested data.
                if isinstance(info, dict) and set(info) == {"http_connection_failed"}:
                    connection = info["http_connection_failed"]
                    if isinstance(connection, dict) and set(connection) == {"http_status_code"}:
                        status = connection["http_status_code"]
                if type(status) is int and 400 <= status <= 599:
                    append(ref, record, "codex-turn:" + active, kind="API_ERROR", http_status=status)
                else:
                    append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            else:
                append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            active, model, final = None, None, None
        elif (record.get("type") == "event_msg" and payload.get("type") == "turn_aborted"
              and active is not None and payload.get("turn_id") == active):
            require(payload.get("reason") in ("interrupted", "replaced", "review_ended", "budget_limited"),
                    "native abort has no canonical reason")
            append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            active, model, final = None, None, None
    return values


def claude_outcomes(session, records, task_id, now, *, outcome_after=0, deduplicate_messages=True):
    # Continuity counts one API response, not one streamed content block.
    # Native creation can additionally inspect every qualifying block of that
    # response without bypassing ancestry, model or terminal qualification.
    require(type(deduplicate_messages) is bool, "native message deduplication must be explicit")
    ancestors, seen_messages, values = {}, set(), []
    for ref, record in records:
        if record.get("isSidechain") is True or record.get("sessionId") != session["session_id"]:
            continue
        if record.get("type") == "user" and native_text(record, "claude", "user") is not None:
            uid = canonical_uuid(record.get("uuid"), "native user id")
            ancestors[uid] = uid
            continue
        parent = ancestors.get(record.get("parentUuid"))
        # Native hooks, deferred tools and queued commands are ancestry links,
        # not assistant API outcomes. They retain the original session/UUID gate.
        if parent and record.get("type") in ("assistant", "user", "system", "attachment"):
            uid = canonical_uuid(record.get("uuid"), "native parent-chain id")
            ancestors[uid] = parent
        if record.get("type") == "system" and record.get("subtype") == "api_error" and parent:
            continue
        message = record.get("message")
        if (record.get("type") == "assistant" and isinstance(message, dict)
                and record.get("isApiErrorMessage") is not True
                and isinstance(message.get("model"), str) and message["model"] != "<synthetic>"):
            require(message["model"] == session["model"], "native continuity model changed")
        if (record.get("type") != "assistant" or not isinstance(message, dict)
                or parent is None
                or record.get("truncatedAfterOutput") is True):
            continue
        if record.get("isApiErrorMessage") is True:
            status = record.get("apiErrorStatus")
            if (message.get("role") == "assistant" and message.get("model") == "<synthetic>"
                    and message.get("stop_reason") == "stop_sequence"):
                canonical_uuid(record.get("uuid"), "native terminal error id")
                message_id = canonical_uuid(message.get("id"), "native error message id")
                if deduplicate_messages and message_id in seen_messages:
                    continue
                seen_messages.add(message_id)
                known = type(status) is int and 400 <= status <= 599
                if ref["offset"] >= outcome_after:
                    values.append(outcome(session, task_id, ref, record, "claude-message:" + message_id, now,
                        kind="API_ERROR" if known else "NATIVE_OUTCOME_UNKNOWN",
                        http_status=status if known else None))
            continue
        content = message.get("content")
        visible = isinstance(content, list) and any(isinstance(block, dict) and (
            block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip()
            or block.get("type") == "tool_use" and isinstance(block.get("id"), str)
            and isinstance(block.get("name"), str) and isinstance(block.get("input"), dict))
            for block in content)
        if (message.get("role") == "assistant" and message.get("type") == "message"
                and message.get("model") == session["model"] and message["model"] != "<synthetic>"
                and message.get("stop_reason") in ("end_turn", "tool_use", "stop_sequence")
                and isinstance(message.get("id"), str) and message["id"].startswith("msg_")
                and visible):
            canonical_uuid(record.get("uuid"), "native assistant id")
            if deduplicate_messages and message["id"] in seen_messages:
                continue
            seen_messages.add(message["id"])
            if ref["offset"] >= outcome_after:
                values.append(outcome(session, task_id, ref, record,
                                      "claude-message:" + message["id"], now))
    return values

