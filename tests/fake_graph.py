from __future__ import annotations

from typing import Any


class FakeGraphClient:
    """Records calls; answers GETs from a {path_substring: payload} table."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next_post: dict[str, Any] = {}

    def _lookup(self, path: str, params: dict | None) -> Any:
        key = path + ("?" + "&".join(f"{k}={v}" for k, v in (params or {}).items()) if params else "")
        for needle, payload in self.responses.items():
            if needle in key:
                return payload
        return {"value": []}

    def get(self, path, params=None, headers=None):
        self.calls.append(("GET", path, params))
        return self._lookup(path, params)

    def get_all(self, path, params=None, headers=None):
        self.calls.append(("GET_ALL", path, params))
        data = self._lookup(path, params)
        return data.get("value", []) if isinstance(data, dict) else data

    def post(self, path, json=None, headers=None):
        self.calls.append(("POST", path, json))
        for needle, payload in self.next_post.items():
            if needle in path:
                return payload
        return None

    def patch(self, path, json):
        self.calls.append(("PATCH", path, json))
