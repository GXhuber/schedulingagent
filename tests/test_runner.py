from dataclasses import replace

from scheduling_agent import runner
from scheduling_agent.agent import AgentResult
from scheduling_agent.email_client import InMemoryEmailProvider
from scheduling_agent.models import EmailThread

from .conftest import AGENT, GUEST, OWNER, message


def test_triage_skips_agent_own_auto_and_ownerless_threads(settings):
    assert runner.triage(EmailThread("t", [message(sender=AGENT)]), settings)
    assert runner.triage(EmailThread("t", [message(auto=True)]), settings)
    assert runner.triage(EmailThread("t", [message(to=("someone@else.com",), cc=(AGENT,))]), settings)
    assert runner.triage(EmailThread("t", [message()]), settings) is None


def test_triage_domain_allowlist(settings):
    s = replace(settings, allowed_sender_domains=("partner.com",))
    assert runner.triage(EmailThread("t", [message(sender=GUEST)]), s) is None
    assert runner.triage(EmailThread("t", [message(sender="x@other.com")]), s)
    assert runner.triage(EmailThread("t", [message(sender=OWNER, to=(GUEST,))]), s) is None


def test_process_inbox_marks_threads_and_isolates_failures(settings, calendar, monkeypatch):
    good = EmailThread("good", [message(uid="g")])
    bad = EmailThread("bad", [message(uid="b")])
    skip = EmailThread("skip", [message(uid="s", sender=AGENT)])
    provider = InMemoryEmailProvider([good, bad, skip])

    def fake_run_agent(client, s, tools):
        if tools.thread.thread_id == "bad":
            raise RuntimeError("boom")
        tools.send_reply("hi")
        return AgentResult(summary="replied", actions=tools.log, stop_reason="end_turn")

    monkeypatch.setattr(runner, "run_agent", fake_run_agent)
    summary = runner.process_inbox(settings, provider, calendar, client=object())

    assert [p["thread_id"] for p in summary.processed] == ["good"]
    assert summary.processed[0]["replies"] == 1
    assert [f["thread_id"] for f in summary.failed] == ["bad"]
    assert [s["thread_id"] for s in summary.skipped] == ["skip"]
    assert sorted(provider.processed) == [("bad", True), ("good", False), ("skip", False)]
    assert provider.threads == []
