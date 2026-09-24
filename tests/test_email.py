from scheduling_agent.email_client import build_reply, parse_message
from scheduling_agent.models import EmailThread

from .conftest import AGENT, GUEST, OWNER, message

RAW = b"""From: Guest <Guest@Partner.com>
To: owner@firm.com
Cc: scheduler@gmail.com
Subject: Re: Intro
Date: Mon, 21 Sep 2026 09:55:00 -0400
Message-ID: <m2@partner.com>
In-Reply-To: <m1@firm.com>
References: <m1@firm.com>
Content-Type: multipart/alternative; boundary="b"

--b
Content-Type: text/plain; charset="utf-8"

Tuesday 2pm works.
--b
Content-Type: text/html; charset="utf-8"

<p>Tuesday <b>2pm</b> works.</p>
--b--
"""


def test_parse_message_prefers_plain_text_and_normalises_addresses():
    m = parse_message(RAW, uid="42")
    assert m.sender == "guest@partner.com"
    assert m.to == ("owner@firm.com",)
    assert m.cc == ("scheduler@gmail.com",)
    assert m.body == "Tuesday 2pm works."
    assert m.in_reply_to == "<m1@firm.com>"
    assert m.thread_id == "<m1@firm.com>"
    assert not m.is_auto_generated


def test_parse_message_html_fallback_and_auto_detection():
    raw = (
        b"From: a@b.com\r\nTo: owner@firm.com\r\nAuto-Submitted: auto-replied\r\n"
        b"Content-Type: text/html\r\n\r\n<div>Out of office &amp; away</div>"
    )
    m = parse_message(raw, uid="1")
    assert m.body == "Out of office & away"
    assert m.is_auto_generated


def test_build_reply_is_reply_all_without_agent_and_with_owner_cc():
    thread = EmailThread(
        "thr",
        [message(sender=GUEST, to=(OWNER, "other@partner.com"), cc=(AGENT,), references=("<root@x>",))],
    )
    reply = build_reply(thread, agent_email=AGENT, body="Options below", extra_cc=(OWNER,))
    assert reply.to == (GUEST, OWNER, "other@partner.com")
    assert reply.cc == ()  # owner already in To; agent excluded
    assert reply.subject == "Re: Intro"
    assert reply.in_reply_to == "<1@test>"
    assert reply.references == ("<root@x>", "<1@test>")


def test_build_reply_adds_owner_cc_when_not_on_to():
    thread = EmailThread("thr", [message(sender=GUEST, to=(AGENT,), cc=())])
    reply = build_reply(thread, agent_email=AGENT, body="x", extra_cc=(OWNER,))
    assert reply.to == (GUEST,)
    assert reply.cc == (OWNER,)
