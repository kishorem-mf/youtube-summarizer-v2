"""Core logic: search YouTube via yt-dlp and summarize with Azure OpenAI."""

import datetime
import json
import os
import re
import subprocess
import concurrent.futures as cf

from openai import AzureOpenAI

_azure = AzureOpenAI(
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
)
_AZURE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o-mini")

_WATCH_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


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


def _fmt_date(upload_date):
    """Convert YYYYMMDD to YYYY-MM-DD."""
    d = upload_date or ""
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


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

def search_videos(keyword, max_results=6, date_filter=None, sort_order="relevance"):
    """Search YouTube using yt-dlp ytsearch and return normalized video dicts."""
    fetch_n = max_results * 3 if date_filter else max_results + 2
    cmd = [
        "yt-dlp", "--dump-json", "--no-warnings", "--quiet",
        f"ytsearch{fetch_n}:{keyword}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp search timed out after 120s")
    except FileNotFoundError:
        raise RuntimeError("yt-dlp not installed — pip install yt-dlp")

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

        # Skip Shorts (very short videos)
        dur_s = data.get("duration") or 0
        if 0 < dur_s < 61:
            continue

        # Date filter
        if cutoff:
            upload_date = data.get("upload_date") or ""
            if upload_date and upload_date < cutoff:
                continue

        videos.append({
            "id": vid,
            "title": data.get("title") or "(untitled)",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": data.get("channel") or data.get("uploader") or "",
            "views": data.get("view_count") or 0,
            "likes": data.get("like_count") or 0,
            "date": _fmt_date(data.get("upload_date") or ""),
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
            ["yt-dlp", "--dump-json", "--quiet", "--no-warnings",
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
        1100, 16000,
    ),
}
DEFAULT_DETAIL = "medium"


def _detail_spec(detail):
    return DETAIL_SPECS.get(detail, DETAIL_SPECS[DEFAULT_DETAIL])


def summarize_video(video, source_text, detail=DEFAULT_DETAIL):
    source_text = (source_text or "").strip()
    if len(source_text) < 40:
        return "_Not enough text available to summarize (no description/transcript)._"
    instruction, max_tokens, char_limit = _detail_spec(detail)
    source_text = source_text[:char_limit]
    user = (
        f"Title: {video['title']}\n"
        f"Channel: {video['channel']}\n"
        f"Views: {video['views']:,} | Published: {video['date']} | Duration: {video['duration']}\n\n"
        f"{instruction}\n\n"
        f"Material:\n{source_text}"
    )
    try:
        resp = _azure.chat.completions.create(
            model=_AZURE_DEPLOYMENT,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"_Summary failed: {e}_"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_terms(terms, max_results=6, deep=False, date_filter=None,
              sort_order="relevance", detail=DEFAULT_DETAIL, max_workers=4):
    """Search each term, summarize unique videos, return structured results."""
    results = {}
    errors = {}

    def _search(label, query):
        return label, search_videos(
            query, max_results=max_results,
            date_filter=date_filter, sort_order=sort_order,
        )

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

    if deep:
        def _tx(v):
            return v["id"], fetch_transcript(v["url"])

        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for vid, fut in [(v["id"], ex.submit(_tx, v)) for v in videos_by_id.values()]:
                try:
                    videos_by_id[vid]["transcript"] = fut.result()[1]
                except Exception:
                    videos_by_id[vid]["transcript"] = ""

    def _sum(v):
        src = v.get("transcript") or v.get("description")
        return v["id"], summarize_video(v, src, detail=detail)

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for vid, fut in [(v["id"], ex.submit(_sum, v)) for v in videos_by_id.values()]:
            try:
                videos_by_id[vid]["summary"] = fut.result()[1]
            except Exception as e:
                videos_by_id[vid]["summary"] = f"_Summary failed: {e}_"

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
