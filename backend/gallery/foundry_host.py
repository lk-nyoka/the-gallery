"""Host Microsoft Foundry Local in its own process.

The on-device model is CPU-heavy; running it inside the web server's process starves the event loop (the match
clock and the console slow down). The app starts this module as a child process and talks to the model over
Foundry Local's OpenAI-compatible HTTP endpoint instead.

Protocol: one JSON object per line on stdout, e.g. {"status": "downloading", "pct": 40} then
{"status": "ready", "url": "http://127.0.0.1:59552/v1", "model": "qwen2.5-0.5b-instruct-generic-cpu:4"}.
The process then waits until its parent closes stdin.

    python -m gallery.foundry_host qwen2.5-0.5b <cache_dir>
"""

from __future__ import annotations

import json
import sys


def say(**kw) -> None:
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def main() -> None:
    alias = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-0.5b"
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore
    except Exception:
        say(status="unavailable", detail="Foundry Local SDK not installed: pip install -r requirements-foundry.txt")
        return
    try:
        FoundryLocalManager.initialize(Configuration(app_name="the-gallery", model_cache_dir=cache))
        manager = FoundryLocalManager.instance
        model = manager.catalog.get_model(alias)
        if model is None:
            raise RuntimeError(f"model alias '{alias}' not in the Foundry Local catalog")
        last = [-5.0]

        def progress(p: float) -> None:
            if p - last[0] >= 5:
                last[0] = p
                say(status="downloading", pct=round(p))
        if not model.is_cached:
            say(status="downloading", pct=0)
        model.download(progress)
        say(status="loading")
        model.load()
        manager.start_web_service()
        say(status="ready", url=f"{manager.urls[0]}/v1", model=model.id)
    except Exception as exc:
        say(status="unavailable", detail=f"Foundry Local couldn't start ({str(exc)[:200]})")
        return
    sys.stdin.read()   # block until the parent exits or closes the pipe


if __name__ == "__main__":
    main()
