# 09 · Operations

How to run The Gallery as a service: configuration, deployment, monitoring, limits and what to do when something breaks.

**Status:** v1, 13 Oct 2026. Live instance: https://the-gallery-dm6q.onrender.com (Render free plan, Docker).

---

## 1. Shape of the service

- One container, one process (uvicorn + FastAPI). SQLite holds events, stories, decisions and the audit trail.
- The browser console is static files served by the same process; live updates go over a WebSocket.
- `PUBLIC_DEMO=1` turns the instance into a public sandbox: each visitor (a random id stored in their browser) sees only their own matches; real-match sources and non-synthetic modes are off; the database is reset at start.
- State is per instance. Scaling out needs a shared database and a shared rate-limit store (see §7).

## 2. Configuration

Everything is an environment variable with a safe default.

| Variable | Default | What it does |
|---|---|---|
| `PUBLIC_DEMO` | `0` (`1` in the Docker image) | Visitor sandboxes, synthetic data only, rate limits, idle-match eviction, API docs off |
| `MODEL_PROVIDER` | `template` | `template`, `foundry_local`, `azure_openai`, `openai_compatible`. Verification is the same for all |
| `LOG_JSON` | on when `PUBLIC_DEMO=1` | One JSON object per log line (for log search) |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs successful GET requests (polling) |
| `RATE_LIMIT_READ_PER_MIN` | `600` | Reads per visitor (IP + session) per minute, public mode only |
| `RATE_LIMIT_WRITE_PER_MIN` | `120` | Writes (approve, replay, corrections…) per visitor per minute |
| `RATE_LIMIT_CREATE_PER_MIN` | `10` | New matches per visitor per minute |
| `MAX_BODY_BYTES` | `64000` | Larger request bodies get `413` |
| `MATCH_IDLE_MINUTES` | `120` | Public mode: matches nobody touched for this long are freed |
| `MAX_MATCHES` | `40` | Public mode: matches held in memory in total (oldest go first) |
| `MAX_MATCHES_PER_VISITOR` | `3` | Public mode: matches per visitor |
| `API_DOCS` | off in public mode | Serve `/docs` and `/openapi.json` |
| `APP_VERSION` | `0.3.0` | Reported by `/api/metrics` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset | Optional: send traces and logs to Azure Application Insights (`pip install -r requirements-observability.txt`) |
| `ENABLE_VALIDATION_SOURCES` | `0` | Private validation only; forced off in public mode. Never enable on a public host |

## 3. Deploy

**Render (free, current):** `render.yaml` is a blueprint. Pushing to `main` redeploys automatically. Render checks `/readyz` before switching traffic, so a broken build never replaces a working one.

**Any Docker host (Azure Container Apps, Azure App Service, Fly, a VM):**

```bash
docker build -t the-gallery .
docker run -p 8000:8000 -e PORT=8000 the-gallery
```

The image runs as a non-root user (`gallery`, uid 10001), installs exact versions from `requirements.lock`, has a container `HEALTHCHECK`, honours `PORT`, trusts proxy headers (for HTTPS detection and client IPs) and shuts down gracefully within 10 seconds.

**Free-tier sleep:** free instances sleep after ~15 idle minutes. The *Keep demo awake* workflow pings `DEMO_URL` every 10 minutes. If a visitor does hit a sleeping instance, the console shows "Waking up the demo…" and retries on its own.

## 4. Health and monitoring

| Endpoint | Use |
|---|---|
| `GET /healthz` | Liveness: the process answers |
| `GET /readyz` | Readiness: the database answers and the console files exist; `503` otherwise |
| `GET /api/health` | Writer/model status, active matches, public mode |
| `GET /api/metrics` | Requests, 5xx count, rate-limited count, status counts, latency p50/p95/p99, matches created/evicted, model calls/failures, version and commit |

Every response carries `X-Request-Id` (or echoes the caller's). Errors return JSON with the same `request_id`, and the log line for that request has it too, so a user's screenshot of an error is enough to find the log entry.

Log fields: `ts`, `level`, `msg`, `request_id`, `method`, `path`, `status`, `ms`, `visitor` (first 8 characters of the random session id; no IPs or personal data).

## 5. Security measures

- **Headers on every response:** Content-Security-Policy (scripts only from this site, no framing, no plugins), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (no camera, mic, location, payment), `Cross-Origin-Opener-Policy`, HSTS over HTTPS, `Cache-Control: no-store` on the API.
- **Input validation:** every request body is a typed model with ranges and allowed values; bad input gets a plain `422`.
- **Limits:** body size, per-visitor rate limits, per-visitor and total match caps, idle eviction.
- **Isolation:** in public mode a visitor can't read, change or subscribe to another visitor's match (HTTP `404`, WebSocket refused).
- **No internals in errors:** unhandled errors return a generic message and a request id; the stack trace goes to the log only.
- **Supply chain:** pinned lock file, `pip-audit` in CI (fails on known vulnerabilities), Dependabot for Python, Actions and the base image, CI permissions read-only.
- **Data:** synthetic only; CI fails if real-match data is committed.

## 6. Limits of the free instance

- 512 MB RAM, shared CPU: about 40 concurrent matches is the safe ceiling (hence `MAX_MATCHES`).
- Memory and the SQLite file are wiped on redeploy or restart; visitors simply get a fresh match.
- In-memory rate limiting is per instance.

## 7. Scaling beyond the demo

| Need | Change |
|---|---|
| More than one instance | Move SQLite to Azure Database for PostgreSQL or Azure SQL; move rate limits to Redis; make WebSocket fan-out use a shared pub/sub (Azure Web PubSub) |
| Real producers | Put sign-in in front (Microsoft Entra ID); map visitor sessions to user accounts; keep the decision log per user |
| Hosted model | `MODEL_PROVIDER=azure_openai`; the verifier and fallbacks are unchanged |
| Monitoring | Set `APPLICATIONINSIGHTS_CONNECTION_STRING`; alert on 5xx rate, p95 latency and `/readyz` failures |

## 8. Incident runbook

| Symptom | Check | Fix |
|---|---|---|
| Site doesn't load | `curl -i https://…/healthz` | If it takes ~50 s then answers, the instance was asleep: check the keepalive workflow and `DEMO_URL`. If it fails, open the Render logs |
| `503` from `/readyz` | Render logs for database errors | Redeploy (the database is recreated at start in public mode) |
| Many `429`s in metrics | `/api/metrics` → `rate_limited`; logs by `visitor` | Normal under abuse. Raise `RATE_LIMIT_*` only if real users hit it |
| `5xx` in metrics | Search logs for the `request_id` from the error | Fix and push; CI must pass before Render deploys the new image |
| Out of memory / restarts | Render metrics | Lower `MAX_MATCHES` or `MATCH_IDLE_MINUTES` |
| Stories stop arriving | `/api/health` → model status; WebSocket in the browser dev tools | With `MODEL_PROVIDER=template` there is nothing external to fail; otherwise the template fallback takes over on model errors |
| Deploy broke something | Render → Deploys | Roll back to the previous deploy in one click, then fix forward |
