"""LLM provider abstraction for the Fleet Assistant.

Supports two backends behind one normalized interface:

- ``deepseek`` (default) — OpenAI-compatible API (api.deepseek.com).
- ``gemini``     — Google genai SDK (legacy, kept as fallback).

Normalized message format used by callers:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str}                        # plain text
    {"role": "assistant", "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "tool_call_id": str, "content": str}

``chat()`` returns ``LLMResult(text, tool_calls)`` where tool_calls is a list
of ``ToolCall(name, args)``. Tool declarations use the Gemini-style schema
(uppercase types) and are converted per backend internally.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_FALLBACK_MODELS = [
    m for m in os.getenv("DEEPSEEK_FALLBACK_MODELS", "deepseek-v4-pro,deepseek-v4-flash").split(",") if m
]
DEEPSEEK_VISION_MODEL = os.getenv("DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp")

ASSISTANT_PROVIDER = os.getenv("ASSISTANT_PROVIDER", "").strip().lower()  # "deepseek" | "gemini" | "" = auto

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]


@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""  # thinking-mode tokens to echo back on tool turns


def active_provider() -> str:
    """Resolve which backend to use: explicit env > auto-detect by key."""
    if ASSISTANT_PROVIDER in {"deepseek", "gemini"}:
        return ASSISTANT_PROVIDER
    if DEEPSEEK_API_KEY:
        return "deepseek"
    if GEMINI_API_KEY:
        return "gemini"
    return "deepseek"  # default intent; chat() will raise a clear error if unconfigured


def is_configured() -> bool:
    if active_provider() == "deepseek":
        return bool(DEEPSEEK_API_KEY)
    return bool(GEMINI_API_KEY)


def _raise_quota() -> None:
    from fastapi import HTTPException

    raise HTTPException(status_code=429, detail="Assistant is temporarily busy. Please try again in a minute.")


# ═════════════════════════════════════════════════════════════════════════
# DeepSeek (OpenAI-compatible)
# ═════════════════════════════════════════════════════════════════════════

def _gemini_tools_to_openai(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if not tools:
        return None
    out = []
    for t in tools:
        decl = t.get("function_declarations", [t]) if "function_declarations" in t else [t]
        for d in decl:
            params = json.loads(json.dumps(d.get("parameters", {})))
            params["type"] = "object"
            for prop in (params.get("properties") or {}).values():
                if isinstance(prop, dict):
                    prop["type"] = str(prop.get("type", "string")).lower()
            out.append({
                "type": "function",
                "function": {
                    "name": d.get("name"),
                    "description": d.get("description", ""),
                    "parameters": params,
                },
            })
    return out or None


def _messages_to_openai(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id", ""),
                "content": m.get("content", ""),
            })
        elif m.get("tool_calls"):
            entry: Dict[str, Any] = {
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))},
                    }
                    for i, tc in enumerate(m["tool_calls"])
                ],
            }
            # Thinking-mode models require their reasoning tokens to be echoed
            # back on the next turn.
            if m.get("reasoning_content"):
                entry["reasoning_content"] = m["reasoning_content"]
            out.append(entry)
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _chat_deepseek(
    system: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    max_tokens: int,
) -> LLMResult:
    import httpx

    openai_messages = [{"role": "system", "content": system}] + _messages_to_openai(messages)
    openai_tools = _gemini_tools_to_openai(tools)

    payload: Dict[str, Any] = {
        "messages": openai_messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
    }
    if openai_tools:
        payload["tools"] = openai_tools

    models_to_try = [DEEPSEEK_MODEL] + [m for m in DEEPSEEK_FALLBACK_MODELS if m != DEEPSEEK_MODEL]
    last_error: Optional[Exception] = None

    for model in models_to_try:
        try:
            resp = httpx.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={**payload, "model": model},
                timeout=90.0,
            )
            if resp.status_code == 429:
                logger.warning(f"Fleet Assistant: deepseek {model} rate-limited, trying next model...")
                last_error = Exception("429")
                continue
            resp.raise_for_status()
            data = resp.json()

            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = (msg.get("content") or "").strip()
            reasoning = msg.get("reasoning_content") or ""

            tool_calls: List[ToolCall] = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(name=fn.get("name", ""), args=args or {}))

            return LLMResult(text=text, tool_calls=tool_calls, reasoning_content=reasoning)
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "rate" in err_str.lower():
                continue
            raise

    logger.error(f"Fleet Assistant: all deepseek models failed. Last error: {last_error}")
    _raise_quota()


# ═════════════════════════════════════════════════════════════════════════
# Gemini (legacy backend)
# ═════════════════════════════════════════════════════════════════════════

def _chat_gemini(
    system: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    max_tokens: int,
) -> LLMResult:
    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    contents: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "tool":
            contents.append({
                "role": "user",
                "parts": [{"function_response": {"name": m.get("name", ""), "response": {"output": m.get("content", "")}}}],
            })
        elif m.get("tool_calls"):
            for tc in m["tool_calls"]:
                contents.append({
                    "role": "model",
                    "parts": [{"function_call": {"name": tc["name"], "args": tc.get("args", {})}}],
                })
        else:
            contents.append({
                "role": "user" if m.get("role") == "user" else "model",
                "parts": [{"text": m.get("content", "")}],
            })

    config: Dict[str, Any] = {
        "system_instruction": system,
        "temperature": 0.3,
        "max_output_tokens": max_tokens,
    }
    if tools:
        config["tools"] = [{"function_declarations": tools}]

    preferred = os.getenv("ASSISTANT_MODEL", "gemini-2.5-flash")
    models_to_try = [preferred] + [m for m in GEMINI_MODELS if m != preferred]
    last_error: Optional[Exception] = None

    for model in models_to_try:
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            calls = response.function_calls or []
            tool_calls = [ToolCall(name=c.name, args=dict(c.args or {})) for c in calls]
            text = (response.text or "").strip()
            return LLMResult(text=text, tool_calls=tool_calls)
        except Exception as e:
            last_error = e
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                continue
            raise

    logger.error(f"Fleet Assistant: all gemini models exhausted. Last error: {last_error}")
    _raise_quota()


# ═════════════════════════════════════════════════════════════════════════
# Public interface
# ═════════════════════════════════════════════════════════════════════════

def extract_text_from_image(image_bytes: bytes) -> str:
    """OCR-style extraction via the DeepSeek vision model (images / scanned PDFs)."""
    import base64
    import httpx

    if not DEEPSEEK_API_KEY or not image_bytes:
        return ""

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": DEEPSEEK_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract all visible text from this image. Return only the extracted text."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": 4000,
    }
    try:
        resp = httpx.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        return ((choice.get("message") or {}).get("content") or "").strip()
    except Exception as e:
        logger.warning(f"Vision extraction failed: {e}")
        return ""


def chat(
    system: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 500,
) -> LLMResult:
    """One LLM completion with normalized messages/tools.

    Raises fastapi.HTTPException(429) when every model is rate-limited,
    and ValueError when the selected provider is unconfigured.
    """
    provider = active_provider()
    if provider == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise ValueError("Assistant is not configured (missing DEEPSEEK_API_KEY).")
        return _chat_deepseek(system, messages, tools, max_tokens)
    if not GEMINI_API_KEY:
        raise ValueError("Assistant is not configured (missing GEMINI_API_KEY).")
    return _chat_gemini(system, messages, tools, max_tokens)
