"""Model providers. One setting picks where the words come from; anything unavailable falls back.

MODEL_PROVIDER = template | foundry_local | azure_openai | openai_compatible | auto (default)

  template           deterministic writer, no model (always available)
  foundry_local      Microsoft Foundry Local, a free on-device model (Windows / macOS)
                     FOUNDRY_MODEL = model alias (default qwen2.5-1.5b; qwen2.5-0.5b for low-RAM laptops)
  azure_openai       AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
  openai_compatible  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
  auto               azure_openai if configured, else openai_compatible, else template

Models never compute numbers or decide what's true. They choose and phrase; code verifies.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Optional

import httpx


class Provider:
    def __init__(self) -> None:
        self.kind = "template"
        self.base_url = ""
        self.key = ""
        self.model = ""
        self.headers: dict[str, str] = {}
        self.status = "ready"
        self.detail = "Deterministic template writer (no model)."
        self.calls = 0
        self.failures = 0
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ setup
    def configure(self) -> "Provider":
        want = os.getenv("MODEL_PROVIDER", "auto").strip().lower() or "auto"
        if want == "auto":
            if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY"):
                want = "azure_openai"
            elif os.getenv("LLM_BASE_URL"):
                want = "openai_compatible"
            else:
                want = "template"
        if want == "azure_openai":
            ep = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
            dep = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
            ver = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
            if ep and dep and os.getenv("AZURE_OPENAI_API_KEY"):
                self.kind, self.model = "azure_openai", dep
                self.key = os.getenv("AZURE_OPENAI_API_KEY", "")
                self.headers = {"api-key": self.key}
                self.base_url = (f"{ep}/openai/v1" if ver == "v1"
                                 else f"{ep}/openai/deployments/{dep}")
                self._azure_version = ver
                self.status, self.detail = "ready", f"Azure OpenAI deployment '{dep}'"
            else:
                self._fallback("Azure OpenAI selected but AZURE_OPENAI_* settings are incomplete.")
        elif want == "openai_compatible":
            if os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL"):
                self.kind = "openai_compatible"
                self.base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
                self.key = os.getenv("LLM_API_KEY", "none")
                self.model = os.getenv("LLM_MODEL", "")
                self.headers = {"Authorization": f"Bearer {self.key}"}
                self.status, self.detail = "ready", f"OpenAI-compatible endpoint, model '{self.model}'"
            else:
                self._fallback("OpenAI-compatible provider selected but LLM_BASE_URL / LLM_MODEL are missing.")
        elif want == "foundry_local":
            self.kind = "foundry_local"
            self.status, self.detail = "starting", "Starting Microsoft Foundry Local…"
            threading.Thread(target=self._start_foundry, daemon=True).start()
        else:
            self._fallback(None)
        return self

    def _fallback(self, why: Optional[str]) -> None:
        self.kind, self.status = "template", "ready"
        self.detail = "Deterministic template writer (no model)." + (f" {why}" if why else "")

    def _start_foundry(self) -> None:
        """Start Foundry Local in-process, download the model once, expose its OpenAI-compatible endpoint."""
        alias = os.getenv("FOUNDRY_MODEL", "qwen2.5-1.5b")
        try:
            from foundry_local_sdk import Configuration, FoundryLocalManager  # type: ignore
        except Exception:
            self.status = "unavailable"
            self.detail = ("Foundry Local SDK not installed: pip install -r requirements-foundry.txt. "
                           "Using the template writer meanwhile.")
            return
        try:
            cache = os.getenv("FOUNDRY_CACHE_DIR") or os.path.join(os.path.dirname(__file__), "..", "data",
                                                                    "foundry_cache")
            FoundryLocalManager.initialize(Configuration(app_name="the-gallery", model_cache_dir=cache))
            manager = FoundryLocalManager.instance
            model = manager.catalog.get_model(alias)
            if model is None:
                raise RuntimeError(f"model alias '{alias}' not in the Foundry Local catalog")

            def progress(p: float) -> None:
                self.detail = f"Downloading {alias} for Foundry Local: {p:.0f}%"
            self.status = "downloading"
            model.download(progress)
            self.status, self.detail = "loading", f"Loading {alias} into memory…"
            model.load()
            manager.start_web_service()
            self.base_url = f"{manager.urls[0]}/v1"
            self.model = model.id
            self.key = "none"
            self.headers = {"Authorization": "Bearer none"}
            self._foundry = (manager, model)
            self.status, self.detail = "ready", f"Microsoft Foundry Local, on-device model '{alias}'"
        except Exception as exc:  # keep the app running on templates
            self.status = "unavailable"
            self.detail = f"Foundry Local couldn't start ({str(exc)[:160]}). Using the template writer."

    # ------------------------------------------------------------------ use
    @property
    def available(self) -> bool:
        return self.kind != "template" and self.status == "ready"

    def uses(self, agent: str) -> bool:
        """Which agents call the model. MODEL_AGENTS=editor,analyst,writer,localiser (default: all for hosted
        models; writer + localiser for on-device models, which are slow on a laptop CPU)."""
        if not self.available:
            return False
        default = "writer,localiser" if self.kind == "foundry_local" else "editor,analyst,writer,localiser"
        wanted = {a.strip().lower() for a in (os.getenv("MODEL_AGENTS") or default).split(",") if a.strip()}
        return agent in wanted or "all" in wanted

    def describe(self) -> dict[str, Any]:
        agents = [a for a in ("editor", "analyst", "writer", "localiser") if self.uses(a)]
        return {"provider": self.kind if self.available else "template", "selected": self.kind, "agents": agents,
                "status": self.status, "detail": self.detail, "model": self.model if self.available else None,
                "calls": self.calls, "failures": self.failures, "last_error": self.last_error}

    def label(self) -> str:
        return f"{self.kind}:{self.model}" if self.available else "template"

    def _url(self) -> str:
        if self.kind == "azure_openai" and getattr(self, "_azure_version", "") != "v1":
            return f"{self.base_url}/chat/completions?api-version={self._azure_version}"
        return f"{self.base_url}/chat/completions"

    async def chat(self, messages: list[dict], json_mode: bool = True, max_tokens: int = 700,
                   temperature: float = 0.2, timeout: float = 120.0) -> str:
        if not self.available:
            raise RuntimeError("no model available")
        body: dict[str, Any] = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.kind != "azure_openai" or getattr(self, "_azure_version", "") == "v1":
            body["model"] = self.model
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        self.calls += 1
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(self._url(), headers={**self.headers, "Content-Type": "application/json"},
                                      json=body)
                if r.status_code == 400 and "response_format" in body:
                    body.pop("response_format")
                    r = await client.post(self._url(), headers={**self.headers, "Content-Type": "application/json"},
                                          json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as exc:
            self.failures += 1
            self.last_error = str(exc)[:200]
            raise

    async def chat_json(self, messages: list[dict], **kw) -> dict:
        raw = await self.chat(messages, json_mode=True, **kw)
        return parse_json(raw)


def parse_json(raw: str) -> dict:
    """Small local models wrap JSON in prose or code fences; take the first JSON object."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        if start < 0:
            raise
        obj, _ = json.JSONDecoder().raw_decode(s[start:])   # first complete object; ignore trailing text
        return obj


_provider: Optional[Provider] = None
_started = time.time()


def provider() -> Provider:
    global _provider
    if _provider is None:
        _provider = Provider().configure()
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None
