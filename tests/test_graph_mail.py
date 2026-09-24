from scheduling_agent.graph_mail import GraphMailProvider
from scheduling_agent.models import EmailThread, OutgoingEmail

from .conftest import AGENT_M365, GUEST, OWNER, message
from .fake_graph import FakeGraphClient


def _msg(mid: str, sender: str, received: str, body: str, conv: str = "conv1", html: bool = False):
    return {
        "id": mid,
        "conversationId": conv,
        "internetMessageId": f"<{mid}@x>",
        "subject": "Intro",
        "from": {"emailAddress": {"address": sender.upper()}},
        "toRecipients": [{"emailAddress": {"address": OWNER}}],
        "ccRecipients": [{"emailAddress": {"address": AGENT_M365}}],
        "receivedDateTime": received,
        "uniqueBody": {"contentType": "html" if html else "text", "content": body},
        "body": {"contentType": "text", "content": "FULL " + body},
    }


def test_fetch_builds_threads_from_unread_plus_history():
    client = FakeGraphClient(
        {
            "/mailFolders/inbox/messages": {
                "value": [_msg("m2", GUEST, "2026-09-21T14:00:00Z", "<p>Tues &amp; Wed work</p>", html=True)]
            },
            "conversationId eq 'conv1'": {
                "value": [
                    _msg("m1", OWNER, "2026-09-21T13:00:00Z", "Looping in my scheduler"),
                    _msg("m2", GUEST, "2026-09-21T14:00:00Z", "dup"),
                    {**_msg("d1", AGENT_M365, "2026-09-21T15:00:00Z", "draft"), "isDraft": True},
                ]
            },
            "/messages/m2?$select=internetMessageHeaders": {
                "internetMessageHeaders": [{"name": "X-Mailer", "value": "Outlook"}]
            },
        }
    )
    provider = GraphMailProvider(client, mailbox=AGENT_M365)
    threads = provider.fetch_unprocessed_threads(limit=5)

    assert len(threads) == 1
    t = threads[0]
    assert t.thread_id == "conv1"
    assert [m.uid for m in t.messages] == ["m1", "m2"]
    assert t.latest.sender == GUEST
    assert t.latest.body == "Tues & Wed work"  # html stripped, uniqueBody preferred
    assert t.latest.date.isoformat() == "2026-09-21T14:00:00+00:00"
    assert not t.latest.is_auto_generated


def test_auto_reply_header_detected():
    client = FakeGraphClient(
        {
            "/mailFolders/inbox/messages": {"value": [_msg("m9", GUEST, "2026-09-21T14:00:00Z", "OOO")]},
            "/messages/m9?$select=internetMessageHeaders": {
                "internetMessageHeaders": [{"name": "Auto-Submitted", "value": "auto-replied"}]
            },
        }
    )
    threads = GraphMailProvider(client, mailbox=AGENT_M365).fetch_unprocessed_threads(5)
    assert threads[0].latest.is_auto_generated


def test_send_uses_reply_all_draft_with_exact_recipients():
    client = FakeGraphClient()
    client.next_post = {"createReplyAll": {"id": "draft1"}}
    provider = GraphMailProvider(client, mailbox=AGENT_M365)
    thread = EmailThread("conv1", [message(uid="m2", cc=(AGENT_M365,))])
    out = OutgoingEmail(
        to=(GUEST,), cc=(OWNER,), subject="Re: Intro", body="Options", in_reply_to="<m2@x>", references=()
    )
    provider.send(out, thread)

    methods = [(c[0], c[1]) for c in client.calls]
    assert methods == [
        ("POST", f"/users/{AGENT_M365}/messages/m2/createReplyAll"),
        ("PATCH", f"/users/{AGENT_M365}/messages/draft1"),
        ("POST", f"/users/{AGENT_M365}/messages/draft1/send"),
    ]
    patch = client.calls[1][2]
    assert patch["toRecipients"] == [{"emailAddress": {"address": GUEST}}]
    assert patch["ccRecipients"] == [{"emailAddress": {"address": OWNER}}]
    assert patch["body"] == {"contentType": "text", "content": "Options"}


def test_mark_processed_skips_agent_messages_and_flags_failures():
    client = FakeGraphClient()
    provider = GraphMailProvider(client, mailbox=AGENT_M365)
    thread = EmailThread(
        "conv1", [message(uid="a1", sender=AGENT_M365), message(uid="g1", sender=GUEST)]
    )
    provider.mark_processed(thread, failed=True)
    assert client.calls == [
        ("PATCH", f"/users/{AGENT_M365}/messages/g1", {"isRead": True, "flag": {"flagStatus": "flagged"}})
    ]
