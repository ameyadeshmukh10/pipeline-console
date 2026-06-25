"""Report 3 (Part B) — the LLM forecast agent. Strictly downstream of the
deterministic packet: it narrates/judges, never recomputes numbers. Output is
forced through a tool schema and every deal_id is validated against the packet.
"""
import json
from typing import Any, Dict, List, Set

from ..config import settings
from .client import get_async_client
from .prompts import FORECAST_SYSTEM, FORECAST_TOOL

_LIST_KEYS = ["deals_to_discuss", "anomalies", "should_be_closed_lost", "per_deal_call"]


def _empty(summary: str = "") -> Dict[str, Any]:
    out = {k: [] for k in _LIST_KEYS}
    out["summary"] = summary
    out["dropped_ids"] = []
    return out


def _validate(raw: Dict[str, Any], valid_ids: Set[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"summary": str(raw.get("summary", "")), "dropped_ids": []}
    for key in _LIST_KEYS:
        kept: List[Dict[str, Any]] = []
        for item in raw.get(key, []) or []:
            if isinstance(item, dict) and item.get("deal_id") in valid_ids:
                kept.append(item)
            elif isinstance(item, dict) and item.get("deal_id"):
                out["dropped_ids"].append(item.get("deal_id"))
        out[key] = kept
    return out


async def run_forecast_agent(packet: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.anthropic_ready:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured in .env")

    valid_ids = {d["deal_id"] for d in packet.get("deals", [])}
    if not valid_ids:
        return _empty("No open S1-S5 pipeline for this AE.")

    client = get_async_client()
    msg = await client.messages.create(
        model=settings.FORECAST_MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": FORECAST_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        tools=[FORECAST_TOOL],
        tool_choice={"type": "tool", "name": "submit_forecast"},
        messages=[{"role": "user", "content": json.dumps(packet, default=str)}],
    )

    if getattr(msg, "stop_reason", None) == "refusal":
        return _empty("Model declined to analyze this packet.")

    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_forecast":
            result = _validate(dict(block.input), valid_ids)
            result["model"] = settings.FORECAST_MODEL
            result["usage"] = {
                "input_tokens": getattr(msg.usage, "input_tokens", None),
                "output_tokens": getattr(msg.usage, "output_tokens", None),
            }
            return result
    return _empty("No structured output returned.")
