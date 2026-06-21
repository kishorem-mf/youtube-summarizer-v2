"""Mixer: score technology fit for a supply chain problem and generate LinkedIn content."""

from __future__ import annotations

import json
import os

import httpx as _httpx
import anthropic as _anthropic_sdk

import prompts
import storage

_anthropic = _anthropic_sdk.Anthropic(
    api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
    base_url=os.environ.get(
        "ANTHROPIC_FOUNDRY_ENDPOINT",
        "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1",
    ),
    http_client=_httpx.Client(verify=False),
)
_MODEL = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")


def get_video(video_id: str, detail: str) -> dict | None:
    table = storage._dynamo_table()
    resp  = table.get_item(Key={"video_id": video_id, "detail": detail})
    return resp.get("Item")


def list_recent(limit: int = 50) -> list[dict]:
    table = storage._dynamo_table()
    resp  = table.scan(Limit=limit)
    items = resp.get("Items", [])
    return sorted(items, key=lambda x: x.get("searched_on", ""), reverse=True)


def score_fit(sc_data: dict, tech_data: dict) -> dict:
    """Call Claude to score technology fit for the supply chain problem. Returns dict with score/headline/reasoning."""
    user_msg = prompts.mixer_score_prompt(
        sc_title   = sc_data.get("title", ""),
        sc_summary = sc_data.get("summary", ""),
        tech_title  = tech_data.get("title", ""),
        tech_summary = tech_data.get("summary", ""),
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=prompts.MIXER_SCORE_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=600,
        )
        raw = resp.content[0].text.strip()
        return json.loads(raw)
    except Exception as e:
        return {"score": 0, "headline": "Error", "reasoning": f"[Scoring error: {e}]"}


def generate_post(sc_data: dict, tech_data: dict, score: int, reasoning: str, customization: str = "") -> str:
    """Call Claude to generate the Mixer LinkedIn post text."""
    user_msg = prompts.mixer_post_prompt(
        sc_title   = sc_data.get("title", ""),
        sc_summary = sc_data.get("summary", ""),
        tech_title  = tech_data.get("title", ""),
        tech_summary = tech_data.get("summary", ""),
        score        = score,
        reasoning    = reasoning,
        customization = customization,
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=prompts.LINKEDIN_POST_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=600,
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[Error generating post: {e}]"


def generate_slides(sc_data: dict, tech_data: dict, score: int, reasoning: str) -> list[dict]:
    """Call Claude to generate Mixer carousel slide dicts."""
    user_msg = prompts.mixer_slides_prompt(
        sc_title   = sc_data.get("title", ""),
        sc_summary = sc_data.get("summary", ""),
        tech_title  = tech_data.get("title", ""),
        tech_summary = tech_data.get("summary", ""),
        score        = score,
        reasoning    = reasoning,
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=prompts.MIXER_SLIDES_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=800,
        )
        raw  = resp.content[0].text.strip()
        data = json.loads(raw)
        return data.get("slides", [])
    except Exception as e:
        return [{"type": "title", "heading": "Mixer Carousel", "subheading": f"Error: {e}"}]
