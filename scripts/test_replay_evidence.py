"""Historical v1 parsing tests; these fixtures cannot prove measured gains."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from orchestration_planner import build_schedule  # noqa: E402
from validate_replay_evidence import EvidenceError, validate_replay_evidence  # noqa: E402


PLAN = json.loads((ROOT / "tests/fixtures/orchestration-plan.json").read_text(encoding="utf-8"))


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def proof(name: str) -> dict[str, str]:
    return {"status": "PASS", "source": f"/replay/{name}.json"}


def make_evidence() -> dict:
    start = datetime(2026, 10, 10, tzinfo=timezone.utc)
    expected = {node["id"]: node for node in PLAN["nodes"]}

    expected_waves = {
        node_id: wave["wave"]
        for wave in build_schedule(PLAN)
        for node_id in wave["nodes"]
    }

    def node_record(node_id: str, started: datetime, finished: datetime, wave: int | None = None) -> dict:
        node = expected[node_id]
        return {
            "node_id": node_id,
            "task_id": f"{PLAN['plan_id']}--{node_id}",
            "executor": node["executor"],
            "transport": node["transport"],
            "session_policy": "reuse_existing",
            "replacement_session": False,
            **({"wave": wave} if wave is not None else {}),
            "started_at": stamp(started),
            "finished_at": stamp(finished),
            "native_receipt": proof(f"{node_id}-native"),
            "ack": proof(f"{node_id}-ack"),
            "callback": proof(f"{node_id}-callback"),
            "closeout": proof(f"{node_id}-closeout"),
            "acceptance": proof(f"{node_id}-acceptance"),
            "report_sha256": "a" * 64,
            "retries": 0,
            "interventions": 0,
        }

    serial_nodes = []
    cursor = start
    for node in PLAN["nodes"]:
        finished = cursor + timedelta(seconds=10)
        serial_nodes.append(node_record(node["id"], cursor, finished))
        cursor = finished
    serial_end = cursor

    scheduled_nodes = []
    cursor = start + timedelta(hours=1)
    for wave in build_schedule(PLAN):
        wave_start = cursor
        wave_end = wave_start + timedelta(seconds=10)
        for node_id in wave["nodes"]:
            scheduled_nodes.append(node_record(node_id, wave_start, wave_end, expected_waves[node_id]))
        cursor = wave_end
    return {
        "version": 1,
        "evidence_class": "measured_cli_replay",
        "plan_id": PLAN["plan_id"],
        "third_party_api": False,
        "replacement_sessions": False,
        "external_model_calls": 0,
        "correctness": {"status": "PASS", "checks": 7},
        "runs": [
            {
                "mode": "serial",
                "status": "PASS",
                "started_at": stamp(start),
                "finished_at": stamp(serial_end),
                "nodes": serial_nodes,
            },
            {
                "mode": "scheduled",
                "status": "PASS",
                "started_at": stamp(start + timedelta(hours=1)),
                "finished_at": stamp(cursor),
                "nodes": scheduled_nodes,
            },
        ],
    }


class ReplayEvidenceTests(unittest.TestCase):
    def test_cli_returns_three_for_readable_self_reported_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "history.json"
            evidence.write_text(json.dumps(make_evidence()), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/validate_replay_evidence.py"),
                "--plan", str(ROOT / "tests/fixtures/orchestration-plan.json"), "--evidence", str(evidence)],
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["status"], "HISTORICAL_ONLY")
        self.assertFalse(json.loads(result.stdout)["promotion_eligible"])

    def test_self_reported_cli_replay_is_historical_only(self):
        result = validate_replay_evidence(PLAN, make_evidence())
        self.assertEqual(result["status"], "HISTORICAL_ONLY")
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["meets_target"])
        self.assertTrue(result["claimed_meets_target"])
        self.assertGreaterEqual(result["wall_time_reduction_percent"], 15.0)
        self.assertEqual(result["correctness_checks"], 7)

    def test_offline_estimate_cannot_be_promoted(self):
        evidence = make_evidence()
        evidence["evidence_class"] = "offline_schedule_estimate"
        with self.assertRaisesRegex(EvidenceError, "measured_cli_replay"):
            validate_replay_evidence(PLAN, evidence)

    def test_missing_callback_is_blocking(self):
        evidence = make_evidence()
        del evidence["runs"][1]["nodes"][0]["callback"]
        with self.assertRaisesRegex(EvidenceError, "callback"):
            validate_replay_evidence(PLAN, evidence)

    def test_wrong_scheduled_wave_is_blocking(self):
        evidence = make_evidence()
        evidence["runs"][1]["nodes"][1]["wave"] = 99
        with self.assertRaisesRegex(EvidenceError, "wave"):
            validate_replay_evidence(PLAN, evidence)

    def test_external_api_or_replacement_is_blocking(self):
        evidence = make_evidence()
        evidence["external_model_calls"] = 1
        with self.assertRaisesRegex(EvidenceError, "external_model_calls"):
            validate_replay_evidence(PLAN, evidence)
        evidence = make_evidence()
        evidence["runs"][0]["nodes"][0]["replacement_session"] = True
        with self.assertRaisesRegex(EvidenceError, "replacement_session"):
            validate_replay_evidence(PLAN, evidence)

    def test_below_target_is_fail_even_with_complete_receipts(self):
        evidence = make_evidence()
        evidence["runs"][1]["finished_at"] = stamp(
            datetime(2026, 10, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=75)
        )
        result = validate_replay_evidence(PLAN, evidence)
        self.assertEqual(result["status"], "HISTORICAL_ONLY")
        self.assertFalse(result["meets_target"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
