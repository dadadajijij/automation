from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_http_gateway.main import _normalize_skill_audit, _runtime_audit_with_private_skills, _request_event_payload
from hermes_http_gateway.hermes_client import _read_skill_audit_file


class SkillAuditTests(unittest.TestCase):
    def test_selected_skills_do_not_overwrite_execution_telemetry(self):
        selected = [{"id": "demo", "source": "user_binding"}]
        result = _runtime_audit_with_private_skills(
            {"gateway_audit": {"skills": [{"id": "demo", "status": "succeeded"}]}}, selected,
        )
        self.assertEqual(result["skills"], selected)
        self.assertEqual(result["skill_audit"]["skills"][0]["status"], "succeeded")
        self.assertNotIn("private_skills", result)

    def test_historical_selection_key_is_normalized_without_mutating_record(self):
        audit = {"private_skills": [{"id": "demo"}], "skill_audit": {"skills": []}}
        row = dict.fromkeys([
            "id", "external_session_id", "user_id", "prompt", "status", "http_status",
            "error_detail", "model", "provider", "created_at", "completed_at",
        ])
        row["runtime_audit"] = audit
        result = _request_event_payload(row)["runtime_audit"]
        self.assertEqual(result["skills"], [{"id": "demo"}])
        self.assertNotIn("private_skills", result)
        self.assertIn("private_skills", audit)

    def test_missing_telemetry_is_unknown(self):
        audit = _normalize_skill_audit(
            None,
            [{"id": "agora-bailian-rag", "source": "user_binding"}],
        )
        self.assertEqual(audit["telemetry"], "missing")
        self.assertEqual(audit["skills"][0]["status"], "unknown")

    def test_normalizes_events_and_adds_unreported_selected_skill(self):
        audit = _normalize_skill_audit(
            {
                "version": 1,
                "events": [
                    {
                        "skill_id": "agora-bailian-rag",
                        "status": "succeeded",
                        "duration_ms": 12.4,
                        "summary": "three services returned",
                    }
                ],
            },
            [{"id": "agora-support-investigation-reports"}],
        )
        self.assertEqual(audit["telemetry"], "received")
        self.assertEqual(audit["skills"][0]["status"], "succeeded")
        self.assertEqual(audit["skills"][1]["status"], "unknown")

    def test_reads_jsonl_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill-audit.jsonl"
            path.write_text(
                json.dumps({"skill_id": "demo", "status": "failed"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(_read_skill_audit_file(path)["events"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
