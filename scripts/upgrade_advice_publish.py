"""Publish one advice body as an ordinary pinned message reference.

The advice is not a new transport. It is an ordinary message body, so it gets a
real ``MESSAGE_REFERENCE_V2`` notice minted by ``cmux_prompt_reference.plan`` and
a sha-named read-only file written by ``persist``. The existing reader, the
existing wire parser and the existing exposure matcher therefore apply to it
unchanged, and the notice is never delivered to any lane: it exists only as the
exact string a later read must name.

The body states its own contract in its first field: reading it is not accepting
it, and the advice binds nothing. The body also carries ``input_units`` - the
exact frozen campaign input refs as read when the advice was produced - inside
the sha-pinned bytes, so the input version cited at binding time cannot be
edited after publication without breaking the sha the decision names.
"""
from __future__ import annotations

import json
from pathlib import Path

from cmux_prompt_reference import digest, encoded, persist, plan

SCHEMA = "upgrade-advice-body-v1"
SCOPE = "advisory_language_only_binds_nothing_and_decides_no_authority"
MARKER_PREFIX = "JEV_ADVICE"


def advice_dir(root, node_id) -> Path:
    """The advice body lives beside the node's own evidence, under its run."""
    return Path(root) / "nodes" / str(node_id) / "advice"


def advice_tag(advice: dict) -> str:
    """A short, deterministic tag for the advice value itself.

    This is deliberately *not* the published body's sha256: the marker is a
    field inside that body, so a self-referential digest is impossible. The tag
    identifies the advice value; the body's own sha256 is what the decision
    pins.
    """
    return digest(encoded(normalised_advice(advice)))[:16]


def normalised_advice(advice: dict) -> dict:
    """Candidates are a tuple in memory and a sorted list on the wire."""
    return dict(advice, candidates=sorted(advice["candidates"]))


def advice_marker(run_id, node_id, advice_sha_prefix) -> str:
    """An inline-safe marker; ``plan`` refuses anything outside its charset."""
    return "_".join((MARKER_PREFIX, str(run_id), str(node_id), str(advice_sha_prefix)))


def advice_body(campaign_ref, run_id, node_id, advice, request, *, marker, input_units) -> str:
    """The exact UTF-8 bytes published for this advice; always multi-line JSON.

    ``json.dumps(..., indent=2)`` guarantees a newline, so ``plan`` always takes
    its reference branch and never inlines the body into a wire notice.
    """
    value = dict(schema=SCHEMA, scope=SCOPE, reply_required=False, marker=marker,
                 campaign=campaign_ref, run_id=str(run_id), node_id=str(node_id),
                 input_units=input_units, advice=normalised_advice(advice), request=request)
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def publish_advice(root, run_id, node_id, body) -> dict:
    """Mint the notice and exclusively create the sha-named read-only body."""
    value = json.loads(body)
    if not isinstance(value, dict) or not isinstance(value.get("marker"), str):
        raise ValueError("ADVICE_BODY_MARKER_REQUIRED")
    if value.get("run_id") != str(run_id) or value.get("node_id") != str(node_id):
        raise ValueError("ADVICE_BODY_RUN_NODE_MISMATCH")
    prepared = plan(body, value["marker"], advice_dir(root, node_id))
    if prepared["reference"] is None:
        raise ValueError("ADVICE_BODY_MUST_USE_A_REFERENCE")
    pin = persist(prepared["reference"], body)
    return dict(notice=prepared["text"], reference=prepared["reference"], pin=pin,
                marker=value["marker"], body_sha256=prepared["reference"]["sha256"])
