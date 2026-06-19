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
DEFAULT_DATE_FILTER = "month"
DEFAULT_SORT = "relevance"
DEFAULT_DETAIL = "high"
OUTPUTS = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUTS, exist_ok=True)


@app.template_filter("mdlite")
def mdlite(text):
    """Render the LLM's lightweight markdown (just **bold**) as HTML, escaping
    everything else. Newlines are preserved by the CSS (white-space:pre-wrap)."""
    from markupsafe import escape, Markup
    esc = str(escape(text or ""))
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    return Markup(esc)


@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        sort_order=DEFAULT_SORT,
        detail=DEFAULT_DETAIL,
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
        groups=search_terms.get_groups(),
        default_max=max_results,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=date_filter,
        sort_order=sort_order,
        detail=detail,
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
            target=storage.save_result, args=(video, detail, result), daemon=True
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

    return render_template(
        "index.html",
        groups=search_terms.get_groups(),
        default_max=DEFAULT_MAX,
        date_windows=DATE_WINDOWS,
        sort_options=SORT_OPTIONS,
        detail_options=DETAIL_OPTIONS,
        date_filter=DEFAULT_DATE_FILTER,
        sort_order=DEFAULT_SORT,
        detail=detail,
        results=None,
        transcript_result=tr,
    )


@app.route("/dynamo", methods=["GET", "POST"])
def dynamo_explorer():
    from flask import jsonify
    from boto3.dynamodb.conditions import Key as DKey

    ctx = dict(rows=None, query_type="get_item", video_id="", detail="",
               limit="20", query_label="", message=None, message_type=None)

    if request.method == "GET":
        return render_template("dynamo_explorer.html", **ctx)

    qt       = request.form.get("query_type", "get_item")
    video_id = request.form.get("video_id", "").strip()
    detail   = request.form.get("detail", "").strip()
    limit    = max(1, min(100, int(request.form.get("limit", "20") or "20")))
    ctx.update(query_type=qt, video_id=video_id, detail=detail, limit=str(limit))

    table = storage._dynamo_table()

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
            resp = table.scan(Limit=limit)
            items = sorted(resp.get("Items", []), key=lambda x: x.get("searched_on", ""), reverse=True)
            ctx.update(rows=items, query_label=f"scan · last {limit} items")

        elif qt == "delete_item":
            if not video_id or not detail:
                ctx.update(message="Video ID and Detail are required for delete.", message_type="err", rows=[])
            else:
                table.delete_item(Key={"video_id": video_id, "detail": detail})
                ctx.update(rows=[], message=f"Deleted {video_id} / {detail}", message_type="ok",
                           query_label=f"delete · {video_id} / {detail}")

    except Exception as e:
        ctx.update(rows=[], message=f"DynamoDB error: {e}", message_type="err")

    return render_template("dynamo_explorer.html", **ctx)


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8051"))
    app.run(host="127.0.0.1", port=port, debug=True)
