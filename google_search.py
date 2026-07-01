"""Google Search via DuckDuckGo + article fetch + Claude summarisation.

Mirrors summarizer.py patterns: same Anthropic client, same detail specs,
same result dict shape, same DynamoDB storage via storage.py.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import os

import httpx as _httpx
import requests as _req
import anthropic as _anthropic_sdk

import storage

# ─────────────────────────────────────────────
# Clients
# ─────────────────────────────────────────────

_anthropic = _anthropic_sdk.Anthropic(
    api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
    base_url=os.environ.get(
        "ANTHROPIC_FOUNDRY_ENDPOINT",
        "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1",
    ),
    http_client=_httpx.Client(verify=False),
)
_MODEL = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")

# ─────────────────────────────────────────────
# Date filter mapping  (UI label → DuckDuckGo timelimit)
# ─────────────────────────────────────────────

_DATE_MAP = {
    "":       None,   # any time
    "today":  "d",    # past day
    "week":   "w",    # past week
    "month":  "m",    # past month
    "year":   "y",    # past year
}

# ─────────────────────────────────────────────
# Summarisation specs  (same as summarizer.py)
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a market-intelligence analyst. Summarize the web article for a "
    "professional tracking this topic. Be factual and specific — no hype, no "
    "filler. Prefer concrete details: numbers, tools, product names, claims. "
    "Base it ONLY on the supplied material; if information is thin, say so briefly."
)

_DETAIL_SPECS = {
    "low":    ("Output a one-sentence TL;DR, then 2-3 short bullet points of the most important takeaways.", 320, 6000),
    "medium": ("Output a one-sentence TL;DR, then 4-6 bullet points capturing the key concrete takeaways with specifics.", 550, 9000),
    "high":   ("Output a 1-2 sentence TL;DR, then 8-12 detailed bullet points covering all key concepts, examples, numbers, tools/products named, and actionable takeaways. Group bullets under short bold sub-headings when the content has distinct themes.", 2000, 16000),
}


def _content_id(url: str) -> str:
    """Stable 12-char hash of URL — used as DynamoDB PK (video_id field)."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────

def search_google(query: str, max_results: int = 6, date_filter: str = "") -> list[dict]:
    """Search via DuckDuckGo (free, no API key). Returns list of article dicts."""
    from duckduckgo_search import DDGS

    timelimit = _DATE_MAP.get(date_filter, None)

    try:
        with DDGS(verify=False) as ddgs:
            raw = list(ddgs.text(
                query,
                max_results=max_results,
                timelimit=timelimit,
            ))
    except Exception as e:
        raise RuntimeError(f"DuckDuckGo search failed: {e}") from e

    articles = []
    for item in raw:
        article_url = item.get("href", "")
        if not article_url:
            continue
        articles.append({
            "id":          _content_id(article_url),
            "title":       item.get("title", ""),
            "url":         article_url,
            "domain":      _domain(article_url),
            "date":        "",
            "description": item.get("body", ""),
            "search_term": query,
        })
    return articles


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.removeprefix("www.")
    except Exception:
        return ""


# ─────────────────────────────────────────────
# Article fetch
# ─────────────────────────────────────────────

def fetch_article_text(url: str) -> tuple[str, str]:
    """Fetch article URL and extract visible text via BeautifulSoup.
    Returns (text, error_message). text is empty string on failure."""
    try:
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; summarizer-bot/1.0)"}
        r = _req.get(url, headers=headers, timeout=30, verify=False, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:20000], ""
    except Exception as e:
        return "", str(e)


# ─────────────────────────────────────────────
# Summarisation
# ─────────────────────────────────────────────

def _generate_tags(summary: str) -> list:
    if not summary or summary.startswith("_"):
        return []
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system="You are a tagging assistant. Return only a comma-separated list of tags, nothing else.",
            messages=[{"role": "user", "content": (
                f"From the following article summary, generate up to 6 concise, lowercase tags "
                f"that best describe the topic and content. No hashtags.\n\n{summary[:1000]}"
            )}],
            max_tokens=60,
        )
        raw = resp.content[0].text.strip()
        return [t.strip().lower() for t in raw.split(",") if t.strip()][:6]
    except Exception:
        return []


def _n_questions(word_count):
    if word_count < 100:
        return 2
    if word_count < 400:
        return 3
    return 5


def _split_questions(raw):
    """Split LLM response on ---QUESTIONS--- delimiter. Returns (summary_str, questions_list)."""
    if "---QUESTIONS---" not in raw:
        return raw.strip(), []
    summary_part, q_part = raw.split("---QUESTIONS---", 1)
    questions = []
    for line in q_part.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line[0].isdigit():
            line = line.split(".", 1)[-1].strip()
        if line:
            questions.append(line)
    return summary_part.strip(), questions[:5]


def summarize_article(article: dict, text: str, detail: str = "medium") -> dict:
    """Summarise article text with Claude. Returns same shape as summarizer.fetch_and_summarize()."""
    instruction, max_tokens, char_limit = _DETAIL_SPECS.get(detail, _DETAIL_SPECS["medium"])
    text = (text or "").strip()

    if len(text) < 40:
        return {
            "transcript": text,
            "summary":    "",
            "tags":       [],
            "questions":  [],
            "source":     "apify_google_search",
            "wordCount":  0,
            "language":   "en",
            "error":      "Not enough article text to summarize (page may be paywalled or blocked).",
        }

    word_count = len(text.split())
    n = _n_questions(word_count)
    q_suffix = (
        f"\n\n---QUESTIONS---\n"
        f"Generate exactly {n} questions this content directly answers. "
        f"Write specific questions a researcher would search for. "
        f"Number them 1-{n}. Output only the questions, no preamble."
    )
    user_msg = (
        f"Title: {article.get('title', '')}\n"
        f"Source: {article.get('domain', '')}\n"
        f"Published: {article.get('date', 'unknown')}\n\n"
        f"{instruction}\n\n"
        f"Material:\n{text[:char_limit]}"
        f"{q_suffix}"
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=max_tokens + 400,
        )
        summary, questions = _split_questions(resp.content[0].text.strip())
    except Exception as e:
        summary   = f"_Summary failed: {e}_"
        questions = []

    tags = _generate_tags(summary)

    return {
        "transcript": text,
        "summary":    summary,
        "tags":       tags,
        "questions":  questions,
        "source":     "apify_google_search",
        "wordCount":  word_count,
        "language":   "en",
        "error":      "",
    }


def fetch_and_summarize(article: dict, detail: str = "medium") -> dict:
    """Fetch article text then summarise. Main entry point for /google/summary."""
    text, fetch_err = fetch_article_text(article["url"])
    result = summarize_article(article, text, detail)
    if fetch_err and not text:
        result["error"] = f"Could not fetch article: {fetch_err}"
    result["search_term"] = article.get("search_term", "")
    return result


# ─────────────────────────────────────────────
# Parallel search across topics
# ─────────────────────────────────────────────

def run_topics(
    term_pairs: list[tuple[str, str]],
    max_results: int = 6,
    date_filter: str = "",
    max_workers: int = 4,
) -> tuple[dict, dict]:
    """Search each term in parallel. Returns (results, errors).

    results = {label: {"results": [article, ...]}}
    errors  = {label: error_message}
    """
    results: dict = {}
    errors:  dict = {}

    def _search(label, query):
        return label, search_google(query, max_results=max_results, date_filter=date_filter)

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [(label, ex.submit(_search, label, query)) for label, query in term_pairs]
        for label, fut in futures:
            try:
                _, articles = fut.result()
                results[label] = {"results": articles}
            except Exception as e:
                results[label] = {"results": []}
                errors[label]  = str(e)

    return results, errors
