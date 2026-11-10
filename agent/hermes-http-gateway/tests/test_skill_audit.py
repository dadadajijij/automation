from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_http_gateway.main import (
    _detect_executed_skills,
    _normalize_skill_audit,
    _runtime_audit_with_private_skills,
    _request_event_payload,
    _strip_internal_runtime_diagnostics,
)
from hermes_http_gateway.hermes_client import _read_skill_audit_file
from hermes_http_gateway.main import _timing_stages


class SkillAuditTests(unittest.TestCase):
    def test_detected_auxiliary_skill_is_published_separately(self):
        usage = {"gateway_audit": {"skills": [{"id": "agora-wecom-support-assistant", "status": "succeeded"}]}}
        result = _runtime_audit_with_private_skills(
            usage,
            [{"id": "agora-wecom-support-assistant", "source": "gateway_default"}],
            executed_skills=[{
                "id": "agora-bailian-rag",
                "status": "succeeded",
                "source": "hermes_tool_call",
            }],
        )
        self.assertEqual(result["skills"][0]["id"], "agora-wecom-support-assistant")
        self.assertIn(
            "agora-bailian-rag",
            [item["id"] for item in result["executed_skills"]],
        )
        self.assertIn(
            "agora-bailian-rag",
            [item["id"] for item in result["skill_audit"]["skills"]],
        )

    def test_internal_file_mutation_footer_is_hidden_and_audited(self):
        answer, diagnostic = _strip_internal_runtime_diagnostics(
            "结论：调查完成。\n\n⚠️ File-mutation verifier: 1 file(s) were NOT modified this turn.\n  • `/tmp/report.json` — [write_file] syntax validation failed"
        )
        self.assertEqual(answer, "结论：调查完成。")
        self.assertEqual(diagnostic["customer_visible"], False)
        self.assertEqual(diagnostic["source"], "hermes_file_mutation_verifier")

    def test_normal_answer_is_unchanged_by_runtime_diagnostic_filter(self):
        answer = "结论：没有发现持续异常。"
        self.assertEqual(_strip_internal_runtime_diagnostics(answer), (answer, None))

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

    def test_timing_stages_expose_model_and_tool_breakdown(self):
        stages = _timing_stages({
            "model_wait_ms": 1_069_789,
            "tool_execution_ms": 92_667.8,
            "cli_overhead_ms": 9_100,
            "model_calls": 30,
            "tool_calls": 39,
        })
        self.assertEqual([item["stage"] for item in stages], [
            "模型决策等待", "Hermes 工具执行", "Hermes CLI 初始化/收尾"
        ])
        self.assertEqual(stages[0]["detail"], "30 次模型调用")

    def test_runtime_timing_contains_ordered_segments(self):
        from hermes_http_gateway.main import _hermes_runtime_breakdown
        from hermes_http_gateway.config import Settings
        from pathlib import Path
        settings = Settings(
            host="127.0.0.1", port=1, database_url=Path("/tmp/test.sqlite"),
            hermes_bin="hermes", hermes_workdir=Path("/tmp"), hermes_profile_default="default",
            gateway_api_key=None, admin_username="admin", admin_password=None,
            admin_cookie_name="x", admin_session_ttl_seconds=60, admin_cookie_secure=False,
            public_base_path="", timeout_seconds=1, source_tag="tool", default_model=None,
            default_provider=None, attachment_max_bytes=1, attachment_download_timeout_seconds=1,
            attachment_max_images=1, attachment_redirect_limit=1, attachment_storage_root=Path("/tmp"),
            attachment_retention_days=1,
        )
        timing = _hermes_runtime_breakdown(settings, "20261109_015945_52df3f")
        self.assertTrue(timing["segments"])
        self.assertLessEqual(
            timing["segments"][0]["start_ts"], timing["segments"][-1]["end_ts"]
        )


if __name__ == "__main__":
    unittest.main()
