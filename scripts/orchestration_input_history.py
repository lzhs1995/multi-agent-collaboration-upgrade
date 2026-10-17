"""Bounded cross-cell values, bound to original native call/result records.

Only a unique successful *outer* exec result commits store candidates. Calls
are interpreted in byte order, not by timestamps or matching command counts.
An overlapping writer makes both producers uncertain, regardless of which
result arrives last. This module never executes a journal's source.
"""
from __future__ import annotations

import copy
import hashlib
import re

from orchestration_common import ContractError
from orchestration_input_syntax import LIMIT, UNKNOWN, StaticJS, serializable
from orchestration_inputs import invocation_calls, require_unrelated

MAX_EVENTS = 65536
MAX_RECORDS = 250000
MAX_KEYS = 256
MAX_PROOFS = 4096
MAX_ANCESTRY = 64


def require(ok, reason):
    if not ok:
        raise ContractError(reason)


def events(record, provider):
    if provider == "codex" and record.get("type") == "response_item":
        payload = record.get("payload", {})
        require(isinstance(payload, dict), "malformed native tool payload")
        kind = payload.get("type")
        if kind in ("function_call", "custom_tool_call"):
            yield "call", payload.get("call_id"), kind + "_output", payload
        elif kind in ("function_call_output", "custom_tool_call_output"):
            yield "result", payload.get("call_id"), kind, payload
    elif provider == "claude" and record.get("type") in ("assistant", "user"):
        if record.get("isSidechain") is True:
            return
        message = record.get("message")
        require(isinstance(message, dict), "malformed native message")
        content = message.get("content")
        require(isinstance(content, (str, list)), "malformed native message content")
        if isinstance(content, list):
            for block in content:
                require(isinstance(block, dict) and isinstance(block.get("type"), str), "malformed native content block")
                if record["type"] == "assistant" and block.get("type") == "tool_use":
                    yield "call", block.get("id"), "tool_result", block
                elif record["type"] == "user" and block.get("type") == "tool_result":
                    yield "result", block.get("tool_use_id"), "tool_result", block


def outer_exec_success(payload):
    """Nested exec_command exit/session fields describe another operation."""
    if payload.get("is_error") is True or payload.get("isError") is True:
        return False
    value = payload.get("output")
    if isinstance(value, list):
        if not value or not isinstance(value[0], dict) or value[0].get("type") not in ("text", "input_text"):
            return False
        value = value[0].get("text")
    return isinstance(value, str) and re.match(
        r"\AScript completed\nWall time [0-9]+(?:\.[0-9]+)? seconds\nOutput:\n", value) is not None


def _index(reader, start, end):
    index, size, signature = {}, 0, hashlib.sha256()
    for number, (ref, record) in enumerate(reader.records(start, end), 1):
        signature.update(f'{ref["offset"]}:{ref["length"]}:{ref["sha256"]}\n'.encode())
        require(number <= MAX_RECORDS, "native history record observation bound exceeded")
        for kind, key, result_type, payload in events(record, reader.provider):
            require(isinstance(key, str) and 0 < len(key) <= 256, "native call id missing or oversized")
            size += 1
            require(size <= MAX_EVENTS, "native tool history observation bound exceeded")
            item = index.setdefault(key, dict(calls=0, results=0))
            field = "calls" if kind == "call" else "results"
            item[field] += 1
            item[kind] = ref
            item[kind + "_type"] = result_type
    for item in index.values():
        item["unique"] = (item["calls"] == item["results"] == 1 and
                          item["call_type"] == item["result_type"] and
                          item["call"]["offset"] < item["result"]["offset"])
    return index, signature.hexdigest()


def sender_history(reader, start, end, receiver, sender, targets, *, state_root=None, refusal_context=None):
    index, indexed_signature = _index(reader, start, end)
    committed, pending, generations, aliases = {}, {}, {}, {}
    proof = dict(version=1, start=start, end=end, cached_reads=[], sender_calls=[], completed_stores=0)

    def related(keys):
        if keys is None:
            return None
        expanded = set(keys)
        while True:
            before = set(expanded)
            expanded.update(other for key in before for other in aliases.get(key, ()))
            if expanded == before:
                return expanded

    def invalidate(keys):
        if keys is None:
            keys = set(committed) | set(generations)
        keys = related(keys)
        for key in keys:
            committed.pop(key, None)
            generations[key] = generations.get(key, 0) + 1
        require(len(generations) <= MAX_KEYS, "native stored-key observation bound exceeded")

    signature = hashlib.sha256()
    for number, (ref, record) in enumerate(reader.records(start, end), 1):
        require(number <= MAX_RECORDS, "native history record observation bound exceeded")
        signature.update(f'{ref["offset"]}:{ref["length"]}:{ref["sha256"]}\n'.encode())
        for kind, key, result_type, payload in events(record, reader.provider):
            require(key in index, "native history changed during observation")
            item = index[key]
            if kind == "result":
                current = pending.pop(key, None)
                if current is None or not item["unique"] or not outer_exec_success(payload):
                    continue
                if current["conflict"] or current["exports"] is None:
                    continue
                for name, value in current["exports"].items():
                    if value is UNKNOWN or not serializable(value):
                        continue
                    if current["generations"].get(name) != generations.get(name):
                        continue
                    committed[name] = dict(value=copy.deepcopy(value), producer_call=current["ref"],
                                           producer_result=ref, ancestry=current["ancestry"])
                    if isinstance(value, (dict, list)):
                        for source in current["mutable_reads"]:
                            aliases.setdefault(name, set()).add(source)
                            aliases.setdefault(source, set()).add(name)
                        require(len(aliases) <= MAX_KEYS, "native cache alias observation bound exceeded")
                    proof["completed_stores"] += 1
                require(len(committed) <= MAX_KEYS, "native stored-key observation bound exceeded")
                require(sum(len(str(entry["value"]).encode()) for entry in committed.values()) <= 4 * LIMIT,
                        "native stored values observation bound exceeded")
                continue

            name = payload.get("name")
            arguments = payload.get("arguments", payload.get("input"))
            static = None
            if reader.provider == "codex" and name in ("functions.exec", "exec"):
                require(isinstance(arguments, str), "native exec source missing")
                static = StaticJS(arguments, stored={k: v["value"] for k, v in committed.items()})
                effects = static.effects()
                ancestry = []
                reads = effects["reads"]
                mutable_reads = set()
                for loaded in reads or ():
                    entry = committed.get(loaded)
                    if entry is None:
                        continue
                    read = dict(key=loaded, producer_call=entry["producer_call"],
                                producer_result=entry["producer_result"], consumer_call=ref,
                                ancestry=entry["ancestry"])
                    proof["cached_reads"].append(read)
                    ancestry.extend(entry["ancestry"] + [dict(call=entry["producer_call"], result=entry["producer_result"])])
                    if isinstance(entry["value"], (dict, list)):
                        mutable_reads.add(loaded)
                unique_ancestry = {(a["call"]["offset"], a["result"]["offset"]): a for a in ancestry}
                require(len(unique_ancestry) <= MAX_ANCESTRY, "native stored-value ancestry bound exceeded")
                touched = related(effects["writes"])
                reads = related(reads)
                # Cache mutations affect even cells that later fail. Neither an
                # exception nor a newer result establishes actual write order.
                invalidate(touched)
                if touched is not None:
                    static.stored = {k: v["value"] for k, v in committed.items()}
                else:
                    static.stored = {}
                current = dict(ref=ref, writes=touched, reads=reads, exports=static.exports(),
                               conflict=not item["unique"], generations=dict(generations),
                               ancestry=list(unique_ancestry.values()), mutable_reads=mutable_reads)
                for active in pending.values():
                    overlap = (touched is None or active["writes"] is None or reads is None or
                               active["reads"] is None or
                               bool(touched & (active["writes"] | active["reads"])) or
                               bool(reads & active["writes"]))
                    if overlap:
                        current["conflict"] = active["conflict"] = True
                if touched is None or touched:
                    # Duplicate ids have no cache authority; preserve both
                    # effects conservatively as one unknown in-flight writer.
                    if key in pending:
                        current["writes"], current["conflict"] = None, True
                    pending[key] = current

            for call in invocation_calls(name, arguments, static=static):
                try:
                    require_unrelated(call, receiver, sender, targets, state_root=state_root)
                except ContractError as exc:
                    if call["kind"] != "successor" or refusal_context is None:
                        raise
                    from orchestration_refusal import HistoricalRefusal, diagnose_refusal
                    diagnostic = diagnose_refusal(reader, start, end, ref, item, call,
                                                  refusal_context, indexed_signature)
                    diagnostic["absence_check_error"] = str(exc)
                    raise HistoricalRefusal(diagnostic) from exc
                proof["sender_calls"].append(dict(invocation=ref, kind=call["kind"],
                                                  parsed=copy.deepcopy(call)))
            require(len(proof["cached_reads"]) + len(proof["sender_calls"]) <= MAX_PROOFS,
                    "native input provenance observation bound exceeded")
    require(signature.hexdigest() == indexed_signature, "native history changed during observation")
    proof["scan_sha256"] = indexed_signature
    return proof
