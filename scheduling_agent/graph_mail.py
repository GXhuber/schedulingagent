"""Email provider backed by the agent's own Microsoft 365 mailbox via Graph (app-only auth).

Unread inbox messages are the work queue. The rest of each conversation (including the agent's
own sent replies) is pulled by conversationId for context. Replies go through
createReplyAll -> patch recipients/body -> send so the thread headers stay intact and the
recipient list is exactly what `build_reply` computed.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .email_client import html_to_text
from .graph_client import GraphClient, GraphError
from .models import EmailMessage, EmailThread, OutgoingEmail

log = logging.getLogger(__name__)

_SELECT = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,body,uniqueBody,isDraft"
)
_TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}
_AUTO_HEADERS = {"auto-submitted", "x-autoreply", "x-autorespond", "x-auto-response-suppress"}


class GraphMailProvider:
    def __init__(self, client: GraphClient, *, mailbox: str) -> None:
        self.client = client
        self.mailbox = mailbox.lower()
        self.base = f"/users/{mailbox}"

    def fetch_unprocessed_threads(self, limit: int) -> list[EmailThread]:
        unread = self.client.get_all(
            f"{self.base}/mailFolders/inbox/messages",
            params={"$filter": "isRead eq false", "$select": _SELECT, "$top": "50"},
            headers=_TEXT_BODY,
        )
        unread.sort(key=lambda m: m["receivedDateTime"])
        threads: dict[str, EmailThread] = {}
        for item in unread:
            if len(threads) >= limit and item["conversationId"] not in threads:
                break
            msg = self._to_message(item, is_auto=self._is_auto(item["id"]))
            thread = threads.get(msg.thread_id)
            if thread is None:
                thread = EmailThread(thread_id=msg.thread_id, messages=self._history(msg.thread_id))
                threads[msg.thread_id] = thread
            # The history query returns this message too; keep the copy with header analysis.
            thread.messages = [m for m in thread.messages if m.uid != msg.uid] + [msg]
            thread.messages.sort(key=lambda m: m.date)
        return list(threads.values())

    def _history(self, conversation_id: str) -> list[EmailMessage]:
        items = self.client.get_all(
            f"{self.base}/messages",
            params={
                "$filter": f"conversationId eq '{conversation_id}'",
                "$select": _SELECT,
                "$top": "50",
            },
            headers=_TEXT_BODY,
        )
        return [self._to_message(i) for i in items if not i.get("isDraft")]

    def _is_auto(self, message_id: str) -> bool:
        data = self.client.get(f"{self.base}/messages/{message_id}", params={"$select": "internetMessageHeaders"})
        for h in data.get("internetMessageHeaders") or []:
            name = (h.get("name") or "").lower()
            if name in _AUTO_HEADERS or (name == "precedence" and (h.get("value") or "").lower() in {"bulk", "junk", "list"}):
                return True
        return False

    def send(self, message: OutgoingEmail, thread: EmailThread) -> None:
        draft = self.client.post(f"{self.base}/messages/{thread.latest.uid}/createReplyAll", json={})
        if not draft or "id" not in draft:
            raise RuntimeError("createReplyAll did not return a draft")
        self.client.patch(
            f"{self.base}/messages/{draft['id']}",
            json={
                "subject": message.subject,
                "toRecipients": _recipients(message.to),
                "ccRecipients": _recipients(message.cc),
                "body": {"contentType": "text", "content": message.body},
            },
        )
        self.client.post(f"{self.base}/messages/{draft['id']}/send")
        log.info("Sent reply to %s (cc %s): %s", message.to, message.cc, message.subject)

    def close(self) -> None:
        return None

    def mark_processed(self, thread: EmailThread, *, failed: bool = False) -> None:
        for m in thread.messages:
            if m.sender == self.mailbox:
                continue
            patch: dict = {"isRead": True}
            if failed:
                patch["flag"] = {"flagStatus": "flagged"}
            try:
                self.client.patch(f"{self.base}/messages/{m.uid}", json=patch)
            except GraphError as exc:  # history items may have been moved; not fatal
                log.warning("Could not mark %s processed: %s", m.uid, exc)

    def _to_message(self, item: dict, *, is_auto: bool = False) -> EmailMessage:
        body_obj = item.get("uniqueBody") or item.get("body") or {}
        content = body_obj.get("content") or ""
        if (body_obj.get("contentType") or "").lower() == "html":
            content = html_to_text(content)
        sender = ((item.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        return EmailMessage(
            uid=item["id"],
            message_id=item.get("internetMessageId") or f"<{item['id']}@graph>",
            thread_id=item["conversationId"],
            subject=(item.get("subject") or "").strip(),
            sender=sender,
            to=_addresses(item.get("toRecipients")),
            cc=_addresses(item.get("ccRecipients")),
            date=datetime.fromisoformat(item["receivedDateTime"]),
            body=content.strip(),
            is_auto_generated=is_auto,
        )


def _addresses(recipients: list | None) -> tuple[str, ...]:
    return tuple(
        (r.get("emailAddress") or {}).get("address", "").lower()
        for r in recipients or []
        if (r.get("emailAddress") or {}).get("address")
    )


def _recipients(addresses: tuple[str, ...]) -> list[dict]:
    return [{"emailAddress": {"address": a}} for a in addresses]
