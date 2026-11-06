import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xdf_argus_links", ROOT / "scripts/build_argus_links.py")
links = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(links)


def fixtures():
    call = {"callId": "call-abc", "createdTs": 1792824125, "destroyedTs": 1792824455, "finished": True}
    calls = {"ok": True, "status": 200, "error": None, "data": {"callSessions": [call]}}
    users = {"call-abc": {
        "ok": True, "status": 200, "error": None,
        "request": {"method": "GET", "path": "/api/v1/call-sessions/call-abc/user-sessions", "query": {"fromTs": 1792824125, "toTs": 1792824455}},
        "data": {"userSessions": [{"uid": 70000001, "sid": "A" * 32, "joinTs": 1792824125, "leaveTs": 1792824455}]},
    }}
    return calls, users


class ArgusLinkTests(unittest.TestCase):
    def test_actual_lifecycle_uid_and_shanghai_time(self):
        calls, users = fixtures()
        result = links.build_links(calls, users, ["70000001"])
        row = result["links"][0]
        self.assertEqual(row["start_shanghai"], "2026-10-24 14:42:05")
        self.assertEqual(row["end_shanghai"], "2026-10-24 14:47:35")
        query = parse_qs(urlparse(row["url"]).query)
        self.assertEqual(query, {"id": ["call-abc"], "source": ["normal"], "rangeFromTs": ["1792824125"], "rangeToTs": ["1792824455"], "uids": ["70000001"]})
        self.assertFalse(row["ongoing"])

    def test_single_get_call_and_multiple_sorted_calls(self):
        calls, users = fixtures()
        call = calls["data"]["callSessions"][0]
        self.assertEqual(len(links.build_links({**calls, "data": call}, users, ["70000001"])["links"]), 1)
        older = {**call, "callId": "call-earlier", "createdTs": call["createdTs"] - 600, "destroyedTs": call["destroyedTs"] - 600}
        users["call-earlier"] = copy.deepcopy(users["call-abc"])
        users["call-earlier"]["request"]["path"] = "/api/v1/call-sessions/call-earlier/user-sessions"
        row = users["call-earlier"]["data"]["userSessions"][0]
        row.update(joinTs=older["createdTs"], leaveTs=older["destroyedTs"])
        calls["data"]["callSessions"].append(older)
        self.assertEqual([r["call_id"] for r in links.build_links(calls, users, ["70000001"])["links"]], ["call-earlier", "call-abc"])

    def test_reject_unverified_uid_and_cross_call_users(self):
        calls, users = fixtures()
        with self.assertRaises(ValueError):
            links.build_links(calls, users, ["70000002"])
        users["call-abc"]["request"]["path"] = "/api/v1/call-sessions/other-call/user-sessions"
        with self.assertRaises(ValueError):
            links.build_links(calls, users, ["70000001"])

    def test_reject_failed_missing_or_inverted_millisecond_times(self):
        for changes in ({"createdTs": 1792824125000}, {"destroyedTs": 1}, {"destroyedTs": 0}, {"createdTs": None}, {"createdTs": True}):
            with self.subTest(changes=changes):
                calls, users = fixtures()
                calls["data"]["callSessions"][0].update(changes)
                with self.assertRaises(ValueError):
                    links.build_links(calls, users, ["70000001"])
        calls, users = fixtures()
        users["call-abc"]["ok"] = False
        with self.assertRaises(ValueError):
            links.build_links(calls, users, ["70000001"])

    def test_ongoing_uses_saved_query_end(self):
        calls, users = fixtures()
        calls["data"]["callSessions"][0].update(destroyedTs=0, finished=False)
        users["call-abc"]["data"]["userSessions"][0]["leaveTs"] = 0
        row = links.build_links(calls, users, ["70000001"])["links"][0]
        self.assertTrue(row["ongoing"])
        self.assertEqual(row["to_ts"], 1792824455)
        del users["call-abc"]["request"]["query"]["toTs"]
        with self.assertRaises(ValueError):
            links.build_links(calls, users, ["70000001"])

    def test_only_verified_uids_and_no_credentials_in_url(self):
        calls, users = fixtures()
        users["call-abc"]["request"]["headers"] = {"apikey": "EXAMPLE_SECRET"}
        users["call-abc"]["data"]["userSessions"].append({"uid": 70000002, "sid": "B" * 32, "joinTs": 1792824125, "leaveTs": 1792824455})
        result = links.build_links(calls, users, ["70000001", "70000002", "70000001"])
        self.assertEqual(parse_qs(urlparse(result["links"][0]["url"]).query)["uids"], ["70000001,70000002"])
        self.assertNotIn("EXAMPLE_SECRET", json.dumps(result))
        self.assertNotIn("headers", json.dumps(result))

    def test_cli_writes_verified_links_and_fails_explicitly(self):
        calls, users = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            c, u, out = directory / "calls.json", directory / "users.json", directory / "links.json"
            c.write_text(json.dumps(calls))
            u.write_text(json.dumps(users))
            command = [sys.executable, str(ROOT / "scripts/build_argus_links.py"), "--calls", str(c), "--users", str(u), "--uids", "70000001"]
            result = subprocess.run(command + ["--output", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), json.loads(out.read_text()))
            users["call-abc"]["ok"] = False
            u.write_text(json.dumps(users))
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertFalse(json.loads(failed.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
