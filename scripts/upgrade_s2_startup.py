"""S2 lane Goal startup text (R579): the same template in both advisory arms, only group identity differs.

The Codex lane waits for the campaign's generated supervisor instructions; the Claude lanes run only
original task packs. claude_a's semantic send duty is the frozen work-policy clause (semantic_sender),
not repeated here. Nothing here names old trios, shadow advice or a different exercise.
"""
from __future__ import annotations

from pathlib import Path

GOAL = ("Complete every original task pack that this group's Codex supervisor dispatches to this lane, "
        "until all registered runs of this group close, and report the actual results.")

RULES = (
    "Run the startup probe exactly as written, as one tool call by itself: no redirection such as 2>&1, no pipe, "
    "no ; or &&, no second command.\n"
    "The external verifier independently authenticates hooks, native identities and isolation, then publishes "
    "adoption. Until a file you wait for exists, use bounded read-only file checks of at most 30 seconds each.\n"
    "Never run hooks manually, fabricate identities, receipts, timestamps or health labels, publish adoption, "
    "create agents or replacement sessions, or send messages outside the pinned harness.\n"
    "Upgrade findings, including any parser, hook or gate inside this private controller, belong to owner43 "
    "(7063B683); shared MAC findings belong to surface40. Keep them as pinned file reports under this root's "
    "evidence and keep all original sources.\n")


def lane_responsibilities(group_name: str, group: dict, campaign_root) -> dict:
    """Responsibilities per lane for one registered S2 group; campaign_root is where freeze will write it."""
    instructions = Path(campaign_root) / "supervisor-instructions.md"
    head = "S2 JEV experiment group %s (%s, strategy omo), lane %s.\n"
    codex = (head % (group_name, group["advisory"], "codex")
             + "You are this group's original Codex supervisor. After adoption is published, wait for the frozen "
             "campaign's generated supervisor instructions at " + str(instructions) + ". Read them completely and "
             "follow them in this same session. If they do not exist 2100 seconds after your own startup probe "
             "call, preserve evidence and report the actual timeout.\n" + RULES)
    claude = {lane: head % (group_name, group["advisory"], lane)
              + "Execute only original task packs this group's supervisor dispatches to you through the pinned "
              "harness: read the complete pack, run its bounded business command once, write the pinned report "
              "and actively send the original callback. Never repeat a business command or callback.\n" + RULES
              for lane in ("claude_a", "claude_b")}
    return dict(codex=codex, **claude)
