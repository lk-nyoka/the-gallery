# Security

## Reporting a problem

Please report security issues privately through GitHub: **Security → Report a vulnerability** on this repository. Don't open a public issue for them. You'll get a reply within a few days.

Useful details: what you did, what happened, the `X-Request-Id` from the response if you have one, and whether it affects the live demo.

## Scope

- The code in this repository.
- The public demo at https://the-gallery-dm6q.onrender.com. Please don't run load tests or automated scanners against it; it's a free instance and rate-limited.

## What's in place

The full list is in [`docs/09-operations.md`](docs/09-operations.md#5-security-measures). In short: strict security headers and Content-Security-Policy, typed input validation, body-size and rate limits, per-visitor sandboxes in the public demo, generic error messages with request ids, a non-root container, a pinned lock file checked by `pip-audit` in CI, and Dependabot updates.

The project uses synthetic data only and stores no personal data. Models never approve or publish anything; a human producer does.
