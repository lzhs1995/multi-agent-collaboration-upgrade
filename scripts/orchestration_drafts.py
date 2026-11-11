"""Explicit publication of immutable task templates, outside measured run roots.

Templates contain no reservation, nonce or accepted dependency binding. Their
publication cannot start a run, claim a lane or authorize input. The frozen plan
and coordinator remain the only task definition and lifecycle authorities.
"""
from pathlib import Path

from orchestration_common import checked_path, exclusive_lock, file_ref, json_ref, write_json
from orchestration_native import require
from orchestration_observe import package_drafts


def draft_path(runner, run_id):
    runner.spec(run_id)
    return checked_path(Path(runner.campaign["root"]) / "package-drafts" / (run_id + ".json"))


def published_drafts(runner, run_id, *, expected=None):
    """Read only. A changed or partial publication is never silently replaced."""
    path = draft_path(runner, run_id)
    if not path.exists():
        return None
    expected = expected if expected is not None else package_drafts(runner.ref, runner.campaign, runner.spec(run_id))
    ref = file_ref(path)
    require(json_ref(ref) == expected, "published drafts differ from the frozen campaign; preserve original publication")
    return ref


def publish_drafts(runner, run_id):
    value = package_drafts(runner.ref, runner.campaign, runner.spec(run_id))
    path = draft_path(runner, run_id)
    with exclusive_lock(path.with_suffix(".lock")):
        ref = published_drafts(runner, run_id, expected=value)
        if ref is None:
            ref = write_json(path, value, exclusive=True)
    return dict(schema="orchestration-draft-publication-v1", publication=ref,
                nodes=len(value["drafts"]), dispatch_authorized=False, input_operations=0)
