"""Core logic: search YouTube via yt-dlp and summarize with Anthropic via Azure AI Foundry."""

import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import concurrent.futures as cf

_YTDLP = [sys.executable, "-m", "yt_dlp", "--no-check-certificates", "--no-check-formats"]

# Corporate SSL inspection proxy bypass
import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context

import anthropic as _anthropic_sdk
import httpx as _httpx

_anthropic = _anthropic_sdk.Anthropic(
    api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
    base_url=os.environ.get("ANTHROPIC_FOUNDRY_ENDPOINT", "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1"),
    http_client=_httpx.Client(verify=False),
)
_ANTHROPIC_DEPLOYMENT = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")

_APIFY_TOKEN = os.environ.get("APIFY_API_KEY", "")
_APIFY_BASE = "https://api.apify.com/v2/acts"
_TRANSCRIPT_ACTOR = "foudhil~actor-youtube-transcript"

_WATCH_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
DEFAULT_DETAIL = "medium"


def _extract_vid(url):
    m = _WATCH_RE.search(url or "")
    return m.group(1) if m else None


def _cutoff_date(date_filter):
    """Return YYYYMMDD string for earliest allowed upload_date, or None."""
    if not date_filter:
        return None
    today = datetime.date.today()
    delta = {
        "hour":  datetime.timedelta(days=1),
        "today": datetime.timedelta(days=1),
        "week":  datetime.timedelta(days=7),
        "month": datetime.timedelta(days=30),
        "year":  datetime.timedelta(days=365),
    }.get(date_filter)
    return (today - delta).strftime("%Y%m%d") if delta else None


def _fmt_duration(secs):
    if not secs:
        return ""
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _fmt_date(upload_date, timestamp=None):
    """Convert YYYYMMDD to YYYY-MM-DD, or fall back to Unix timestamp."""
    d = upload_date or ""
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    if timestamp:
        try:
            return datetime.datetime.utcfromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return d


def _yyyymmdd(upload_date, timestamp=None):
    """Return YYYYMMDD string for date-filter comparisons."""
    d = upload_date or ""
    if len(d) == 8:
        return d
    if timestamp:
        try:
            return datetime.datetime.utcfromtimestamp(int(timestamp)).strftime("%Y%m%d")
        except Exception:
            pass
    return ""


def _best_thumbnail(data):
    thumbs = data.get("thumbnails") or []
    if not thumbs:
        return data.get("thumbnail") or ""
    candidates = [t for t in thumbs if 300 <= (t.get("width") or 0) <= 700]
    bucket = candidates or thumbs
    return max(bucket, key=lambda t: t.get("width") or 0).get("url", "")


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

_YT_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
_YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

_DATE_FILTER_ISO = {
    "hour":  lambda: (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "today": lambda: (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "week":  lambda: (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "month": lambda: (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "year":  lambda: (datetime.datetime.utcnow() - datetime.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
}

_SORT_MAP = {
    "relevance": "relevance",
    "date":      "date",
    "views":     "viewCount",
    "rating":    "rating",
}


def _yt_api_get(url, params):
    """GET a YouTube API URL with params, return parsed JSON."""
    import urllib.parse
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def _search_yt_api(keyword, max_results=6, date_filter=None, sort_order="relevance"):
    """Search via YouTube Data API v3 — returns full metadata including dates."""
    params = {
        "part": "snippet",
        "q": keyword,
        "type": "video",
        "maxResults": min(max_results, 50),
        "order": _SORT_MAP.get(sort_order, "relevance"),
        "videoDuration": "medium",   # excludes Shorts (<4 min) and very long (>20 min) — use 'any' for all
        "key": _YT_API_KEY,
    }
    # Add date filter if specified
    published_after = _DATE_FILTER_ISO.get(date_filter)
    if published_after:
        params["publishedAfter"] = published_after()

    # Override videoDuration to 'any' so we also get long videos
    params["videoDuration"] = "any"

    data = _yt_api_get(_YT_SEARCH_URL, params)
    items = data.get("items", [])

    if not items:
        return []

    # Fetch view counts + duration via videos.list (search results don't include them)
    video_ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    stats = {}
    if video_ids:
        vdata = _yt_api_get(_YT_VIDEOS_URL, {
            "part": "statistics,contentDetails",
            "id": ",".join(video_ids),
            "key": _YT_API_KEY,
        })
        for v in vdata.get("items", []):
            vid = v["id"]
            dur_iso = v.get("contentDetails", {}).get("duration", "")
            stats[vid] = {
                "views": int(v.get("statistics", {}).get("viewCount", 0)),
                "likes": int(v.get("statistics", {}).get("likeCount", 0)),
                "duration": _parse_iso_duration(dur_iso),
                "duration_s": _iso_duration_secs(dur_iso),
            }

    videos = []
    for it in items:
        vid = it.get("id", {}).get("videoId")
        if not vid:
            continue
        snip = it.get("snippet", {})
        s = stats.get(vid, {})

        # Skip Shorts (< 61 seconds)
        if 0 < s.get("duration_s", 999) < 61:
            continue

        pub = (snip.get("publishedAt") or "")[:10]   # YYYY-MM-DD
        thumb = (
            snip.get("thumbnails", {}).get("high", {}).get("url")
            or snip.get("thumbnails", {}).get("medium", {}).get("url")
            or snip.get("thumbnails", {}).get("default", {}).get("url")
            or ""
        )
        videos.append({
            "id": vid,
            "title": snip.get("title") or "(untitled)",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": snip.get("channelTitle") or "",
            "views": s.get("views", 0),
            "likes": s.get("likes", 0),
            "date": pub,
            "duration": s.get("duration", ""),
            "subscribers": 0,
            "thumbnail": thumb,
            "description": (snip.get("description") or "")[:3000],
        })

    return videos[:max_results]


def _parse_iso_duration(iso):
    """Convert ISO 8601 duration (PT1H2M3S) to human-readable string."""
    if not iso:
        return ""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return ""
    h, mn, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return f"{h}h {mn}m" if h else f"{mn}m"


def _iso_duration_secs(iso):
    """Convert ISO 8601 duration to total seconds."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mn, s = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


def _search_ytdlp(keyword, max_results=6, date_filter=None, sort_order="relevance"):
    """Fallback search via yt-dlp flat-playlist (no dates or descriptions)."""
    fetch_n = max_results * 3 if date_filter else max_results + 2
    cmd = _YTDLP + [
        "--dump-json", "--flat-playlist", "--no-warnings", "--quiet",
        f"ytsearch{fetch_n}:{keyword}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp search timed out after 120s")

    cutoff = _cutoff_date(date_filter)
    videos = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = data.get("id")
        if not vid:
            continue
        dur_s = data.get("duration") or 0
        if 0 < dur_s < 61:
            continue
        ts = data.get("timestamp") or data.get("release_timestamp")
        if cutoff:
            vid_date = _yyyymmdd(data.get("upload_date"), ts)
            if vid_date and vid_date < cutoff:
                continue
        videos.append({
            "id": vid,
            "title": data.get("title") or "(untitled)",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": data.get("channel") or data.get("uploader") or "",
            "views": data.get("view_count") or 0,
            "likes": data.get("like_count") or 0,
            "date": _fmt_date(data.get("upload_date"), ts),
            "duration": _fmt_duration(dur_s),
            "subscribers": data.get("channel_follower_count") or 0,
            "thumbnail": _best_thumbnail(data),
            "description": (data.get("description") or "")[:3000],
        })
        if len(videos) >= max_results:
            break
    if sort_order == "views":
        videos.sort(key=lambda x: x["views"], reverse=True)
    elif sort_order == "date":
        videos.sort(key=lambda x: x["date"], reverse=True)
    return videos


def search_videos(keyword, max_results=6, date_filter=None, sort_order="relevance"):
    """Dispatcher: YouTube Data API v3 if key present, else yt-dlp fallback."""
    if _YT_API_KEY:
        return _search_yt_api(keyword, max_results, date_filter, sort_order)
    return _search_ytdlp(keyword, max_results, date_filter, sort_order)


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #

def _fetch_transcript_text(video_id):
    """Return (transcript_text, language_code) or ('', '') on failure."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return "", ""

    try:
        # Pass a no-verify httpx client to handle corporate SSL inspection proxies.
        # youtube-transcript-api 1.x uses httpx internally which ignores the urllib SSL patch.
        try:
            import httpx
            client = httpx.Client(verify=False)
            tlist = YouTubeTranscriptApi(http_client=client).list(video_id)
        except (TypeError, AttributeError):
            # Older API or httpx not available — fall back to default client
            try:
                tlist = YouTubeTranscriptApi().list(video_id)
            except (AttributeError, TypeError):
                tlist = YouTubeTranscriptApi.list_transcripts(video_id)
    except Exception:
        return "", ""

    transcript_obj = None
    for t in tlist:
        if not getattr(t, "is_translation", False):
            transcript_obj = t
            break
    if transcript_obj is None:
        try:
            transcript_obj = next(iter(tlist))
        except StopIteration:
            return "", ""

    try:
        segments = transcript_obj.fetch()
    except Exception:
        return "", ""

    use_dict = isinstance(segments[0], dict) if segments else False
    texts = [s["text"] if use_dict else s.text for s in segments]
    return " ".join(texts), transcript_obj.language_code


def fetch_transcript(url):
    """Return transcript text for a watch URL, or '' if unavailable."""
    vid = _extract_vid(url)
    if not vid:
        return ""
    text, _ = _fetch_transcript_text(vid)
    return text


def _fetch_transcript_apify(url):
    """Fetch transcript via Apify transcript actor. Returns text or ''."""
    if not _APIFY_TOKEN:
        return ""
    try:
        import requests as _req
        resp = _req.post(
            f"{_APIFY_BASE}/{_TRANSCRIPT_ACTOR}/run-sync-get-dataset-items",
            params={"token": _APIFY_TOKEN, "timeout": 120},
            json={
                "videoUrl": url,
                "includeTimestamps": False,
                "languages": ["en", "en-US", "en-GB"],
                "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
            },
            timeout=180,
            verify=False,
        )
        resp.raise_for_status()
        items = resp.json()
        for it in items:
            for key in ("transcript", "text", "captions"):
                val = it.get(key)
                if isinstance(val, str) and val.strip():
                    return val
                if isinstance(val, list):
                    parts = [s.get("text", "") if isinstance(s, dict) else str(s) for s in val]
                    joined = " ".join(p for p in parts if p)
                    if joined.strip():
                        return joined
    except Exception:
        return ""
    return ""


def fetch_and_summarize(video, detail=DEFAULT_DETAIL):
    """On-demand: fetch transcript for one video and summarize it.

    Tries youtube-transcript-api first (free); falls back to Apify if key present.
    Returns dict: {transcript, summary, source, wordCount, language, error}
    """
    vid = video.get("id") or _extract_vid(video.get("url", ""))
    url = video.get("url", f"https://www.youtube.com/watch?v={vid}")

    text, lang = _fetch_transcript_text(vid)
    source = "youtube-transcript-api"

    if not text and _APIFY_TOKEN:
        text = _fetch_transcript_apify(url)
        source = "apify"
        lang = ""

    if not text:
        msg = "No transcript available."
        if not _APIFY_TOKEN:
            msg += " Add APIFY_API_KEY to .env to enable Apify fallback."
        return {"transcript": "", "summary": "", "source": "", "wordCount": 0, "language": "", "error": msg}

    word_count = len(text.split())
    summary, questions = summarize_video(video, text, detail=detail, word_count=word_count)
    tags = _generate_tags(summary)
    return {
        "transcript": text,
        "summary":    summary,
        "tags":       tags,
        "questions":  questions,
        "source":     source,
        "wordCount":  word_count,
        "language":   lang,
        "error":      "",
    }


def fetch_transcript_full(url):
    """Fetch transcript + metadata for ad-hoc single-video tool.

    Returns dict: {transcript, wordCount, durationMinutes, language,
    videoId, status, error}
    """
    out = {
        "transcript": "", "wordCount": 0, "durationMinutes": 0,
        "language": "", "videoId": "", "status": "", "error": "",
    }
    vid = _extract_vid(url)
    if not vid:
        out["error"] = "Could not extract a YouTube video ID from that URL."
        return out

    out["videoId"] = vid
    text, lang = _fetch_transcript_text(vid)
    if not text:
        out["error"] = "No transcript available for this video (captions may be disabled)."
        return out

    out["transcript"] = text
    out["language"] = lang
    out["wordCount"] = len(text.split())
    out["status"] = "ok"

    # Estimate duration from yt-dlp (best effort, non-blocking)
    try:
        proc = subprocess.run(
            _YTDLP + ["--dump-json", "--quiet", "--no-warnings",
                      "--skip-download", f"https://www.youtube.com/watch?v={vid}"],
            capture_output=True, text=True, timeout=30,
        )
        meta = json.loads(proc.stdout.strip())
        dur_s = meta.get("duration") or 0
        out["durationMinutes"] = round(dur_s / 60)
    except Exception:
        out["durationMinutes"] = round(out["wordCount"] / 130)  # ~130 wpm fallback

    return out


# --------------------------------------------------------------------------- #
# Summarization
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "You are a market-intelligence analyst. Summarize the YouTube video for a "
    "professional tracking this topic. Be factual and specific — no hype, no "
    "'great video' language. Prefer concrete details: numbers, tools, product "
    "names, steps, and claims. Base it ONLY on the supplied material; if "
    "information is thin, say so briefly rather than padding."
)

DETAIL_SPECS = {
    "low": (
        "Output a one-sentence TL;DR, then 2-3 short bullet points of the most "
        "important takeaways.",
        320, 6000,
    ),
    "medium": (
        "Output a one-sentence TL;DR, then 4-6 bullet points capturing the key "
        "concrete takeaways with specifics.",
        550, 9000,
    ),
    "high": (
        "Output a 1-2 sentence TL;DR, then 8-12 detailed bullet points covering "
        "all key concepts, examples, numbers, tools/products named, and "
        "actionable takeaways. Group bullets under short bold sub-headings when "
        "the content has distinct themes.",
        2000, 16000,
    ),
}
DEFAULT_DETAIL = "medium"


def _detail_spec(detail):
    return DETAIL_SPECS.get(detail, DETAIL_SPECS[DEFAULT_DETAIL])


def _generate_tags(summary):
    """Ask the LLM for up to 6 tags from the summary. Returns a list of strings."""
    if not summary or summary.startswith("_"):
        return []
    try:
        resp = _anthropic.messages.create(
            model=_ANTHROPIC_DEPLOYMENT,
            system="You are a tagging assistant. Return only a comma-separated list of tags, nothing else.",
            messages=[{"role": "user", "content": (
                f"From the following video summary, generate up to 6 concise, lowercase tags "
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


def summarize_video(video, source_text, detail=DEFAULT_DETAIL, word_count=0):
    source_text = (source_text or "").strip()
    if len(source_text) < 40:
        return "_Not enough text available to summarize (no description/transcript)._", []
    instruction, max_tokens, char_limit = _detail_spec(detail)
    source_text = source_text[:char_limit]
    n = _n_questions(word_count)
    q_suffix = (
        f"\n\n---QUESTIONS---\n"
        f"Generate exactly {n} questions this content directly answers. "
        f"Write specific questions a researcher would search for. "
        f"Number them 1-{n}. Output only the questions, no preamble."
    )
    user = (
        f"Title: {video['title']}\n"
        f"Channel: {video['channel']}\n"
        f"Views: {video['views']:,} | Published: {video['date']} | Duration: {video['duration']}\n\n"
        f"{instruction}\n\n"
        f"Material:\n{source_text}"
        f"{q_suffix}"
    )
    try:
        resp = _anthropic.messages.create(
            model=_ANTHROPIC_DEPLOYMENT,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens + 400,
        )
        return _split_questions(resp.content[0].text.strip())
    except Exception as e:
        return f"_Summary failed: {e}_", []


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_terms(terms, max_results=6, date_filter=None, sort_order="relevance", max_workers=4):
    """Search each term, return video lists. Summaries are fetched on demand."""
    results = {}
    errors = {}

    def _search(label, query):
        return label, search_videos(query, max_results=max_results,
                                    date_filter=date_filter, sort_order=sort_order)

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [(label, ex.submit(_search, label, query)) for label, query in terms]
        for label, fut in futures:
            try:
                _, vids = fut.result()
                results[label] = vids
            except Exception as e:
                results[label] = []
                errors[label] = str(e)

    videos_by_id = {}
    for vids in results.values():
        for v in vids:
            videos_by_id.setdefault(v["id"], v)

    out = {}
    for term, vids in results.items():
        enriched = [videos_by_id[v["id"]] for v in vids]
        top = enriched
        trending = sorted(enriched, key=lambda x: x.get("views", 0), reverse=True)
        out[term] = {"top": top, "trending": trending}

    return out, videos_by_id, errors


# --------------------------------------------------------------------------- #
# Ad-hoc single URL
# --------------------------------------------------------------------------- #

def normalize_watch_url(url):
    url = (url or "").strip()
    m = _WATCH_RE.search(url)
    if not m:
        return None, None
    vid = m.group(1)
    return f"https://www.youtube.com/watch?v={vid}", vid


def summarize_url(url, detail=DEFAULT_DETAIL):
    """Fetch transcript for one URL and summarize it."""
    watch_url, vid = normalize_watch_url(url)
    if not watch_url:
        return {"error": "That doesn't look like a YouTube video URL.", "url": url}

    tx = fetch_transcript_full(watch_url)
    result = {
        "url": watch_url,
        "videoId": vid,
        "wordCount": tx.get("wordCount", 0),
        "durationMinutes": tx.get("durationMinutes", 0),
        "language": tx.get("language", ""),
        "transcript": tx.get("transcript", ""),
        "summary": "",
        "error": "",
    }
    if not result["transcript"]:
        result["error"] = tx.get("error") or "No transcript available for this video."
        return result

    pseudo = {
        "title": f"Video {vid}", "channel": "", "views": 0,
        "date": "", "duration": f"{result['durationMinutes']} min",
    }
    result["summary"] = summarize_video(pseudo, result["transcript"], detail=detail)
    return result
