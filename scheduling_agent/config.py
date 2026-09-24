from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    owner_name: str
    owner_email: str
    agent_name: str
    agent_email: str

    email_backend: str  # "graph" | "imap"
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    email_password: str

    graph_auth_mode: str  # "app" | "delegated"
    graph_client_id: str
    graph_client_secret: str
    graph_tenant_id: str
    graph_token_cache_path: str
    graph_token_cache_b64: str
    calendar_backend: str  # "graph" | "mock"
    mock_calendar_path: str

    anthropic_model: str
    timezone: str
    work_start_hour: int
    work_end_hour: int
    default_duration_minutes: int
    buffer_minutes: int
    min_notice_hours: int
    lookahead_days: int
    max_proposed_slots: int
    allowed_sender_domains: tuple[str, ...]
    dry_run: bool
    always_cc_owner: bool
    max_threads_per_run: int

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            owner_name=os.environ.get("OWNER_NAME", "the meeting owner"),
            owner_email=os.environ.get("OWNER_EMAIL", "").lower(),
            agent_name=os.environ.get("AGENT_NAME", "Scheduling Assistant"),
            agent_email=os.environ.get("AGENT_EMAIL", "").lower(),
            email_backend=os.environ.get("EMAIL_BACKEND", "graph").lower(),
            imap_host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
            imap_port=_env_int("IMAP_PORT", 993),
            smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=_env_int("SMTP_PORT", 465),
            email_password=os.environ.get("EMAIL_PASSWORD", ""),
            graph_auth_mode=os.environ.get("GRAPH_AUTH_MODE", "app").lower(),
            graph_client_id=os.environ.get("GRAPH_CLIENT_ID", ""),
            graph_client_secret=os.environ.get("GRAPH_CLIENT_SECRET", ""),
            graph_tenant_id=os.environ.get("GRAPH_TENANT_ID", ""),
            graph_token_cache_path=os.environ.get("GRAPH_TOKEN_CACHE_PATH", "graph_token_cache.json"),
            graph_token_cache_b64=os.environ.get("GRAPH_TOKEN_CACHE_B64", ""),
            calendar_backend=os.environ.get("CALENDAR_BACKEND", "mock").lower(),
            mock_calendar_path=os.environ.get("MOCK_CALENDAR_PATH", "mock_calendar.json"),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            timezone=os.environ.get("OWNER_TIMEZONE", "America/New_York"),
            work_start_hour=_env_int("WORK_START_HOUR", 9),
            work_end_hour=_env_int("WORK_END_HOUR", 17),
            default_duration_minutes=_env_int("DEFAULT_DURATION_MINUTES", 30),
            buffer_minutes=_env_int("BUFFER_MINUTES", 15),
            min_notice_hours=_env_int("MIN_NOTICE_HOURS", 24),
            lookahead_days=_env_int("LOOKAHEAD_DAYS", 10),
            max_proposed_slots=_env_int("MAX_PROPOSED_SLOTS", 4),
            allowed_sender_domains=_env_list("ALLOWED_SENDER_DOMAINS"),
            dry_run=_env_bool("DRY_RUN", True),
            always_cc_owner=_env_bool("ALWAYS_CC_OWNER", True),
            max_threads_per_run=_env_int("MAX_THREADS_PER_RUN", 10),
        )
