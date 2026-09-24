from __future__ import annotations

import email
import email.utils
import html
import imaplib
import logging
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from email.policy import default as default_policy
from typing import Protocol

from .models import EmailMessage, EmailThread, OutgoingEmail

log = logging.getLogger(__name__)

_AUTO_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


class EmailProvider(Protocol):
    def fetch_unprocessed_threads(self, limit: int) -> list[EmailThread]: ...

    def send(self, message: OutgoingEmail, thread: EmailThread) -> None: ...

    def mark_processed(self, thread: EmailThread, *, failed: bool = False) -> None: ...


def parse_addresses(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(addr.lower() for _, addr in email.utils.getaddresses([raw]) if addr)


def extract_body(msg: email.message.Message) -> str:
    plain: str | None = None
    markup: str | None = None
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode(part)
        elif ctype == "text/html" and markup is None:
            markup = _decode(part)
    if plain:
        return plain.strip()
    if markup:
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = _TAG_RE.sub("", text)
        return _WS_RE.sub("\n\n", html.unescape(text)).strip()
    return ""


def _decode(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def parse_message(raw: bytes, *, uid: str, thread_id: str | None = None) -> EmailMessage:
    msg = email.message_from_bytes(raw, policy=default_policy)
    date_hdr = msg.get("Date")
    try:
        date = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else datetime.now(UTC)
    except (TypeError, ValueError):
        date = datetime.now(UTC)
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    message_id = (msg.get("Message-ID") or f"<{uid}@local>").strip()
    references = tuple(r for r in (msg.get("References") or "").split() if r)
    in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
    is_auto = any(msg.get(h) for h in _AUTO_HEADERS) or (
        (msg.get("Precedence") or "").lower() in {"bulk", "junk", "list"}
    )
    sender = parse_addresses(msg.get("From"))
    return EmailMessage(
        uid=uid,
        message_id=message_id,
        thread_id=thread_id or (references[0] if references else message_id),
        subject=(msg.get("Subject") or "").strip(),
        sender=sender[0] if sender else "",
        to=parse_addresses(msg.get("To")),
        cc=parse_addresses(msg.get("Cc")),
        date=date,
        body=extract_body(msg),
        in_reply_to=in_reply_to,
        references=references,
        is_auto_generated=bool(is_auto),
    )


def build_reply(
    thread: EmailThread,
    *,
    agent_email: str,
    body: str,
    extra_cc: tuple[str, ...] = (),
) -> OutgoingEmail:
    """Reply-all to the latest message, excluding the agent itself."""
    latest = thread.latest
    to = _dedupe((latest.sender, *latest.to), exclude={agent_email})
    cc = _dedupe((*latest.cc, *extra_cc), exclude={agent_email, *to})
    subject = latest.subject if latest.subject.lower().startswith("re:") else f"Re: {latest.subject}"
    references = tuple(_dedupe((*latest.references, latest.message_id)))
    return OutgoingEmail(
        to=to,
        cc=cc,
        subject=subject,
        body=body,
        in_reply_to=latest.message_id,
        references=references,
    )


def _dedupe(addrs: tuple[str, ...], exclude: set[str] | None = None) -> tuple[str, ...]:
    seen: list[str] = []
    for a in addrs:
        a = a.lower()
        if a and a not in seen and a not in (exclude or set()):
            seen.append(a)
    return tuple(seen)


class ImapSmtpEmailProvider:
    """Gmail (or any IMAP/SMTP host) provider.

    Unseen messages are treated as unprocessed. Gmail's X-GM-THRID extension is used to pull the
    rest of the thread for context; on non-Gmail servers only the new message is available.
    """

    def __init__(
        self,
        *,
        address: str,
        password: str,
        display_name: str,
        imap_host: str,
        imap_port: int,
        smtp_host: str,
        smtp_port: int,
    ) -> None:
        self.address = address.lower()
        self.password = password
        self.display_name = display_name
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self._imap: imaplib.IMAP4_SSL | None = None
        self._is_gmail = "gmail" in imap_host or "googlemail" in imap_host

    def _conn(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            self._imap.login(self.address, self.password)
            self._imap.select("INBOX")
        return self._imap

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    def fetch_unprocessed_threads(self, limit: int) -> list[EmailThread]:
        conn = self._conn()
        status, data = conn.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        uids = data[0].split()[-limit * 3 :]
        threads: dict[str, EmailThread] = {}
        for uid in uids:
            uid_s = uid.decode()
            msg = self._fetch_message(uid_s)
            if msg is None:
                continue
            thread = threads.get(msg.thread_id)
            if thread is None:
                thread = EmailThread(thread_id=msg.thread_id, messages=self._thread_history(msg))
                threads[msg.thread_id] = thread
            if all(m.message_id != msg.message_id for m in thread.messages):
                thread.messages.append(msg)
            thread.messages.sort(key=lambda m: m.date)
        return list(threads.values())[:limit]

    def _fetch_message(self, uid: str) -> EmailMessage | None:
        conn = self._conn()
        items = "(X-GM-THRID BODY.PEEK[])" if self._is_gmail else "(BODY.PEEK[])"
        status, data = conn.uid("fetch", uid, items)
        if status != "OK" or not data or data[0] is None:
            return None
        header, raw = data[0][0], data[0][1]
        thread_id = None
        if self._is_gmail:
            m = re.search(rb"X-GM-THRID (\d+)", header)
            thread_id = m.group(1).decode() if m else None
        return parse_message(raw, uid=uid, thread_id=thread_id)

    def _thread_history(self, msg: EmailMessage) -> list[EmailMessage]:
        if not self._is_gmail:
            return []
        conn = self._conn()
        status, data = conn.uid("search", None, "X-GM-THRID", msg.thread_id)
        if status != "OK":
            return []
        history: list[EmailMessage] = []
        for uid in data[0].split():
            uid_s = uid.decode()
            if uid_s == msg.uid:
                continue
            prior = self._fetch_message(uid_s)
            if prior is not None:
                history.append(prior)
        return history

    def send(self, message: OutgoingEmail, thread: EmailThread) -> None:
        mime = MimeMessage()
        mime["From"] = email.utils.formataddr((self.display_name, self.address))
        mime["To"] = ", ".join(message.to)
        if message.cc:
            mime["Cc"] = ", ".join(message.cc)
        mime["Subject"] = message.subject
        mime["Date"] = email.utils.formatdate(localtime=True)
        mime["Message-ID"] = email.utils.make_msgid(domain=self.address.split("@")[-1])
        if message.in_reply_to:
            mime["In-Reply-To"] = message.in_reply_to
        if message.references:
            mime["References"] = " ".join(message.references)
        mime.set_content(message.body)
        with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as smtp:
            smtp.login(self.address, self.password)
            smtp.send_message(mime)
        log.info("Sent reply to %s (cc %s): %s", message.to, message.cc, message.subject)

    def mark_processed(self, thread: EmailThread, *, failed: bool = False) -> None:
        conn = self._conn()
        for m in thread.messages:
            conn.uid("store", m.uid, "+FLAGS", "(\\Seen)")
            if failed:
                conn.uid("store", m.uid, "+FLAGS", "(\\Flagged)")


class InMemoryEmailProvider:
    """Test/demo provider: threads are supplied up front, sends are recorded."""

    def __init__(self, threads: list[EmailThread] | None = None) -> None:
        self.threads = list(threads or [])
        self.sent: list[OutgoingEmail] = []
        self.processed: list[tuple[str, bool]] = []

    def fetch_unprocessed_threads(self, limit: int) -> list[EmailThread]:
        return self.threads[:limit]

    def send(self, message: OutgoingEmail, thread: EmailThread) -> None:
        self.sent.append(message)

    def mark_processed(self, thread: EmailThread, *, failed: bool = False) -> None:
        self.processed.append((thread.thread_id, failed))
        self.threads = [t for t in self.threads if t.thread_id != thread.thread_id]
