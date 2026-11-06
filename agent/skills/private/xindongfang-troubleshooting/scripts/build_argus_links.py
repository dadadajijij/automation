#!/usr/bin/env python3
"""Build read-only Argus browser links from locally saved, verified API results."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
BASE_URL = "https://argus.agoralab.co/call/qoe"


def success_data(payload: Any) -> Any:
    if not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("error"):
        raise ValueError("successful Argus result required")
    status = payload.get("status")
    if type(status) is not int or not 200 <= status < 300:
        raise ValueError("successful HTTP status required")
    return payload.get("data")


def timestamp(value: Any) -> int:
    if type(value) is not int or not 0 < value < 100_000_000_000:
        raise ValueError("Unix integer seconds required")
    datetime.fromtimestamp(value, SHANGHAI)
    return value


def uid_text(value: Any) -> str:
    if type(value) not in (str, int):
        raise ValueError("UID must be a string or integer")
    text = str(value)
    if not text or len(text) > 128 or text != text.strip() or any(ord(c) < 32 for c in text):
        raise ValueError("invalid UID")
    return text


def build_links(calls_payload: Any, users_by_call: Any, uids: list[str]) -> dict[str, Any]:
    data = success_data(calls_payload)
    if not isinstance(data, dict):
        raise ValueError("call object or callSessions object required")
    calls = [data] if "callId" in data else data.get("callSessions")
    if not isinstance(calls, list) or not calls or not isinstance(users_by_call, dict):
        raise ValueError("calls and per-call users required")
    if not isinstance(uids, list) or not uids:
        raise ValueError("target UIDs required")
    requested = list(dict.fromkeys(uid_text(uid) for uid in uids))
    links = []
    seen = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("invalid call")
        cid = call.get("callId")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", cid) or cid in seen:
            raise ValueError("unique callId required")
        seen.add(cid)
        users_payload = users_by_call.get(cid)
        users_data = success_data(users_payload)
        request = users_payload.get("request") or {}
        expected = f"/api/v1/call-sessions/{quote(cid, safe='')}/user-sessions"
        if request.get("method") != "GET" or request.get("path") != expected:
            raise ValueError("users response must belong to this callId")
        if not isinstance(users_data, dict) or not isinstance(users_data.get("userSessions"), list):
            raise ValueError("userSessions required")
        start = timestamp(call.get("createdTs"))
        destroyed = call.get("destroyedTs")
        ongoing = destroyed in (None, 0) and call.get("finished") is False
        if ongoing:
            end = timestamp((request.get("query") or {}).get("toTs"))
        else:
            end = timestamp(destroyed)
        if end <= start:
            raise ValueError("call end must follow call start")
        matched = set()
        for row in users_data["userSessions"]:
            if not isinstance(row, dict) or "uid" not in row:
                continue
            uid = uid_text(row["uid"])
            if uid not in requested:
                continue
            if not isinstance(row.get("sid"), str) or not row["sid"]:
                raise ValueError("verified SID required for target UID")
            joined = timestamp(row.get("joinTs"))
            left = row.get("leaveTs")
            left = end if left == 0 else timestamp(left)
            if joined <= end and left >= start and left >= joined:
                matched.add(uid)
        if not set(requested) <= matched:
            raise ValueError("target UID not verified in this call's time range")
        params = {"id": cid, "source": "normal", "rangeFromTs": start, "rangeToTs": end, "uids": ",".join(requested)}
        links.append({
            "call_id": cid, "uids": requested,
            "from_ts": start, "to_ts": end, "ongoing": ongoing,
            "start_shanghai": datetime.fromtimestamp(start, SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "end_shanghai": datetime.fromtimestamp(end, SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "url": BASE_URL + "?" + urlencode(params, safe=","),
        })
    return {"ok": True, "timezone": "Asia/Shanghai", "links": sorted(links, key=lambda row: (row["from_ts"], row["call_id"]))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--users", type=Path, required=True)
    parser.add_argument("--uids", required=True, help="Exact RTC UIDs separated by commas")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = build_links(
            json.loads(args.calls.read_text(encoding="utf-8")),
            json.loads(args.users.read_text(encoding="utf-8")),
            [uid.strip() for uid in args.uids.split(",")],
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, AttributeError):
        print(json.dumps({"ok": False, "error": "Invalid or unverified call, UID, time, users response or file path"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
