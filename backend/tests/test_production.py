"""Production behaviour: headers, limits, validation, health probes, metrics, sandbox ownership, error hiding."""

import pytest
from fastapi.testclient import TestClient

from gallery import app as app_mod
from gallery import production


@pytest.fixture
def client():
    return TestClient(app_mod.app)


def test_security_headers_and_request_id(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    h = r.headers
    assert "default-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    assert len(h["x-request-id"]) >= 8
    assert client.get("/api/health", headers={"X-Request-Id": "trace-me-123"}).headers["x-request-id"] == "trace-me-123"


def test_console_page_has_no_inline_script(client):
    """The CSP forbids inline scripts, so the console must load all JS from files."""
    import re
    html = client.get("/").text
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag, tag


def test_health_probes_and_metrics(client):
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["ready"] is True
    m = client.get("/api/metrics").json()
    for k in ("uptime_s", "requests", "errors_5xx", "rate_limited", "latency_ms", "version", "matches_active"):
        assert k in m
    assert m["requests"] >= 1


def test_readyz_reports_not_ready_when_db_fails(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_mod.store, "query", boom)
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["ready"] is False


def test_validation_errors_are_plain_422(client):
    r = client.post("/api/matches/any/replay", json={"speed": 0})
    assert r.status_code == 422 and r.json()["detail"].startswith("Invalid request:")
    r = client.post("/api/storylines/nope/decision", json={"action": "delete-everything"})
    assert r.status_code == 422
    r = client.post("/api/matches", json={"mode": "rm -rf", "fixture": "A"})
    assert r.status_code == 422


def test_oversized_body_is_rejected(client):
    r = client.post("/api/matches", content=b"{" + b" " * (production.settings.max_body_bytes + 10) + b"}",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_rate_limit_in_public_mode(client, monkeypatch):
    monkeypatch.setattr(app_mod, "PUBLIC_DEMO", True)
    monkeypatch.setattr(production.settings, "rate_read", 5)
    monkeypatch.setattr(production, "limiter", production.RateLimiter())
    monkeypatch.setattr(app_mod, "limiter", production.limiter)
    hdr = {"X-Gallery-Session": "ratelimit-test"}
    codes = [client.get("/api/matches", headers=hdr).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    r = client.get("/api/matches", headers=hdr)
    assert int(r.headers["retry-after"]) >= 1
    # another visitor is not affected
    assert client.get("/api/matches", headers={"X-Gallery-Session": "someone-else"}).status_code == 200


def test_rate_limiter_window_slides():
    rl = production.RateLimiter()
    assert all(rl.allow("k", 2, now=t)[0] for t in (0, 1))
    assert rl.allow("k", 2, now=2) == (False, 59)
    assert rl.allow("k", 2, now=61)[0]


def test_websocket_rejects_other_visitors_in_public_mode(client, monkeypatch):
    monkeypatch.setattr(app_mod, "PUBLIC_DEMO", True)
    monkeypatch.setattr(app_mod, "runtimes", {})
    mid = client.post("/api/matches", json={"mode": "fixture", "fixture": "A"},
                      headers={"X-Gallery-Session": "owner-1"}).json()["match_id"]
    with client.websocket_connect(f"/ws/{mid}?s=intruder") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
    with client.websocket_connect(f"/ws/{mid}?s=owner-1") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_unhandled_errors_hide_internals(monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("secret internal detail /etc/passwd")
    monkeypatch.setattr(app_mod.llm, "provider", explode)
    r = TestClient(app_mod.app, raise_server_exceptions=False).get("/api/health")
    assert r.status_code == 500
    body = r.json()
    assert "secret" not in r.text and "request_id" in body
