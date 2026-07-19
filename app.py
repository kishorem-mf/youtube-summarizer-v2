"""Local web app: pick search terms, fetch top + trending YouTube videos,
and view AI summaries. Run with `python app.py` then open localhost:8051.

Note: port 8051 is used by default so this can run alongside the original
app on port 8050. Override with the PORT env var if needed.
"""

import io
import os
import re
import sys
import threading
import datetime as dt

# Force UTF-8 stdout/stderr so Windows cp1252 console doesn't choke on emoji in video titles
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()  # must run before importing summarizer (reads env at import)

from flask import Flask, render_template, request, redirect, url_for  # noqa: E402

import search_terms  # noqa: E402
import summarizer  # noqa: E402
import storage     # noqa: E402

app = Flask(__name__)
DEFAULT_MAX = int(os.environ.get("DEFAULT_MAX_RESULTS", "6"))

# In-process cache for LinkedIn posts (keyed by post id) so full text
# survives the redirect to the post detail page without URL-encoding it.
_li_post_cache: dict = {}

# In-process cache for ad-hoc research results (keyed by video id) so the
# "Save to Reading" button can persist the already-computed summary without
# round-tripping the full transcript through the form.
_adhoc_cache: dict = {}

# Upload-date windows offered in the UI -> yt-dlp dateFilter values.
DATE_WINDOWS = [
    ("", "Any time"),
    ("today", "Last 24 hours"),
    ("week", "Last week"),
    ("month", "Last month"),
    ("year", "Last year"),
]
SORT_OPTIONS = [
    ("relevance", "Relevance"),
    ("date", "Upload date"),
    ("views", "View count"),
]
DETAIL_OPTIONS = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
]
DEFAULT_DATE_FILTER = "week"
DEFAULT_SORT = "relevance"
DEFAULT_DETAIL = "high"
OUTPUTS = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)


@app.template_filter("mdlite")
def mdlite(text):
    """Render the LLM's lightweight markdown as safe HTML: # headings,
    - / * / • and 1. list items, and **bold**. Everything else is escaped."""
    from markupsafe import escape, Markup

    def inline(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", str(escape(s)))

    html, list_type = [], None

    def close_list():
        nonlocal list_type
        if list_type:
            html.append(f"</{list_type}>")
            list_type = None

    for raw in str(text or "").split("\n"):
        line = raw.strip()
        if not line:
            close_list()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_list()
            level = min(len(m.group(1)) + 2, 6)   # '#' -> <h3>
            html.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            continue
        m = re.match(r"^[-*•]\s+(.*)$", line)
        if m:
            if list_type != "ul":
                close_list(); html.append("<ul>"); list_type = "ul"
            html.append(f"<li>{inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+[.)]\s+(.*)$", line)
        if m:
            if list_type != "ol":
                close_list(); html.append("<ol>"); list_type = "ol"
            html.append(f"<li>{inline(m.group(1))}</li>")
            continue
        close_list()
        html.append(f"<p>{inline(line)}</p>")
    close_list()
    return Markup("".join(html))


@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        active_tab="search",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        sort_order=DEFAULT_SORT,
        detail=DEFAULT_DETAIL,
        search_mode="youtube",
        custom="",
        results=None,
        transcript_result=None,
    )


@app.route("/run", methods=["GET", "POST"])
def run():
    # A bare GET (e.g. browser refresh / typing /run in the address bar) has no
    # form data — send the user back to the form instead of erroring.
    if request.method == "GET":
        return redirect(url_for("index"))

    selected = request.form.getlist("terms")
    custom = (request.form.get("custom") or "").strip()
    if custom:
        selected = selected + [t.strip() for t in custom.split(",") if t.strip()]
    if not selected:
        selected = search_terms.all_terms()

    try:
        max_results = max(1, min(15, int(request.form.get("max_results", DEFAULT_MAX))))
    except ValueError:
        max_results = DEFAULT_MAX

    valid_dates = {v for v, _ in DATE_WINDOWS}
    date_filter = request.form.get("date_filter", DEFAULT_DATE_FILTER)
    if date_filter not in valid_dates:
        date_filter = DEFAULT_DATE_FILTER
    valid_sorts = {v for v, _ in SORT_OPTIONS}
    sort_order = request.form.get("sort_order", DEFAULT_SORT)
    if sort_order not in valid_sorts:
        sort_order = DEFAULT_SORT
    valid_details = {v for v, _ in DETAIL_OPTIONS}
    detail = request.form.get("detail", DEFAULT_DETAIL)
    if detail not in valid_details:
        detail = DEFAULT_DETAIL

    # Map each selected display label to its context-augmented query.
    term_pairs = [(label, search_terms.build_query(label)) for label in selected]

    results, videos_by_id, errors = summarizer.run_terms(
        term_pairs, max_results=max_results,
        date_filter=date_filter or None, sort_order=sort_order,
    )

    window_label = dict(DATE_WINDOWS).get(date_filter, "Any time")
    today = dt.date.today().isoformat()
    _save_digest(today, selected, results, window_label)

    return render_template(
        "index.html",
        active_tab="search",
        groups=search_terms.get_groups(),
        default_max=max_results,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=date_filter,
        sort_order=sort_order,
        detail=detail,
        search_mode="youtube",
        custom="",
        window_label=window_label,
        results=results,
        errors=errors,
        selected=selected,
        today=today,
        total_videos=len(videos_by_id),
        transcript_result=None,
    )


@app.route("/video_summary", methods=["POST"])
def video_summary():
    """On-demand: fetch transcript + summary for one video. Returns JSON."""
    from flask import jsonify
    data = request.get_json(force=True) or {}
    video = {
        "id":       data.get("video_id", ""),
        "url":      data.get("url", ""),
        "title":    data.get("title", ""),
        "channel":  data.get("channel", ""),
        "views":    int(data.get("views", 0)),
        "date":     data.get("date", ""),
        "duration": data.get("duration", ""),
    }
    valid_details = {v for v, _ in DETAIL_OPTIONS}
    detail = data.get("detail", DEFAULT_DETAIL)
    if detail not in valid_details:
        detail = DEFAULT_DETAIL

    cached = storage.check_cache(video["id"], detail)
    if cached:
        return jsonify(cached)

    result = summarizer.fetch_and_summarize(video, detail=detail)
    if not result.get("error"):
        result["search_term"] = data.get("search_term", "")
        threading.Thread(
            target=storage.save_result, args=(video, detail, result),
            kwargs={"source_platform": "youtube", "content_type": "video"},
            daemon=True,
        ).start()
    return jsonify(result)


@app.route("/video/<video_id>", methods=["GET"])
def video_page(video_id):
    """Standalone video page: shows metadata, YouTube embed, and detail selector for on-demand summarization."""
    from flask import jsonify
    video = {
        "id":          video_id,
        "title":       request.args.get("title", ""),
        "channel":     request.args.get("channel", ""),
        "views":       request.args.get("views", "0"),
        "date":        request.args.get("date", ""),
        "duration":    request.args.get("duration", ""),
        "search_term": request.args.get("search_term", ""),
        "url":      request.args.get("url", f"https://www.youtube.com/watch?v={video_id}"),
    }
    return render_template(
        "video_page.html",
        video=video,
        detail_options=DETAIL_OPTIONS,
        default_detail=DEFAULT_DETAIL,
    )


# ─────────────────────────────────────────────
# Agentic inbox: collect search hits → review → approve (summarize) → reading queue
# ─────────────────────────────────────────────

@app.route("/inbox/collect", methods=["POST"])
def inbox_collect():
    """Search the selected terms (no LLM) and drop each hit into the inbox.
    Source-aware: YouTube videos or LinkedIn posts depending on the `source` field."""
    source   = request.form.get("source", "youtube")
    selected = request.form.getlist("terms")
    custom = (request.form.get("custom") or "").strip()
    if custom:
        selected = selected + [t.strip() for t in custom.split(",") if t.strip()]
    if not selected:
        selected = search_terms.all_terms()

    cap = 50 if source == "linkedin" else 15
    try:
        max_results = max(1, min(cap, int(request.form.get("max_results", DEFAULT_MAX))))
    except ValueError:
        max_results = DEFAULT_MAX

    valid_dates = {v for v, _ in DATE_WINDOWS}
    date_filter = request.form.get("date_filter", DEFAULT_DATE_FILTER)
    if date_filter not in valid_dates:
        date_filter = DEFAULT_DATE_FILTER

    term_pairs = [(label, search_terms.build_query(label)) for label in selected]
    added = skipped = 0

    if source == "linkedin":
        import linkedin_search as lis
        results, errors = lis.run_topics(
            term_pairs, max_results=max_results, date_filter=date_filter or "",
        )
        for label, bucket in results.items():
            for p in bucket.get("results", []):
                text = p.get("text", "") or ""
                cand = {
                    "id":          p.get("id", ""),
                    "url":         p.get("url", ""),
                    "title":       (text[:90] or p.get("author", "") or "LinkedIn post"),
                    "channel":     p.get("author", ""),
                    "author":      p.get("author", ""),
                    "headline":    p.get("headline", ""),
                    "views":       int(p.get("likes", 0) or 0),
                    "date":        p.get("date", ""),
                    "duration":    "",
                    "thumbnail":   "",
                    "description": text,          # full post text — used later to summarize
                }
                if not cand["id"]:
                    continue
                if storage.save_candidate(cand, search_term=label,
                                          source_platform="linkedin_post", content_type="post"):
                    added += 1
                else:
                    skipped += 1
        return redirect(url_for("inbox_page", added=added, skipped=skipped))

    # Default: YouTube
    results, videos_by_id, errors = summarizer.run_terms(
        term_pairs, max_results=max_results,
        date_filter=date_filter or None, sort_order=DEFAULT_SORT,
    )
    label_by_id = {}
    for label, buckets in results.items():
        for bucket in ("top", "trending"):
            for v in buckets.get(bucket, []):
                label_by_id.setdefault(v["id"], label)
    for vid, video in videos_by_id.items():
        if storage.save_candidate(video, search_term=label_by_id.get(vid, "")):
            added += 1
        else:
            skipped += 1
    return redirect(url_for("inbox_page", added=added, skipped=skipped))


@app.route("/inbox")
def inbox_page():
    rows = storage.list_by_status("inbox")
    return render_template(
        "inbox.html", active_tab="inbox", rows=rows,
        added=request.args.get("added"), skipped=request.args.get("skipped"),
    )


@app.route("/inbox/approve", methods=["POST"])
def inbox_approve():
    """Summarize an approved candidate at medium detail (synchronously) and move it
    into the reading queue as unread. Reads the candidate row from DynamoDB and
    summarizes via the right source path (YouTube transcript vs LinkedIn post text).
    On failure the row is left in the inbox."""
    from flask import jsonify
    data        = request.get_json(force=True) or {}
    vid         = data.get("video_id", "")
    cand_detail = data.get("detail", "medium")
    detail      = "medium"

    row = storage._dynamo_table().get_item(Key={"video_id": vid, "detail": cand_detail}).get("Item")
    if not row:
        return jsonify({"ok": False, "error": "Candidate not found"})
    platform = row.get("source_platform", "youtube")

    if platform == "linkedin_post":
        import linkedin_search as lis
        post = {
            "text":     row.get("description", ""),
            "author":   row.get("author") or row.get("channel", ""),
            "headline": row.get("headline", ""),
            "date":     row.get("date", ""),
            "likes":    int(row.get("views", 0) or 0),
            "comments": 0,
        }
        result = lis.summarize_post(post, detail=detail)
        video = {
            "id":       vid, "url": row.get("url", ""), "title": row.get("title", ""),
            "channel":  row.get("author") or row.get("channel", ""),
            "author":   row.get("author") or row.get("channel", ""),
            "headline": row.get("headline", ""),
            "views":    int(row.get("views", 0) or 0), "date": row.get("date", ""),
            "duration": "", "thumbnail": "", "description": row.get("description", ""),
        }
        src_platform, ctype = "linkedin_post", "post"
    else:
        video = {
            "id":          vid, "url": row.get("url", ""), "title": row.get("title", ""),
            "channel":     row.get("channel", ""), "views": int(row.get("views", 0) or 0),
            "date":        row.get("date", ""), "duration": row.get("duration", ""),
            "thumbnail":   row.get("thumbnail", ""), "description": row.get("description", ""),
        }
        result = summarizer.fetch_and_summarize(video, detail=detail)
        src_platform, ctype = "youtube", "video"

    if result.get("error") or not result.get("summary"):
        return jsonify({"ok": False, "error": result.get("error") or "No summary produced"})
    result["search_term"] = row.get("search_term", "")
    storage.save_result(video, detail, result, source_platform=src_platform,
                        content_type=ctype, status="approved", read=False)
    # If the candidate was stored under a different detail, drop the stale row
    if cand_detail and cand_detail != detail:
        storage.delete_item(vid, cand_detail)
    return jsonify({"ok": True})


@app.route("/reading/regenerate", methods=["POST"])
def reading_regenerate():
    """Re-summarize a reading item at a new detail level and persist it, replacing
    the old-detail row so there stays one entry per video. Preserves the read flag."""
    from flask import jsonify
    data       = request.get_json(force=True) or {}
    vid        = data.get("video_id", "")
    old_detail = data.get("detail", "medium")
    new_detail = data.get("new_detail", "medium")
    if new_detail not in {"low", "medium", "high"}:
        return jsonify({"ok": False, "error": "Invalid detail level"})

    row = storage._dynamo_table().get_item(
        Key={"video_id": vid, "detail": old_detail}
    ).get("Item")
    if not row:
        return jsonify({"ok": False, "error": "Item not found"})

    platform = row.get("source_platform", "youtube")
    if platform == "linkedin_post":
        import linkedin_search as lis
        video = {
            "id":       vid, "url": row.get("url", ""), "title": row.get("title", ""),
            "channel":  row.get("author") or row.get("channel", ""),
            "author":   row.get("author") or row.get("channel", ""),
            "headline": row.get("headline", ""),
            "views":    int(row.get("views", 0) or 0), "date": row.get("date", ""),
            "duration": "", "thumbnail": "", "description": row.get("description", ""),
        }
        post = {
            "text":     row.get("transcript", "") or row.get("description", ""),
            "author":   row.get("author") or row.get("channel", ""),
            "headline": row.get("headline", ""),
            "date":     row.get("date", ""),
            "likes":    int(row.get("views", 0) or 0), "comments": 0,
        }
        result = lis.summarize_post(post, detail=new_detail)
        src_platform, ctype = "linkedin_post", "post"
    else:
        video = {
            "id":          vid,
            "url":         row.get("url", ""),
            "title":       row.get("title", ""),
            "channel":     row.get("channel", ""),
            "views":       int(row.get("views", 0) or 0),
            "date":        row.get("date", ""),
            "duration":    row.get("duration", ""),
            "thumbnail":   row.get("thumbnail", ""),
            "description": row.get("description", ""),
        }
        result = summarizer.fetch_and_summarize(video, detail=new_detail)
        src_platform, ctype = "youtube", "video"

    if result.get("error") or not result.get("summary"):
        return jsonify({"ok": False, "error": result.get("error") or "No summary produced"})
    result["search_term"] = row.get("search_term", "")
    read_flag = bool(row.get("read", False))
    storage.save_result(video, new_detail, result, source_platform=src_platform,
                        content_type=ctype, status="approved", read=read_flag)
    if new_detail != old_detail:
        storage.delete_item(vid, old_detail)
    return jsonify({
        "ok":        True,
        "detail":    new_detail,
        "summary":   result.get("summary", ""),
        "questions": result.get("questions", []),
    })


@app.route("/inbox/decline", methods=["POST"])
def inbox_decline():
    from flask import jsonify
    data = request.get_json(force=True) or {}
    storage.delete_item(data.get("video_id", ""), data.get("detail", "high"))
    return jsonify({"ok": True})


@app.route("/reading")
def reading_page():
    # Reading queue = unread approved items only. Read items stay in DynamoDB
    # (status="approved", read=true) and remain visible in DB Explorer.
    rows = [r for r in storage.list_by_status("approved") if not r.get("read")]
    return render_template("reading.html", active_tab="reading", rows=rows)


@app.route("/reading/toggle-read", methods=["POST"])
def reading_toggle_read():
    from flask import jsonify
    data = request.get_json(force=True) or {}
    storage.set_read(data.get("video_id", ""), data.get("detail", "high"), bool(data.get("read")))
    return jsonify({"ok": True})


@app.route("/inbox/counts")
def inbox_counts():
    from flask import jsonify
    unread_approved = sum(1 for r in storage.list_by_status("approved") if not r.get("read"))
    return jsonify({
        "inbox":   storage.count_by_status("inbox"),
        "reading": unread_approved,
    })


@app.route("/transcript", methods=["GET", "POST"])
def transcript():
    """Ad-hoc: one YouTube URL -> transcript + summary at the chosen detail."""
    if request.method == "GET":
        return redirect(url_for("index"))

    url = (request.form.get("video_url") or "").strip()
    valid_details = {v for v, _ in DETAIL_OPTIONS}
    detail = request.form.get("detail", DEFAULT_DETAIL)
    if detail not in valid_details:
        detail = DEFAULT_DETAIL

    if not url:
        tr = {"error": "Please paste a YouTube video URL.", "url": ""}
    else:
        tr = summarizer.summarize_url(url, detail=detail)
    tr["detail"] = detail

    # Cache the computed result so /adhoc/save can persist it without re-summarizing
    if not tr.get("error") and tr.get("summary") and tr.get("videoId"):
        _adhoc_cache[tr["videoId"]] = tr

    return render_template(
        "index.html",
        active_tab="search",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        sort_order=DEFAULT_SORT,
        detail=detail,
        search_mode="youtube",
        custom="",
        results=None,
        transcript_result=tr,
    )


@app.route("/adhoc/save", methods=["POST"])
def adhoc_save():
    """Persist an ad-hoc research result (already summarized) as an approved,
    unread Reading-queue item — reusing the summary/tags/questions + real video
    metadata. Falls back to recomputing if the in-process cache has expired."""
    vid    = (request.form.get("video_id") or "").strip()
    detail = request.form.get("detail", DEFAULT_DETAIL)
    if not vid:
        return redirect(url_for("index"))

    tr = _adhoc_cache.get(vid)
    if not tr:  # cache miss (e.g. server restarted) — recompute from the URL
        tr = summarizer.summarize_url(f"https://www.youtube.com/watch?v={vid}", detail=detail)
        tr["detail"] = detail
    if tr.get("error") or not tr.get("summary"):
        return redirect(url_for("index"))

    video = {
        "id":          vid,
        "url":         tr.get("url", f"https://www.youtube.com/watch?v={vid}"),
        "title":       tr.get("title", ""),
        "channel":     tr.get("channel", ""),
        "views":       int(tr.get("views", 0) or 0),
        "date":        tr.get("date", ""),
        "duration":    tr.get("duration", ""),
        "thumbnail":   tr.get("thumbnail", ""),
        "description": "",
    }
    result = {
        "transcript":  tr.get("transcript", ""),
        "summary":     tr.get("summary", ""),
        "tags":        tr.get("tags", []),
        "questions":   tr.get("questions", []),
        "source":      "youtube-transcript-api",
        "wordCount":   tr.get("wordCount", 0),
        "language":    tr.get("language", ""),
        "search_term": "ad-hoc research",
    }
    storage.save_result(video, tr.get("detail", detail), result,
                        source_platform="youtube", content_type="video",
                        status="approved", read=False)
    return redirect(url_for("reading_page"))


@app.route("/dynamo", methods=["GET", "POST"])
def dynamo_explorer():
    from flask import jsonify
    from boto3.dynamodb.conditions import Key as DKey

    ctx = dict(rows=None, query_type="get_item", video_id="", detail="",
               limit="20", search_term=[], tag=[], platform="",
               query_label="", message=None, message_type=None)

    if request.method == "GET":
        ctx["active_tab"] = "dynamo"
        return render_template("dynamo_explorer.html", **ctx)

    qt          = request.form.get("query_type", "get_item")
    video_id    = request.form.get("video_id", "").strip()
    detail      = request.form.get("detail", "").strip()
    search_terms = [s.strip() for s in request.form.getlist("search_term") if s.strip()]
    tags         = [t.strip() for t in request.form.getlist("tag") if t.strip()]
    search_term  = search_terms  # keep ctx key name for template compatibility
    tag          = tags
    platform     = request.form.get("platform", "").strip()
    limit        = max(1, min(100, int(request.form.get("limit", "20") or "20")))
    ctx.update(query_type=qt, video_id=video_id, detail=detail,
               search_term=search_terms, tag=tags, platform=platform, limit=str(limit))

    table = storage._dynamo_table()

    # Projection for all scan queries — excludes `summary` (largest field, lazy-loaded on expand)
    # `url` is a DynamoDB reserved word so it must be aliased via ExpressionAttributeNames
    _SCAN_PROJ = (
        "video_id, detail, title, channel, author, search_term, "
        "tags, searched_on, source_type, source_platform, "
        "usage_count, word_count, questions, #url"
    )
    _SCAN_NAMES = {"#url": "url"}

    try:
        if qt == "get_item":
            if not video_id or not detail:
                ctx.update(message="Video ID and Detail are required.", message_type="err", rows=[])
            else:
                item = table.get_item(Key={"video_id": video_id, "detail": detail}).get("Item")
                ctx.update(rows=[item] if item else [], query_label=f"get_item · {video_id} / {detail}")

        elif qt == "all_details":
            if not video_id:
                ctx.update(message="Video ID is required.", message_type="err", rows=[])
            else:
                resp = table.query(KeyConditionExpression=DKey("video_id").eq(video_id))
                ctx.update(rows=resp.get("Items", []), query_label=f"all details · {video_id}")

        elif qt == "scan_recent":
            scan_kwargs = {"ProjectionExpression": _SCAN_PROJ, "ExpressionAttributeNames": _SCAN_NAMES}
            all_rows = []
            while True:
                resp = table.scan(**scan_kwargs)
                all_rows.extend(resp.get("Items", []))
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                scan_kwargs["ExclusiveStartKey"] = lek
            all_rows.sort(key=lambda x: x.get("searched_on", ""), reverse=True)
            ctx.update(rows=all_rows[:limit], query_label=f"scan · last {limit} items")

        elif qt == "delete_item":
            if not video_id or not detail:
                ctx.update(message="Video ID and Detail are required for delete.", message_type="err", rows=[])
            else:
                table.delete_item(Key={"video_id": video_id, "detail": detail})
                ctx.update(rows=[], message=f"Deleted {video_id} / {detail}", message_type="ok",
                           query_label=f"delete · {video_id} / {detail}")

        elif qt == "by_topic":
            from boto3.dynamodb.conditions import Attr
            if not search_terms:
                ctx.update(message="At least one Topic is required.", message_type="err", rows=[])
            else:
                fe = None
                for st in search_terms:
                    c = Attr("search_term").eq(st)
                    fe = c if fe is None else fe | c
                if tags:
                    tag_fe = None
                    for tg in tags:
                        c = Attr("tags").contains(tg)
                        tag_fe = c if tag_fe is None else tag_fe | c
                    fe = fe & tag_fe
                scan_kwargs = {"FilterExpression": fe, "ProjectionExpression": _SCAN_PROJ, "ExpressionAttributeNames": _SCAN_NAMES}
                all_rows = []
                while True:
                    resp = table.scan(**scan_kwargs)
                    all_rows.extend(resp.get("Items", []))
                    lek = resp.get("LastEvaluatedKey")
                    if not lek:
                        break
                    scan_kwargs["ExclusiveStartKey"] = lek
                all_rows.sort(key=lambda x: x.get("searched_on", ""), reverse=True)
                label = "topic · " + ", ".join(search_terms) + ((" + tag · " + ", ".join(tags)) if tags else "")
                ctx.update(rows=all_rows[:limit], query_label=label)

        elif qt == "by_tag":
            from boto3.dynamodb.conditions import Attr
            if not tags:
                ctx.update(message="At least one Tag is required.", message_type="err", rows=[])
            else:
                fe = None
                for tg in tags:
                    c = Attr("tags").contains(tg)
                    fe = c if fe is None else fe | c
                scan_kwargs = {"FilterExpression": fe, "ProjectionExpression": _SCAN_PROJ, "ExpressionAttributeNames": _SCAN_NAMES}
                all_rows = []
                while True:
                    resp = table.scan(**scan_kwargs)
                    all_rows.extend(resp.get("Items", []))
                    lek = resp.get("LastEvaluatedKey")
                    if not lek:
                        break
                    scan_kwargs["ExclusiveStartKey"] = lek
                all_rows.sort(key=lambda x: x.get("searched_on", ""), reverse=True)
                ctx.update(rows=all_rows[:limit], query_label="tag · " + ", ".join(tags))

        elif qt == "by_platform":
            from boto3.dynamodb.conditions import Attr
            platform = request.form.get("platform", "").strip()
            if not platform:
                ctx.update(message="Platform is required.", message_type="err", rows=[])
            else:
                scan_kwargs = {"FilterExpression": Attr("source_platform").eq(platform), "ProjectionExpression": _SCAN_PROJ, "ExpressionAttributeNames": _SCAN_NAMES}
                all_rows = []
                while True:
                    resp = table.scan(**scan_kwargs)
                    all_rows.extend(resp.get("Items", []))
                    lek = resp.get("LastEvaluatedKey")
                    if not lek:
                        break
                    scan_kwargs["ExclusiveStartKey"] = lek
                all_rows.sort(key=lambda x: x.get("searched_on", ""), reverse=True)
                ctx.update(rows=all_rows[:limit],
                           query_label=f"platform · {platform}")

    except Exception as e:
        ctx.update(rows=[], message=f"DynamoDB error: {e}", message_type="err")

    ctx["active_tab"] = "dynamo"
    return render_template("dynamo_explorer.html", **ctx)


@app.route("/dynamo/filters")
def dynamo_filters():
    """Return distinct search_terms and tags for dropdown population."""
    from flask import jsonify
    table = storage._dynamo_table()
    try:
        from collections import Counter
        topic_tags       = {}   # {search_term: Counter of tags}
        source_platforms = set()
        last = None
        while True:
            kwargs = {"ProjectionExpression": "search_term, tags, source_platform"}
            if last:
                kwargs["ExclusiveStartKey"] = last
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                t = (item.get("search_term") or "").strip()
                if t:
                    if t not in topic_tags:
                        topic_tags[t] = Counter()
                    topic_tags[t].update(item.get("tags", []))
                sp = (item.get("source_platform") or "").strip()
                if sp:
                    source_platforms.add(sp)
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
        all_tags = sorted(
            set(tag for c in topic_tags.values() for tag in c),
            key=str.lower
        )
        return jsonify({
            "search_terms":     sorted(topic_tags.keys(), key=str.lower),
            "tags":             all_tags,
            "source_platforms": sorted(source_platforms, key=str.lower),
            "topic_tags": {
                t: [tag for tag, _ in sorted(c.items(), key=lambda x: (-x[1], x[0].lower()))]
                for t, c in topic_tags.items()
            },
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dynamo/item/<video_id>/<detail>")
def dynamo_item_detail(video_id, detail):
    """Return summary + questions for a single item — used by lazy expand in DB Explorer."""
    from flask import jsonify
    item = storage._dynamo_table().get_item(
        Key={"video_id": video_id, "detail": detail}
    ).get("Item")
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "summary":   item.get("summary", ""),
        "questions": list(item.get("questions", [])),
    })


@app.route("/dynamo/export-linkedin", methods=["POST"])
def dynamo_export_linkedin():
    from flask import jsonify
    import anthropic as _asdk, httpx as _httpx

    data  = request.get_json(force=True) or {}
    items = data.get("items", [])
    style = data.get("style", "insights")

    if not items:
        return jsonify({"error": "No items selected"}), 400

    # Fetch full summary from DynamoDB for each item (summary excluded from scan projection)
    tbl = storage._dynamo_table()
    enriched = []
    for it in items:
        vid, det = it.get("video_id", ""), it.get("detail", "high")
        if vid:
            full = tbl.get_item(Key={"video_id": vid, "detail": det}).get("Item", {})
            it = dict(it, summary=full.get("summary", ""), tags=list(full.get("tags", [])),
                      url=full.get("url", it.get("url", "")))
        enriched.append(it)
    items = enriched

    digests = []
    for i, it in enumerate(items, 1):
        title   = it.get("title", "Untitled")
        author  = it.get("author") or it.get("channel", "")
        summary = (it.get("summary") or "")[:400]
        tags    = ", ".join(it.get("tags", []))
        url     = it.get("url", "")
        url_line = f"\nURL: {url}" if url else ""
        digests.append(f"[{i}] {title}\nAuthor: {author}\nTags: {tags}{url_line}\nSummary: {summary}")
    digest_block = "\n\n".join(digests)

    style_instructions = {
        "insights": "Write a 'N things I learned about [topic] this week' post. Use numbered insights.",
        "tips":     "Write a practical tips post. Each tip is actionable and specific.",
        "thread":   "Write a LinkedIn thread-style post using 1/ 2/ 3/ numbering.",
    }.get(style, "Write a LinkedIn post sharing key insights from these articles.")

    prompt = (
        f"You are a LinkedIn thought-leader writing a post based on {len(items)} articles/videos.\n\n"
        f"Style: {style_instructions}\n\n"
        f"Rules:\n"
        f"- Post body: max 3000 characters, professional but conversational tone\n"
        f"- End with a thought-provoking question to drive engagement\n"
        f"- After the post, output exactly this delimiter on its own line: ---HASHTAGS---\n"
        f"- Then output 5-8 relevant hashtags (no # prefix, one per line)\n\n"
        f"Articles:\n{digest_block}"
    )

    _client = _asdk.Anthropic(
        api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_FOUNDRY_ENDPOINT",
                                "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1"),
        http_client=_httpx.Client(verify=False),
    )
    _model = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")

    try:
        resp = _client.messages.create(
            model=_model,
            system="You write concise, high-signal LinkedIn posts. Follow the format exactly.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
        raw = resp.content[0].text.strip()
        if "---HASHTAGS---" in raw:
            post_part, tag_part = raw.split("---HASHTAGS---", 1)
            post_text = post_part.strip()
            hashtags  = [f"#{t.strip().lstrip('#')}" for t in tag_part.strip().splitlines() if t.strip()]
        else:
            post_text = raw
            hashtags  = []

        # Always append sources section with URLs for every item that has one
        sources = []
        for i, it in enumerate(items, 1):
            url   = it.get("url", "").strip()
            title = it.get("title", f"Article {i}")
            if url:
                sources.append(f"[{i}] {title}\n{url}")
        if sources:
            post_text = post_text + "\n\n\U0001f517 Sources:\n" + "\n\n".join(sources)

        return jsonify({"post_text": post_text, "hashtags": hashtags, "char_count": len(post_text)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/terms", methods=["GET", "POST"])
def terms_manager():
    message = None
    message_type = None

    if request.method == "POST":
        action = request.form.get("action")
        groups  = search_terms.get_groups()
        context = search_terms.get_context()

        if action == "add_term":
            group = request.form.get("group", "").strip()
            term  = request.form.get("term", "").strip()
            if group and term:
                if group not in groups:
                    groups[group] = []
                    context.setdefault(group, "")
                if term not in groups[group]:
                    groups[group].append(term)
                    search_terms.save_terms(groups, context)
                    message, message_type = f'Added "{term}" to {group}.', "ok"
                else:
                    message, message_type = f'"{term}" already exists in {group}.', "err"
            else:
                message, message_type = "Group and term are required.", "err"

        elif action == "add_group":
            group   = request.form.get("new_group", "").strip()
            ctx_val = request.form.get("new_context", "").strip()
            if group:
                if group not in groups:
                    groups[group] = []
                    context[group] = ctx_val
                    search_terms.save_terms(groups, context)
                    message, message_type = f'Group "{group}" created.', "ok"
                else:
                    message, message_type = f'Group "{group}" already exists.', "err"
            else:
                message, message_type = "Group name is required.", "err"

        elif action == "delete_term":
            group = request.form.get("group", "").strip()
            term  = request.form.get("term", "").strip()
            if group in groups and term in groups[group]:
                groups[group].remove(term)
                if not groups[group]:
                    del groups[group]
                    context.pop(group, None)
                search_terms.save_terms(groups, context)
                message, message_type = f'Deleted "{term}".', "ok"

        elif action == "update_context":
            group   = request.form.get("group", "").strip()
            ctx_val = request.form.get("context_value", "").strip()
            if group in groups:
                context[group] = ctx_val
                search_terms.save_terms(groups, context)
                message, message_type = f'Context for "{group}" updated.', "ok"

        elif action == "delete_group":
            group = request.form.get("group", "").strip()
            if group in groups:
                del groups[group]
                context.pop(group, None)
                search_terms.save_terms(groups, context)
                message, message_type = f'Group "{group}" deleted.', "ok"

    return render_template(
        "terms_manager.html",
        active_tab="terms",
        groups=search_terms.get_groups(),
        context=search_terms.get_context(),
        message=message,
        message_type=message_type,
    )


def _save_digest(today, terms, results, window_label="Any time"):
    """Write a markdown digest to outputs/ for the record."""
    lines = [f"# YouTube Summary Digest — {today}", ""]
    lines.append(f"**Terms:** {len(terms)} · **Uploaded:** {window_label}")
    lines.append("")
    for term, data in results.items():
        lines.append(f"## {term}")
        if not data["top"]:
            lines.append("_No results._\n")
            continue
        for v in data["top"]:
            lines.append(f"### [{v['title']}]({v['url']})")
            lines.append(
                f"{v['channel']} · {v['views']:,} views · {v['date']} · {v['duration']}"
            )
            lines.append("")
            lines.append("_(summary available on demand — click Get Summary in the UI)_")
            lines.append("")
    path = os.path.join(OUTPUTS, f"digest_{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


@app.route("/linkedin", methods=["GET", "POST"])
def linkedin_page():
    import linkedin_generator as lg
    from flask import send_file
    import io

    ctx = dict(
        active_tab="linkedin",
        video_data=None,
        post_text=None,
        error=None,
        video_id="",
        detail="high",
        post_style="tips",
        customization="",
        temperature=0.7,
    )

    if request.method == "POST":
        video_id      = request.form.get("video_id", "").strip()
        detail        = request.form.get("detail", "high").strip()
        post_style    = request.form.get("post_style", "tips").strip()
        customization = request.form.get("customization", "").strip()
        try:
            temperature = round(float(request.form.get("temperature", "0.7")), 2)
            temperature = max(0.0, min(1.0, temperature))
        except ValueError:
            temperature = 0.7
        ctx.update(video_id=video_id, detail=detail, post_style=post_style,
                   customization=customization, temperature=temperature)

        if not video_id:
            ctx["error"] = "Please select or enter a Video ID."
            return render_template("linkedin.html", **ctx)

        video_data = lg.get_video(video_id, detail)
        if not video_data:
            ctx["error"] = f"No cached entry found for video_id={video_id!r}, detail={detail!r}. Run a summary first."
            return render_template("linkedin.html", **ctx)

        ctx["video_data"] = video_data

        post_text  = lg.generate_post_text(video_data, post_style=post_style,
                                            customization=customization, temperature=temperature)
        slides     = lg.generate_slide_data(video_data, post_style=post_style)
        pdf_bytes  = lg.build_pdf(slides, video_data)
        paths      = lg.save_outputs(video_id, detail, post_text, pdf_bytes)

        ctx["post_text"] = post_text
        ctx["pdf_path"]  = paths["pdf"]
        ctx["txt_path"]  = paths["txt"]

        # If user clicked the PDF download button, serve the file directly
        if request.form.get("action") == "download_pdf":
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"{video_id}_{detail}_carousel.pdf",
            )

    return render_template("linkedin.html", **ctx)


@app.route("/linkedin/videos")
def linkedin_videos():
    """Return recent DynamoDB items as JSON for the LinkedIn dropdown."""
    from flask import jsonify
    import linkedin_generator as lg
    try:
        items = lg.list_recent(50)
        return jsonify([{
            "video_id": i.get("video_id", ""),
            "detail":   i.get("detail", ""),
            "title":    i.get("title", ""),
            "channel":  i.get("channel", ""),
        } for i in items])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/linkedin/download")
def linkedin_download():
    """Serve the most recently generated LinkedIn PDF or TXT."""
    from flask import send_file, abort
    import glob as _glob

    file_type = request.args.get("type", "pdf")
    video_id  = request.args.get("video_id", "")
    detail    = request.args.get("detail", "")

    base = os.path.join(OUTPUTS, "linkedin")
    ext  = "pdf" if file_type == "pdf" else "txt"
    suffix = "_carousel.pdf" if ext == "pdf" else "_post.txt"
    pattern = os.path.join(base, "*", f"{video_id}_{detail}{suffix}")
    matches = sorted(_glob.glob(pattern), reverse=True)
    if not matches:
        abort(404)
    return send_file(
        matches[0],
        mimetype="application/pdf" if ext == "pdf" else "text/plain",
        as_attachment=True,
        download_name=os.path.basename(matches[0]),
    )


def _foundry_client():
    """Anthropic client against the Azure AI Foundry endpoint — same config the
    DB Explorer LinkedIn export uses."""
    import anthropic as _asdk, httpx as _httpx
    return _asdk.Anthropic(
        api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_FOUNDRY_ENDPOINT",
                                "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1"),
        http_client=_httpx.Client(verify=False),
    )


def _append_sources(body, src_idx, items):
    """Append the 🔗 Sources block for the given 1-based article indices."""
    sources = []
    for idx in src_idx:
        if 1 <= idx <= len(items) and items[idx - 1]["url"]:
            sources.append(f"[{idx}] {items[idx - 1]['title']}\n{items[idx - 1]['url']}")
    if sources:
        return body + "\n\n\U0001f517 Sources:\n" + "\n\n".join(sources)
    return body


def _verify_feed_sources(posts, items, client, model):
    """Verification agent — guarantees every post has reference links.

    Posts whose parsed source indices are empty/invalid are sent (with the
    numbered article list) to a second LLM pass that maps each such post to the
    articles it draws from; the returned indices fill in `src_idx`. Mutates
    `posts` in place and never raises (best-effort repair)."""
    import json as _json

    def _valid(p):
        return any(1 <= i <= len(items) and items[i - 1]["url"] for i in p["src_idx"])

    missing = [i for i, p in enumerate(posts) if not _valid(p)]
    if not missing:
        return

    article_list = "\n".join(f"[{j}] {it['title']}" for j, it in enumerate(items, 1))
    blocks = "\n\n".join(f"POST {i + 1}:\n{posts[i]['body'][:1200]}" for i in missing)
    prompt = (
        f"Each LinkedIn post below was written from this numbered list of articles.\n\n"
        f"Articles:\n{article_list}\n\n"
        f"Posts:\n{blocks}\n\n"
        f"For each post shown, identify which article numbers it draws from (at least one each). "
        f'Return ONLY a JSON object mapping the post label to a list of article numbers, '
        f'e.g. {{"POST 2": [3, 5], "POST 4": [1]}}. No prose.'
    )
    try:
        resp = client.messages.create(
            model=model,
            system="You map LinkedIn posts to their source articles. Return JSON only.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )
        txt = resp.content[0].text.strip()
        m = re.search(r"\{.*\}", txt, re.DOTALL)
        mapping = _json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"[linkedin_feed] source-repair agent failed: {e}")
        return

    for i in missing:
        nums = mapping.get(f"POST {i + 1}") or mapping.get(str(i + 1)) or []
        idxs = [int(n) for n in nums
                if isinstance(n, int) or (isinstance(n, str) and n.strip().isdigit())]
        idxs = [n for n in idxs if 1 <= n <= len(items)]
        if idxs:
            posts[i]["src_idx"] = idxs


@app.route("/linkedin/feed", methods=["POST"])
def linkedin_feed():
    """Weekly LinkedIn feed: pull the N most-recent summarized DB entries and have
    the LLM group them into n_posts LinkedIn posts (same style/format as the DB
    Explorer export). Returns a list of posts for copy-paste."""
    from flask import jsonify

    data = request.get_json(force=True) or {}
    try:
        n_db = max(1, min(100, int(data.get("n_db", 30))))
    except (ValueError, TypeError):
        n_db = 30
    try:
        n_posts = max(1, min(15, int(data.get("n_posts", 6))))
    except (ValueError, TypeError):
        n_posts = 6

    entries = storage.list_recent(n_db)
    if not entries:
        return jsonify({"error": "No summarized entries found in the database yet."}), 400
    n_posts = min(n_posts, len(entries))

    # Enrich each entry with its full summary/tags/url (summary excluded from scan)
    tbl = storage._dynamo_table()
    items = []
    for e in entries:
        vid, det = e.get("video_id", ""), e.get("detail", "high")
        full = tbl.get_item(Key={"video_id": vid, "detail": det}).get("Item", {}) if vid else {}
        items.append({
            "title":   e.get("title", "Untitled"),
            "author":  e.get("author") or e.get("channel", ""),
            "tags":    list(full.get("tags", []) or e.get("tags", [])),
            "url":     full.get("url", e.get("url", "")),
            "summary": full.get("summary", ""),
        })

    # Same indexed digest shape as the DB Explorer export
    digests = []
    for i, it in enumerate(items, 1):
        url_line = f"\nURL: {it['url']}" if it["url"] else ""
        digests.append(
            f"[{i}] {it['title']}\nAuthor: {it['author']}\n"
            f"Tags: {', '.join(it['tags'])}{url_line}\nSummary: {(it['summary'] or '')[:400]}"
        )
    digest_block = "\n\n".join(digests)

    prompt = (
        f"You are a LinkedIn thought-leader writing posts based on {len(items)} articles/videos.\n\n"
        f"Group the articles into exactly {n_posts} LinkedIn posts by theme. Assign EVERY article "
        f"(numbers 1-{len(items)}) to exactly one post — every article must appear in some post's "
        f"sources, and no post may be left without at least one source article.\n\n"
        f"Style for every post: Write a 'N things I learned about [topic] this week' post. Use numbered insights.\n\n"
        f"Rules for each post:\n"
        f"- Post body: max 3000 characters, professional but conversational tone\n"
        f"- End with a thought-provoking question to drive engagement\n"
        f"- Provide 5-8 relevant hashtags (no # prefix)\n"
        f"- MANDATORY: list the article numbers this post draws from. Never omit the ---SOURCES--- line.\n\n"
        f"Output EXACTLY this structure for each post and nothing else:\n"
        f"===POST===\n"
        f"<post body>\n"
        f"---HASHTAGS---\n"
        f"<one hashtag per line>\n"
        f"---SOURCES---\n"
        f"<comma-separated article numbers used in this post, e.g. 1,4,7 — at least one, required>\n\n"
        f"Articles:\n{digest_block}"
    )

    try:
        client = _foundry_client()
        model  = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")
        resp = client.messages.create(
            model=model,
            system="You write concise, high-signal LinkedIn posts. Follow the format exactly.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=min(8192, 1000 + n_posts * 900),
        )
        raw = resp.content[0].text.strip()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    posts = []
    for block in (b.strip() for b in raw.split("===POST===") if b.strip()):
        body, hashtags, src_part = block, [], ""
        if "---HASHTAGS---" in body:
            body, rest = body.split("---HASHTAGS---", 1)
            tag_part, src_part = (rest.split("---SOURCES---", 1) + [""])[:2] \
                if "---SOURCES---" in rest else (rest, "")
            hashtags = [f"#{t.strip().lstrip('#')}" for t in tag_part.strip().splitlines() if t.strip()]
        elif "---SOURCES---" in body:
            body, src_part = body.split("---SOURCES---", 1)
        # Tolerant index extraction: handles "1,4,7", "[1] [4]", "1 and 4", etc.
        src_idx = [int(n) for n in re.findall(r"\d+", src_part)]
        posts.append({"body": body.strip(), "hashtags": hashtags, "src_idx": src_idx})

    if not posts:
        return jsonify({"error": "The model returned no parseable posts. Try again."}), 500

    # Verification/repair agent: guarantee every post has reference links.
    _verify_feed_sources(posts, items, client, model)

    out = []
    for p in posts:
        post_text = _append_sources(p["body"], p["src_idx"], items)
        out.append({"post_text": post_text, "hashtags": p["hashtags"], "char_count": len(post_text)})
    return jsonify({"posts": out, "count": len(out), "fetched": len(items)})


@app.route("/mixer/videos")
def mixer_videos():
    """Return cached DynamoDB items for Mixer dropdowns — includes search_term and tags."""
    from flask import jsonify
    import mixer_generator as mg
    try:
        items = mg.list_recent(100)
        return jsonify([{
            "video_id":    i.get("video_id", ""),
            "detail":      i.get("detail", ""),
            "title":       i.get("title", ""),
            "channel":     i.get("channel", ""),
            "search_term": i.get("search_term", ""),
            "tags":        list(i.get("tags", [])),
        } for i in items])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mixer", methods=["GET"])
def mixer_page():
    return render_template("mixer.html", active_tab="mixer",
                           post_text=None, error=None,
                           sc_video_id="", sc_detail="high",
                           tech_video_id="", tech_detail="high",
                           score=None, headline=None, reasoning=None,
                           customization="")


@app.route("/mixer/score", methods=["POST"])
def mixer_score():
    from flask import jsonify
    import mixer_generator as mg
    data = request.get_json(force=True) or {}
    sc_video_id   = data.get("sc_video_id", "").strip()
    sc_detail     = data.get("sc_detail", "high").strip()
    tech_video_id = data.get("tech_video_id", "").strip()
    tech_detail   = data.get("tech_detail", "high").strip()

    if not sc_video_id or not tech_video_id:
        return jsonify({"error": "Both videos must be selected."}), 400

    sc_data   = mg.get_video(sc_video_id, sc_detail)
    tech_data = mg.get_video(tech_video_id, tech_detail)
    if not sc_data:
        return jsonify({"error": f"Supply chain video not found: {sc_video_id} / {sc_detail}"}), 404
    if not tech_data:
        return jsonify({"error": f"Technology video not found: {tech_video_id} / {tech_detail}"}), 404

    result = mg.score_fit(sc_data, tech_data)
    result["sc_title"]   = sc_data.get("title", "")
    result["tech_title"] = tech_data.get("title", "")
    return jsonify(result)


@app.route("/mixer/generate", methods=["POST"])
def mixer_generate():
    import mixer_generator as mg
    import linkedin_generator as lg

    sc_video_id   = request.form.get("sc_video_id", "").strip()
    sc_detail     = request.form.get("sc_detail", "high").strip()
    tech_video_id = request.form.get("tech_video_id", "").strip()
    tech_detail   = request.form.get("tech_detail", "high").strip()
    customization = request.form.get("customization", "").strip()
    try:
        score = int(request.form.get("score", "0"))
    except ValueError:
        score = 0
    reasoning = request.form.get("reasoning", "")
    headline  = request.form.get("headline", "")

    ctx = dict(active_tab="mixer", post_text=None, error=None,
               sc_video_id=sc_video_id, sc_detail=sc_detail,
               tech_video_id=tech_video_id, tech_detail=tech_detail,
               score=score, headline=headline, reasoning=reasoning,
               customization=customization)

    sc_data   = mg.get_video(sc_video_id, sc_detail)
    tech_data = mg.get_video(tech_video_id, tech_detail)
    if not sc_data or not tech_data:
        ctx["error"] = "Could not reload video data. Please re-score first."
        return render_template("mixer.html", **ctx)

    post_text = mg.generate_post(sc_data, tech_data, score, reasoning, customization)
    slides    = mg.generate_slides(sc_data, tech_data, score, reasoning)

    combined_data = {
        "channel":  f"{sc_data.get('channel','')} × {tech_data.get('channel','')}",
        "title":    f"{sc_data.get('title','')} × {tech_data.get('title','')}",
        "url":      sc_data.get("url", ""),
        "video_id": f"mixer_{sc_video_id}_{tech_video_id}",
        "tags":     list(set(list(sc_data.get("tags", [])) + list(tech_data.get("tags", [])))),
    }
    pdf_bytes = lg.build_pdf(slides, combined_data)
    paths     = lg.save_outputs(
        f"mixer_{sc_video_id}", f"{tech_video_id}_{sc_detail}_{tech_detail}",
        post_text, pdf_bytes,
    )

    ctx.update(post_text=post_text,
               pdf_stem=f"mixer_{sc_video_id}_{tech_video_id}_{sc_detail}_{tech_detail}",
               pdf_path=paths["pdf"], txt_path=paths["txt"])
    return render_template("mixer.html", **ctx)


@app.route("/mixer/download")
def mixer_download():
    from flask import send_file, abort
    import glob as _glob

    file_type = request.args.get("type", "pdf")
    stem      = request.args.get("stem", "")
    base      = os.path.join(OUTPUTS, "linkedin")
    suffix    = "_carousel.pdf" if file_type == "pdf" else "_post.txt"
    pattern   = os.path.join(base, "*", f"{stem}{suffix}")
    matches   = sorted(_glob.glob(pattern), reverse=True)
    if not matches:
        abort(404)
    mime = "application/pdf" if file_type == "pdf" else "text/plain"
    return send_file(matches[0], mimetype=mime, as_attachment=True,
                     download_name=os.path.basename(matches[0]))


@app.route("/google", methods=["GET"])
def google_search_page():
    return render_template(
        "google_search.html",
        active_tab="google",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        detail=DEFAULT_DETAIL,
        results=None,
        errors={},
        error=None,
        custom="",
        selected=[],
    )


@app.route("/google/run", methods=["POST"])
def google_run():
    import google_search as gs

    selected = request.form.getlist("terms")
    custom   = (request.form.get("custom") or "").strip()
    if custom:
        selected = selected + [t.strip() for t in custom.split(",") if t.strip()]
    if not selected:
        selected = search_terms.all_terms()

    try:
        max_results = max(1, min(15, int(request.form.get("max_results", DEFAULT_MAX))))
    except ValueError:
        max_results = DEFAULT_MAX

    valid_dates = {v for v, _ in DATE_WINDOWS}
    date_filter = request.form.get("date_filter", DEFAULT_DATE_FILTER)
    if date_filter not in valid_dates:
        date_filter = DEFAULT_DATE_FILTER

    valid_details = {v for v, _ in DETAIL_OPTIONS}
    detail = request.form.get("detail", DEFAULT_DETAIL)
    if detail not in valid_details:
        detail = DEFAULT_DETAIL

    term_pairs = [(label, search_terms.build_query(label)) for label in selected]

    error = None
    results, errors = gs.run_topics(term_pairs, max_results=max_results,
                                    date_filter=date_filter)

    return render_template(
        "google_search.html",
        active_tab="google",
        groups=search_terms.get_groups(),
        default_max=max_results,
        date_windows=DATE_WINDOWS,
        detail_options=DETAIL_OPTIONS,
        date_filter=date_filter,
        detail=detail,
        results=results,
        errors=errors,
        error=error,
        custom=custom,
        selected=selected,
    )


@app.route("/google/summary", methods=["POST"])
def google_summary():
    from flask import jsonify
    import google_search as gs

    data   = request.get_json(force=True) or {}
    url    = data.get("url", "").strip()
    detail = data.get("detail", DEFAULT_DETAIL).strip()

    if not url:
        return jsonify({"error": "url is required"}), 400

    article = {
        "id":          gs._content_id(url),
        "url":         url,
        "title":       data.get("title", ""),
        "domain":      data.get("domain", ""),
        "date":        data.get("date", ""),
        "description": data.get("description", ""),
        "search_term": data.get("search_term", ""),
    }

    cached = storage.check_cache(article["id"], detail)
    if cached:
        return jsonify(cached)

    result = gs.fetch_and_summarize(article, detail=detail)

    if not result.get("error"):
        video_meta = {
            "id":       article["id"],
            "title":    article["title"],
            "channel":  article["domain"],
            "domain":   article["domain"],
            "author":   article.get("author", ""),
            "views":    0,
            "date":     article["date"],
            "duration": "",
            "url":      url,
        }
        threading.Thread(
            target=storage.save_result,
            args=(video_meta, detail, {**result, "search_term": article["search_term"]}),
            kwargs={"source_platform": "google_search", "content_type": "article"},
            daemon=True,
        ).start()

    return jsonify(result)


@app.route("/google/article", methods=["GET"])
def google_article_page():
    article = {
        "url":         request.args.get("url", ""),
        "title":       request.args.get("title", ""),
        "domain":      request.args.get("domain", ""),
        "date":        request.args.get("date", ""),
        "description": request.args.get("description", ""),
        "search_term": request.args.get("search_term", ""),
    }
    return render_template(
        "google_article.html",
        article=article,
        detail_options=DETAIL_OPTIONS,
        default_detail=DEFAULT_DETAIL,
    )


@app.route("/li-search", methods=["GET"])
def li_search_page():
    return render_template(
        "index.html",
        active_tab="search",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        sort_order=DEFAULT_SORT,
        detail=DEFAULT_DETAIL,
        search_mode="linkedin",
        custom="",
        results=None,
        errors={},
        selected=[],
        transcript_result=None,
        today="", total_videos=0, window_label="",
    )


@app.route("/li-search/run", methods=["POST"])
def li_search_run():
    import linkedin_search as ls

    selected = request.form.getlist("terms")
    custom   = (request.form.get("custom") or "").strip()
    if custom:
        selected = selected + [t.strip() for t in custom.split(",") if t.strip()]
    if not selected:
        selected = search_terms.all_terms()

    try:
        max_results = max(1, min(50, int(request.form.get("max_results", DEFAULT_MAX))))
    except ValueError:
        max_results = DEFAULT_MAX

    valid_dates = {v for v, _ in DATE_WINDOWS}
    date_filter = request.form.get("date_filter", DEFAULT_DATE_FILTER)
    if date_filter not in valid_dates:
        date_filter = DEFAULT_DATE_FILTER

    valid_details = {v for v, _ in DETAIL_OPTIONS}
    detail = request.form.get("detail", DEFAULT_DETAIL)
    if detail not in valid_details:
        detail = DEFAULT_DETAIL

    term_pairs = [(label, search_terms.build_query(label)) for label in selected]

    results, errors = ls.run_topics(term_pairs, max_results=max_results,
                                    date_filter=date_filter)

    # Cache posts so the post detail page can retrieve full text by id
    for term_data in results.values():
        for p in term_data.get("results", []):
            _li_post_cache[p["id"]] = p

    return render_template(
        "index.html",
        active_tab="search",
        groups=search_terms.get_groups(),
        default_max=max_results,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=date_filter,
        sort_order=DEFAULT_SORT,
        detail=detail,
        search_mode="linkedin",
        custom=custom,
        results=results,
        errors=errors,
        selected=selected,
        transcript_result=None,
        today="", total_videos=0, window_label="",
    )


@app.route("/li-search/summary", methods=["POST"])
def li_search_summary():
    from flask import jsonify
    import linkedin_search as ls

    data   = request.get_json(force=True) or {}
    url    = data.get("url", "").strip()
    detail = data.get("detail", DEFAULT_DETAIL).strip()

    if not url:
        return jsonify({"error": "url is required"}), 400

    post_id  = ls._post_id(url)
    cached_p = _li_post_cache.get(post_id) or {}
    post = {
        "id":          post_id,
        "url":         url,
        "text":        data.get("text", "") or cached_p.get("text", ""),
        "author":      data.get("author", ""),
        "headline":    data.get("headline", ""),
        "date":        data.get("date", ""),
        "likes":       data.get("likes", 0),
        "comments":    data.get("comments", 0),
        "search_term": data.get("search_term", ""),
    }

    cached = storage.check_cache(post["id"], detail)
    if cached:
        return jsonify(cached)

    result = ls.summarize_post(post, detail=detail)

    if not result.get("error"):
        video_meta = {
            "id":       post["id"],
            "title":    f"LinkedIn: {post['author']}",
            "channel":  post["author"],
            "author":   post["author"],
            "headline": post.get("headline", ""),
            "views":    post["likes"],
            "date":     post["date"],
            "duration": "",
            "url":      url,
        }
        threading.Thread(
            target=storage.save_result,
            args=(video_meta, detail, {**result, "search_term": post["search_term"]}),
            kwargs={"source_platform": "linkedin_post", "content_type": "post"},
            daemon=True,
        ).start()

    return jsonify(result)


@app.route("/li-search/post", methods=["GET"])
def li_search_post_page():
    import linkedin_search as ls
    post_id = request.args.get("id", "")
    post    = _li_post_cache.get(post_id) or {
        "id":          post_id,
        "url":         request.args.get("url", ""),
        "author":      request.args.get("author", ""),
        "headline":    request.args.get("headline", ""),
        "profileUrl":  request.args.get("profileUrl", ""),
        "date":        request.args.get("date", ""),
        "likes":       request.args.get("likes", 0),
        "comments":    request.args.get("comments", 0),
        "text":        "",
        "search_term": request.args.get("search_term", ""),
    }
    return render_template(
        "linkedin_post_page.html",
        post=post,
        detail_options=DETAIL_OPTIONS,
        default_detail=DEFAULT_DETAIL,
    )


# ─── Graph Explorer ──────────────────────────────────────────────────────────

@app.route("/graph")
def graph_page():
    import graph_data as gd
    meta = gd.get_meta()
    return render_template("dynamo_graph_v2.html", active_tab="graph", meta=meta)


@app.route("/graph/data")
def graph_data_api():
    from flask import jsonify
    import graph_data as gd
    platform    = request.args.get("platform") or None
    search_term = request.args.get("search_term") or None
    tag         = request.args.get("tag") or None
    graph = gd.build_graph(platform=platform, search_term=search_term, tag=tag)
    return jsonify(graph)


@app.route("/graph/meta")
def graph_meta_api():
    from flask import jsonify
    import graph_data as gd
    return jsonify(gd.get_meta())


@app.route("/graph/node/<video_id>")
def graph_node_api(video_id):
    from flask import jsonify
    import graph_data as gd
    items = gd.scan_all_items()
    detail_rank = {"high": 3, "medium": 2, "low": 1}
    best = None
    for item in items:
        if item.get("video_id") == video_id:
            if best is None or detail_rank.get(item.get("detail", ""), 0) > detail_rank.get(best.get("detail", ""), 0):
                best = item
    if not best:
        return jsonify({"error": "not found"}), 404
    best.pop("transcript", None)
    return jsonify(best)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8051"))
    app.run(host="127.0.0.1", port=port, debug=True)
