"""Production plumbing: settings, security headers, rate limits, structured logs, request ids, metrics.

Everything is configured by environment variables (see docs/09-operations.md) and has a safe default.
Nothing here changes what the newsroom does; it protects the service that runs it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    public_demo: bool = field(default_factory=lambda: _flag("PUBLIC_DEMO"))
    log_json: bool = field(default_factory=lambda: _flag("LOG_JSON", _flag("PUBLIC_DEMO")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    # requests per minute per visitor (IP + session); write calls are the expensive ones
    rate_read: int = field(default_factory=lambda: _int("RATE_LIMIT_READ_PER_MIN", 600))
    rate_write: int = field(default_factory=lambda: _int("RATE_LIMIT_WRITE_PER_MIN", 120))
    rate_create: int = field(default_factory=lambda: _int("RATE_LIMIT_CREATE_PER_MIN", 10))
    max_body_bytes: int = field(default_factory=lambda: _int("MAX_BODY_BYTES", 64_000))
    idle_minutes: int = field(default_factory=lambda: _int("MATCH_IDLE_MINUTES", 120))
    max_matches: int = field(default_factory=lambda: _int("MAX_MATCHES", 40))
    max_per_visitor: int = field(default_factory=lambda: _int("MAX_MATCHES_PER_VISITOR", 3))
    api_docs: bool = field(default_factory=lambda: _flag("API_DOCS", not _flag("PUBLIC_DEMO")))
    version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "0.3.0"))
    commit: str = field(default_factory=lambda: os.getenv("RENDER_GIT_COMMIT", os.getenv("GIT_COMMIT", "dev"))[:12])


settings = Settings()


# ------------------------------------------------------------------ logging
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
                               "msg": record.getMessage()}
        for k in ("request_id", "method", "path", "status", "ms", "visitor", "match_id"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> logging.Logger:
    log = logging.getLogger("gallery")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_JsonFormatter() if settings.log_json else
                       logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        log.addHandler(h)
        log.propagate = False
    log.setLevel(settings.log_level)
    return log


log = setup_logging()


def setup_azure_monitor() -> bool:
    """Optional: send traces and logs to Azure Application Insights when a connection string is set
    (pip install -r requirements-observability.txt). Off by default; the app runs the same without it."""
    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore
    except Exception:
        log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING is set but azure-monitor-opentelemetry is not installed")
        return False
    configure_azure_monitor(logger_name="gallery")
    log.info("azure monitor enabled")
    return True


# ------------------------------------------------------------------ rate limiting (in memory, per process)
class RateLimiter:
    """Sliding one-minute window per key. Good enough for one free instance; use a shared store when scaling out."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False, int(60 - (now - q[0])) + 1
        q.append(now)
        if len(self.hits) > 50_000:      # bound memory under abuse
            for k in list(self.hits)[:10_000]:
                self.hits.pop(k, None)
        return True, 0


limiter = RateLimiter()


def bucket(method: str, path: str) -> tuple[str, int] | None:
    if not path.startswith("/api/"):
        return None
    if method == "POST" and path.rstrip("/") in ("/api/matches", "/api/matches/default"):
        return "create", settings.rate_create
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "write", settings.rate_write
    return "read", settings.rate_read


# ------------------------------------------------------------------ security headers
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' ws: wss:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# ------------------------------------------------------------------ metrics
class Metrics:
    def __init__(self) -> None:
        self.started = time.time()
        self.requests = 0
        self.errors_5xx = 0
        self.rate_limited = 0
        self.by_status: dict[int, int] = defaultdict(int)
        self.latency_ms: deque = deque(maxlen=2000)
        self.matches_created = 0
        self.matches_evicted = 0

    def observe(self, status: int, ms: float) -> None:
        self.requests += 1
        self.by_status[status] += 1
        if status >= 500:
            self.errors_5xx += 1
        self.latency_ms.append(ms)

    def snapshot(self) -> dict[str, Any]:
        lat = sorted(self.latency_ms)
        pct = (lambda q: round(lat[min(len(lat) - 1, int(q * (len(lat) - 1)))], 1) if lat else None)
        return {"uptime_s": int(time.time() - self.started), "requests": self.requests,
                "errors_5xx": self.errors_5xx, "rate_limited": self.rate_limited,
                "by_status": dict(self.by_status), "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99)},
                "matches_created": self.matches_created, "matches_evicted": self.matches_evicted}


metrics = Metrics()
