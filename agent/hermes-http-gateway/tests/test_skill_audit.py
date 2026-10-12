from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_http_gateway.main import _normalize_skill_audit
from hermes_http_gateway.hermes_client import _read_skill_audit_file


class SkillAuditTests(unittest.TestCase):
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
