"""
Veritas AI Configuration
==========================
Controls how the AI-powered breach investigation feature operates.

Mode is set via VERITAS_AI_MODE environment variable:

  disabled   — Never call any LLM. All explanations use deterministic templates.
               Use this for: air-gapped deployments, strict data governance,
               organisations that don't permit external API calls.

  external   — Use an external cloud LLM provider (OpenAI or Anthropic).
               Requires: OPENAI_API_KEY or ANTHROPIC_API_KEY.
               Data sent: rule type, field name, statute text (no raw PII).
               Default when API keys are configured.

  local      — Use a locally hosted LLM via the OpenAI-compatible API.
               Requires: VERITAS_LOCAL_AI_URL (e.g. http://localhost:11434/v1)
               Compatible with: Ollama, LM Studio, llama.cpp server.
               No data leaves the machine.

Core compliance functions (PII detection, rule engine, evidence storage)
are NEVER dependent on AI — they work identically in all modes.

LLM output NEVER modifies evidence automatically. It only annotates
on-demand investigation responses (the '@N what happened?' queries).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("veritas.ai")

# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

def ai_mode() -> str:
    """
    Return the current AI mode: 'disabled', 'external', or 'local'.

    Priority:
      1. VERITAS_AI_MODE env var (explicit override)
      2. FORCE_LLM_FALLBACK=1 → 'disabled'
      3. VERITAS_LOCAL_AI_URL set → 'local'
      4. OPENAI_API_KEY or ANTHROPIC_API_KEY set → 'external'
      5. None configured → 'disabled'
    """
    explicit = os.environ.get("VERITAS_AI_MODE", "").strip().lower()
    if explicit in ("disabled", "external", "local"):
        return explicit

    if os.environ.get("FORCE_LLM_FALLBACK", "").strip() == "1":
        return "disabled"

    if os.environ.get("VERITAS_LOCAL_AI_URL", "").strip():
        return "local"

    if os.environ.get("OPENAI_API_KEY", "").strip() or \
       os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "external"

    return "disabled"


def ai_enabled() -> bool:
    return ai_mode() != "disabled"


def ai_provider_info() -> dict:
    """Return a dict describing the current AI configuration (no secrets)."""
    mode = ai_mode()
    info: dict = {"mode": mode}

    if mode == "external":
        if os.environ.get("ANTHROPIC_API_KEY"):
            info["provider"] = "anthropic"
            info["model"] = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20251001")
        else:
            info["provider"] = "openai"
            info["model"] = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        info["api_key_configured"] = True

    elif mode == "local":
        info["provider"] = "local"
        info["url"] = os.environ.get("VERITAS_LOCAL_AI_URL", "http://localhost:11434/v1")
        info["model"] = os.environ.get("VERITAS_LOCAL_AI_MODEL", "llama3")

    else:
        info["provider"] = "none"
        info["note"] = "Using deterministic template explanations."

    return info


# ---------------------------------------------------------------------------
# OpenAI-compatible client factory
# ---------------------------------------------------------------------------

def get_ai_client():
    """
    Return a configured OpenAI-compatible client, or None if AI is disabled.

    - mode=external: uses OPENAI_API_KEY or ANTHROPIC_API_KEY
    - mode=local:    uses VERITAS_LOCAL_AI_URL (Ollama / LM Studio / etc.)
    - mode=disabled: returns None
    """
    mode = ai_mode()
    if mode == "disabled":
        return None, None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — AI features unavailable.")
        return None, None

    if mode == "local":
        url   = os.environ.get("VERITAS_LOCAL_AI_URL", "http://localhost:11434/v1")
        model = os.environ.get("VERITAS_LOCAL_AI_MODEL", "llama3")
        client = OpenAI(api_key="local", base_url=url)
        logger.debug("AI: local provider at %s, model=%s", url, model)
        return client, model

    # external
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20251001")
            client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            return client, model
        except ImportError:
            pass

    model  = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("AI mode=external but no API key found — falling back to disabled.")
        return None, None

    client = OpenAI(api_key=api_key)
    return client, model
