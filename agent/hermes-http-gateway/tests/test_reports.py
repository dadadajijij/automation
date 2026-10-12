from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from hermes_http_gateway import main as gateway
from hermes_http_gateway import db
from hermes_http_gateway import reports
from hermes_http_gateway.hermes_client import HermesRunResult
from tests.test_gateway import _chat_request, _init_temp_db, _make_settings


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = replace(
            _make_settings(root),
            report_source_root=root / "documents",
            report_storage_root=root / "reports",
            report_public_base_url="https://athena.agoralab.co/tools2/hermes-gateway",
            gateway_api_key="required-for-chat",
        )
        self.settings.report_source_root.mkdir()
        self.source = self.settings.report_source_root / "argus-investigation-example.html"
        self.contents = b"<!doctype html><h1>Investigation report</h1>"
        self.source.write_bytes(self.contents)

    def publish(self, answer=None, settings=None):
        return reports.publish_report_answer(
            settings or self.settings,
            answer or f"详细排查报告：\n{self.source}",
            user_id="guest",
            session_id="report-session",
            request_id="request-1",
        )

    def http_get(self, path):
        async def request():
            sent = []

            async def send(message):
                sent.append(message)

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "https", "path": path,
                "raw_path": path.encode(), "root_path": "", "query_string": b"",
                "headers": [], "client": ("127.0.0.1", 1), "server": ("test", 443),
            }
            with mock.patch.object(gateway.app.state, "settings", self.settings, create=True):
                await gateway.app(scope, receive, send)
            start = next(item for item in sent if item["type"] == "http.response.start")
            body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
            return start["status"], dict(start["headers"]), body

        return asyncio.run(request())

    def test_report_page_without_gateway_key_after_source_is_deleted(self):
        answer = self.publish(f"详细排查报告：[报告]({self.source})")
        link = answer.split("(")[1].rstrip(")")
        self.assertTrue(link.startswith(self.settings.report_public_base_url))
        self.assertIn(f"/v1/reports/{self.source.name}/", link)
        self.assertNotIn(str(self.source), answer)
        self.source.unlink()
        status, headers, body = self.http_get(link.removeprefix(self.settings.report_public_base_url))
        self.assertEqual(status, 200)
        self.assertEqual(body, self.contents)
        self.assertEqual(headers[b"content-type"], b"text/html; charset=utf-8")
        self.assertEqual(headers[b"content-disposition"], b"inline")
        self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
        self.assertEqual(headers[b"cache-control"], b"no-store")

    def test_filename_must_match_token(self):
        answer = self.publish()
        path = answer.splitlines()[-1].removeprefix(self.settings.report_public_base_url)
        token = path.rsplit("/", 1)[-1]
        self.assertEqual(self.http_get(path.replace(self.source.name, "other.html"))[0], 404)
        self.assertEqual(self.http_get(f"/v1/reports/{token}/download")[0], 404)

    def test_shared_chat_persists_and_returns_same_public_answer(self):
        _init_temp_db(self.settings)
        run = HermesRunResult(
            answer=f"结论：已完成排查。\n详细排查报告：\n{self.source}",
            session_id="hermes-report", stdout="", stderr="", returncode=0,
            usage={"session_id": "hermes-report"},
        )
        with (
            mock.patch.object(gateway, "run_first_turn", return_value=run),
            mock.patch.object(gateway, "list_private_skills", return_value=[]),
        ):
            result = asyncio.run(gateway._run_chat_turn(
                self.settings, user_id="guest", session_id="report-session",
                request=_chat_request("请生成报告"),
            ))
        stored = db.list_messages(self.settings, "guest", "report-session")
        self.assertEqual(stored[-1]["content"], result["answer"])
        self.assertIn("https://athena.agoralab.co/tools2/hermes-gateway/v1/reports/", result["answer"])
        self.assertNotIn(str(self.source), result["answer"])

    def test_expired_and_unknown_links_return_404_and_are_cleaned(self):
        answer = self.publish()
        path = answer.splitlines()[-1].removeprefix(self.settings.report_public_base_url)
        self.assertEqual(self.http_get(f"/v1/reports/{self.source.name}/" + "0" * 64)[0], 404)
        with mock.patch.object(reports.time, "time", return_value=time.time() + 8 * 86400):
            self.assertEqual(self.http_get(path)[0], 404)
            reports.cleanup_reports(self.settings)
        self.assertEqual(list(self.settings.report_storage_root.iterdir()), [])

    def test_missing_report_keeps_conclusion_and_removes_local_reference(self):
        self.source.unlink()
        with self.assertLogs(reports.logger, level="WARNING"):
            answer = self.publish(f"结论：网络卡顿。\n详细排查报告：\n{self.source}")
        self.assertIn("结论：网络卡顿。", answer)
        self.assertIn("报告暂不可用", answer)
        self.assertNotIn(str(self.source), answer)

    def test_paths_escaping_report_root_are_not_published(self):
        outside = Path(self.temp.name) / "outside.html"
        outside.write_bytes(b"private")
        self.source.unlink()
        self.source.symlink_to(outside)
        for reference in (str(self.source), str(self.settings.report_source_root / "../outside.html")):
            with self.assertLogs(reports.logger, level="WARNING"):
                answer = self.publish(f"详细排查报告：\n{reference}")
            self.assertNotIn("/v1/reports/", answer)
            self.assertIn("报告暂不可用", answer)

    def test_size_limit_and_no_report_reply(self):
        with self.assertLogs(reports.logger, level="WARNING"):
            answer = self.publish(settings=replace(self.settings, report_max_bytes=1))
        self.assertIn("报告暂不可用", answer)
        self.assertEqual(self.publish("普通回答，没有报告。"), "普通回答，没有报告。")

    def test_superseded_turn_does_not_save_answer_during_publication(self):
        _init_temp_db(self.settings)
        started = threading.Event()
        release = threading.Event()

        def publish(settings, answer, **kwargs):
            if answer == "old answer":
                started.set()
                release.wait(timeout=3)
            return answer

        def run(settings, *, prompt, **kwargs):
            return HermesRunResult(
                f"{prompt} answer", "hermes-report", "", "", 0,
                {"session_id": "hermes-report"},
            )

        async def scenario():
            old = asyncio.create_task(gateway._run_chat_turn(
                self.settings, user_id="guest", session_id="report-race",
                request=_chat_request("old"),
            ))
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            new = asyncio.create_task(gateway._run_chat_turn(
                self.settings, user_id="guest", session_id="report-race",
                request=_chat_request("new"),
            ))
            await asyncio.sleep(0.05)
            release.set()
            old_result, new_result = await asyncio.gather(old, new, return_exceptions=True)
            self.assertEqual(old_result.status_code, 409)
            self.assertEqual(new_result["answer"], "new answer")

        with (
            mock.patch.object(gateway, "publish_report_answer", side_effect=publish),
            mock.patch.object(gateway, "run_first_turn", side_effect=run),
            mock.patch.object(gateway, "list_private_skills", return_value=[]),
        ):
            try:
                asyncio.run(scenario())
            finally:
                release.set()
        messages = db.list_messages(self.settings, "guest", "report-race")
        self.assertEqual([item["content"] for item in messages], ["new", "new answer"])


if __name__ == "__main__":
    unittest.main()
