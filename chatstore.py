"""Хранилище истории чатов: одна сессия — один JSON-файл в папке chats/."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class ChatStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in self.folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            messages = data.get("messages") or []
            sessions.append(
                {
                    "id": path.stem,
                    "title": str(data.get("title") or "Без названия"),
                    "updated": float(data.get("updated") or 0),
                    "count": len(messages),
                }
            )
        sessions.sort(key=lambda item: item["updated"], reverse=True)
        return sessions

    def save(self, session_id: str, title: str, messages: list[dict[str, Any]]) -> None:
        payload = {
            "id": session_id,
            "title": title,
            "updated": time.time(),
            "messages": messages,
        }
        (self.folder / f"{session_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def load(self, session_id: str) -> dict[str, Any] | None:
        path = self.folder / f"{session_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def delete(self, session_id: str) -> bool:
        path = self.folder / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
