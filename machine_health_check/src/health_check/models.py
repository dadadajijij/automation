from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckItem:
    name: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckReport:
    host: str
    generated_at: str
    overall_status: str
    items: list[CheckItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
            "items": [
                {
                    "name": item.name,
                    "status": item.status,
                    "summary": item.summary,
                    "details": item.details,
                }
                for item in self.items
            ],
        }

    def summary_items(self) -> list[CheckItem]:
        return [item for item in self.items if item.status != "ok"]
