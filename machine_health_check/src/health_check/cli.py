from __future__ import annotations

import argparse
import json

from .config import load_config
from .notifiers import send_wecom_message
from .runner import generate_report, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Linux server machine health check")
    parser.add_argument(
        "--config",
        default="config/default.json",
        help="Path to config file",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write report files",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(args.config)
    report = generate_report(config)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))

    if args.no_write:
        return 0

    write_report(
        report=report,
        report_dir=str(config.get("report_dir", "reports")),
        history_dir=str(config.get("history_dir", "reports/history")),
    )

    notifications = dict(config.get("notifications", {}))
    if notifications.get("enabled"):
        webhook_url = str(notifications.get("wecom_webhook", "")).strip()
        if webhook_url:
            success, message = send_wecom_message(webhook_url, report)
            print(
                json.dumps(
                    {
                        "notification_sent": success,
                        "notification_message": message,
                    },
                    ensure_ascii=False,
                )
            )

    return 0
