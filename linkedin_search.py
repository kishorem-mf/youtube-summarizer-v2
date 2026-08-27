"""LinkedIn post search via Apify actor + Claude summarisation.

Actor: apimaestro/linkedin-posts-search-scraper-no-cookies
No LinkedIn cookies required.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import os
import re

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

APIFY_TOKEN  = os.environ.get("APIFY_API_KEY", "")
ACTOR_ID     = "apimaestro~linkedin-posts-search-scraper-no-cookies"
APIFY_URL    = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"

# ─────────────────────────────────────────────
# Date filter mapping  (UI label → actor value)
# ─────────────────────────────────────────────

_DATE_MAP = {
    "":      None,
    "today": "past-24h",
    "week":  "past-week",
    "month": "past-month",
    "year":  None,   # actor has no yearly filter; fall back to no filter
}

# ─────────────────────────────────────────────
# Summarisation specs
# ─────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a market-intelligence analyst. Summarize the LinkedIn post for a "
    "professional tracking this topic. Be factual and specific — no hype, no "
    "filler. Prefer concrete details: numbers, tools, product names, claims. "
    "Base it ONLY on the supplied material; if content is thin, say so briefly."
)

_DETAIL_SPECS = {
    "low":    ("Output a one-sentence TL;DR, then 2-3 short bullet points of the most important takeaways.", 640),
    "medium": ("Output a one-sentence TL;DR, then 4-6 bullet points capturing the key concrete takeaways with specifics.", 1100),
    "high":   ("Output a 1-2 sentence TL;DR, then 8-12 detailed bullet points covering all key concepts, examples, numbers, and actionable takeaways. Group bullets under short bold sub-headings when the content has distinct themes.", 2000),
}


def _post_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _extract(post: dict, key: str, *fallbacks: str, default=""):
    """Try multiple field name variants, return first non-empty value."""
    for k in (key, *fallbacks):
        v = post.get(k)
        if v not in (None, "", 0):
            return v
    return default


# ─────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────

_HIRING_KEYWORDS = {
    "hiring", "we're hiring", "we are hiring", "now hiring", "job opening",
    "open position", "open role", "apply now", "join our team", "looking to hire",
    "#hiring", "#jobopening", "#jobalert", "#jobs", "#recruitment", "#recruiting",
}


def _is_hiring_post(text: str) -> bool:
    """Return True if the post looks like a job/hiring advertisement."""
    lower = text.lower()
    return any(kw in lower for kw in _HIRING_KEYWORDS)


_EN_STOPWORDS = {
    "the", "and", "to", "of", "a", "in", "is", "for", "on", "with", "this",
    "that", "it", "as", "are", "we", "you", "your", "our", "how", "what", "why",
}


def _looks_english(text: str) -> bool:
    """Best-effort English check, no external deps.

    Rejects text dominated by non-Latin scripts (CJK, Arabic, Cyrillic,
    Devanagari…) and longer Latin text that lacks common English stopwords
    (filters Spanish/French/German/etc.). Short/empty text is kept to avoid
    over-filtering.
    """
    if not text:
        return True
    letters = [c for c in text if c.isalpha()]
    if letters:
        non_latin = sum(1 for c in letters if ord(c) > 0x024F)
        if non_latin / len(letters) > 0.20:
            return False
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) >= 12:
        return sum(1 for w in words if w in _EN_STOPWORDS) >= 2
    return True


def search_linkedin(query: str, max_results: int = 10, date_filter: str = "") -> list[dict]:
    """Search LinkedIn posts via Apify actor. Returns list of post dicts."""
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_API_KEY not set in environment.")

    actor_date = _DATE_MAP.get(date_filter)

    # Exclude hiring/job posts at the query level using LinkedIn boolean syntax
    excluded_query = query + " -hiring -recruiting -jobalert"

    # Over-fetch to compensate for posts dropped by the hiring filter
    fetch_n = min(50, max_results * 2)

    payload: dict = {
        "keyword":     excluded_query,
        "sort_type":   "relevance",
        "total_posts": fetch_n,
    }
    if actor_date:
        payload["date_filter"] = actor_date

    try:
        r = _req.post(
            APIFY_URL,
            params={"token": APIFY_TOKEN, "timeout": 120},
            json=payload,
            timeout=130,
            verify=False,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        raise RuntimeError(f"Apify LinkedIn search failed: {e}") from e

    posts = []
    for item in raw:
        # Actor output: post_url, author{name,headline,profile_url}, stats{total_reactions,comments,shares}, posted_at
        url = _extract(item, "post_url", "url", "postUrl", "linkedinUrl", "link")
        if not url:
            continue

        author   = item.get("author") or {}
        stats    = item.get("stats")  or {}
        name     = _extract(author, "name", "fullName") or _extract(item, "authorName", "authorFullName")
        headline = _extract(author, "headline", "title") or _extract(item, "authorHeadline", "authorTitle")
        profile  = _extract(author, "profile_url", "profileUrl", "url") or _extract(item, "authorProfileUrl")

        likes    = _extract(stats, "total_reactions", "likes", default=0) or _extract(item, "likesCount", "numLikes", default=0)
        comments = _extract(stats, "comments", default=0) or _extract(item, "commentsCount", "numComments", default=0)
        shares   = _extract(stats, "shares",   default=0) or _extract(item, "sharesCount",   "numShares",   default=0)
        _date_raw = item.get("posted_at") or _extract(item, "postedAt", "postedAtISO", "date", "publishedAt")
        if isinstance(_date_raw, dict):
            date_str = _date_raw.get("date") or _date_raw.get("display_text") or ""
        else:
            date_str = str(_date_raw) if _date_raw else ""

        text = _extract(item, "text", "content", "postText", "body")

        if _is_hiring_post(text):
            continue

        # English-only: drop posts with clearly non-English text
        if not _looks_english(text):
            continue

        posts.append({
            "id":          _post_id(url),
            "url":         url,
            "text":        text,
            "author":      name,
            "headline":    headline,
            "profileUrl":  profile,
            "date":        date_str,
            "likes":       int(likes)    if likes    else 0,
            "comments":    int(comments) if comments else 0,
            "shares":      int(shares)   if shares   else 0,
            "search_term": query,
        })
        if len(posts) >= max_results:
            break
    return posts


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
                f"From the following LinkedIn post summary, generate up to 6 concise, lowercase tags. "
                f"No hashtags.\n\n{summary[:1000]}"
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


_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")


def _extract_article_url(text: str) -> str:
    """First external (non-linkedin.com) URL in the post text, or ''. lnkd.in
    wrappers are kept — they redirect to the real article."""
    for raw in _URL_RE.findall(text or ""):
        u = raw.rstrip(".,;)]}\"'")
        if "linkedin.com" in u.lower():   # skip profile/company/post self-links
            continue
        return u
    return ""


def _resolve_external_url(url: str) -> str:
    """Resolve a LinkedIn lnkd.in short link to its true external destination.

    lnkd.in does NOT 302 straight to the target — it serves an interstitial page
    with the real URL embedded. We fetch it and pull out the first non-LinkedIn
    URL. Returns '' if the link resolves back into LinkedIn (jobs/feed/etc.)."""
    import html as _html
    try:
        r = _req.get(url, timeout=25, verify=False, allow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; summarizer-bot/1.0)"})
    except Exception:
        return ""
    final = (r.url or url).lower()
    _skip = ("linkedin.com", "lnkd.in", "licdn.com")
    _asset = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico", ".woff")
    if "lnkd.in" in final:                       # interstitial — dig the real URL out of the HTML
        for u in re.findall(r'https?://[^\s"\'<>]+', r.text):
            u = _html.unescape(u.rstrip(".,;)]}\"'"))
            lu = u.lower()
            if not any(d in lu for d in _skip) and not lu.endswith(_asset):
                return u
        return ""
    if "linkedin.com" in final:                  # resolved to a LinkedIn-native page, not an article
        return ""
    return r.url or url


def _fetch_linked_article(text: str):
    """Best-effort fetch of the first external article linked in the post.
    Resolves lnkd.in shortlinks first, then reuses google_search.fetch_article_text.
    Returns (article_text, resolved_url)."""
    url = _extract_article_url(text)
    if not url:
        return "", ""
    real = _resolve_external_url(url) if "lnkd.in" in url.lower() else url
    if not real:
        return "", ""
    try:
        import google_search as _gs
        atext, _err = _gs.fetch_article_text(real)
        return (atext or ""), real
    except Exception:
        return "", real


def summarize_post(post: dict, detail: str = "medium") -> dict:
    """Summarise a LinkedIn post with Claude. If the post links to an external
    article, that article's text is fetched and summarized too."""
    instruction, max_tokens = _DETAIL_SPECS.get(detail, _DETAIL_SPECS["medium"])
    text = (post.get("text") or "").strip()

    if len(text) < 20:
        return {
            "transcript": text,
            "summary":    "",
            "tags":       [],
            "questions":  [],
            "source":     "apify_linkedin_search",
            "wordCount":  0,
            "language":   "en",
            "error":      "Post text is too short to summarize.",
        }

    # Open any linked article and pull its content in as extra material
    article_text, article_url = _fetch_linked_article(text)
    has_article = len(article_text) > 200

    word_count = len((text + " " + article_text).split())
    n = _n_questions(word_count)
    q_suffix = (
        f"\n\n---QUESTIONS---\n"
        f"Generate exactly {n} questions this content directly answers. "
        f"Write specific questions a researcher would search for. "
        f"Number them 1-{n}. Output only the questions, no preamble."
    )
    if has_article:
        instruction = (instruction + " The post links to an external article whose "
                       "full text is included below — base the summary primarily on "
                       "that article, using the post as framing/context.")
    material = f"Post:\n{text[:12000]}"
    if has_article:
        material += f"\n\nLinked article ({article_url}):\n{article_text[:14000]}"
    user_msg = (
        f"Author: {post.get('author', 'Unknown')}\n"
        f"Headline: {post.get('headline', '')}\n"
        f"Posted: {post.get('date', 'unknown')}\n"
        f"Engagement: {post.get('likes', 0)} likes, {post.get('comments', 0)} comments\n\n"
        f"{instruction}\n\n"
        f"{material}"
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
        "source":     "apify_linkedin_search",
        "wordCount":  word_count,
        "language":   "en",
        "error":      "",
    }


# ─────────────────────────────────────────────
# Parallel search across topics
# ─────────────────────────────────────────────

def run_topics(
    term_pairs: list[tuple[str, str]],
    max_results: int = 10,
    date_filter: str = "",
    max_workers: int = 4,
) -> tuple[dict, dict]:
    """Search each term in parallel. Returns (results, errors)."""
    results: dict = {}
    errors:  dict = {}

    def _search(label, query):
        return label, search_linkedin(query, max_results=max_results, date_filter=date_filter)

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [(label, ex.submit(_search, label, query)) for label, query in term_pairs]
        for label, fut in futures:
            try:
                _, posts = fut.result()
                results[label] = {"results": posts}
            except Exception as e:
                results[label] = {"results": []}
                errors[label]  = str(e)

    return results, errors
