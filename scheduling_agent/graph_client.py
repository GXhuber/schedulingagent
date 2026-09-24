"""Microsoft Graph HTTP client with two auth modes.

app       - client credentials (ConfidentialClientApplication). Recommended when the agent has its
            own mailbox in the firm's tenant: no interactive sign-in, no refresh-token expiry.
            IT scopes the app to specific mailboxes with an Exchange Application Access Policy.
delegated - device-code sign-in as the owner (PublicClientApplication + serialised token cache).
            Calendar only; pair with the IMAP email backend.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msal
import requests

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
DELEGATED_SCOPES = ["Calendars.ReadWrite", "User.Read"]
APP_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphError(RuntimeError):
    pass


class TokenCacheStore:
    """Persists the MSAL token cache for delegated mode (contains a refresh token: keep secret)."""

    def __init__(self, path: str | Path, b64: str = "") -> None:
        self.path = Path(path)
        self.b64 = b64

    def load(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if self.b64:
            cache.deserialize(base64.b64decode(self.b64).decode())
        elif self.path.exists():
            cache.deserialize(self.path.read_text())
        return cache

    def save(self, cache: msal.SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        try:
            self.path.write_text(cache.serialize())
        except OSError as exc:
            log.warning("Could not persist Graph token cache to %s: %s", self.path, exc)


def app_only_token_provider(client_id: str, tenant_id: str, client_secret: str) -> Callable[[], str]:
    if tenant_id in {"", "organizations", "common"}:
        raise GraphError("GRAPH_TENANT_ID must be the directory (tenant) ID for app-only auth")
    app = msal.ConfidentialClientApplication(
        client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )

    def get_token() -> str:
        result = app.acquire_token_for_client(scopes=APP_SCOPES)
        if "access_token" not in result:
            raise GraphError(result.get("error_description") or str(result))
        return result["access_token"]

    return get_token


def delegated_token_provider(
    client_id: str, tenant_id: str, cache_store: TokenCacheStore
) -> Callable[[], str]:
    cache = cache_store.load()
    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", token_cache=cache
    )

    def get_token() -> str:
        accounts = app.get_accounts()
        result = app.acquire_token_silent(DELEGATED_SCOPES, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise GraphError(
                "No cached Graph credentials. Run `scheduling-agent auth-graph` locally and deploy "
                "the resulting token cache."
            )
        cache_store.save(cache)
        return result["access_token"]

    return get_token


def device_code_login(client_id: str, tenant_id: str, cache_store: TokenCacheStore) -> str:
    """Interactive one-time bootstrap for delegated mode. Returns the signed-in username."""
    cache = cache_store.load()
    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", token_cache=cache
    )
    flow = app.initiate_device_flow(scopes=DELEGATED_SCOPES)
    if "user_code" not in flow:
        raise GraphError(f"Device flow failed: {flow.get('error_description', flow)}")
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise GraphError(result.get("error_description", str(result)))
    cache_store.save(cache)
    return result.get("id_token_claims", {}).get("preferred_username", "unknown")


class GraphClient:
    def __init__(self, token_provider: Callable[[], str], timeout: int = 30) -> None:
        self._token = token_provider
        self.timeout = timeout

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if extra:
            headers.update(extra)
        return headers

    def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{GRAPH}{path}"
        resp = requests.get(url, headers=self._headers(headers), params=params, timeout=self.timeout)
        _raise(resp)
        return resp.json()

    def get_all(self, path: str, params: dict | None = None, headers: dict | None = None) -> list[dict]:
        items: list[dict] = []
        url: str | None = path
        while url:
            data = self.get(url, params=params, headers=headers)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
        return items

    def post(self, path: str, json: dict | None = None, headers: dict | None = None) -> dict[str, Any] | None:
        resp = requests.post(f"{GRAPH}{path}", headers=self._headers(headers), json=json, timeout=self.timeout)
        _raise(resp)
        return resp.json() if resp.content else None

    def patch(self, path: str, json: dict) -> dict[str, Any] | None:
        resp = requests.patch(f"{GRAPH}{path}", headers=self._headers(), json=json, timeout=self.timeout)
        _raise(resp)
        return resp.json() if resp.content else None


def _raise(resp: requests.Response) -> None:
    if resp.ok:
        return
    try:
        detail = resp.json().get("error", {})
        msg = f"{detail.get('code')}: {detail.get('message')}"
    except ValueError:
        msg = resp.text[:500]
    raise GraphError(f"Graph {resp.request.method} {resp.url} -> {resp.status_code} {msg}")
