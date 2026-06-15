from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as file_obj:
        config = json.load(file_obj)

    notifications = dict(config.get("notifications", {}))
    if not notifications.get("wecom_webhook"):
        notifications["wecom_webhook"] = os.getenv("MACHINE_HEALTH_CHECK_WECOM_WEBHOOK", "")
        config["notifications"] = notifications

    return config
