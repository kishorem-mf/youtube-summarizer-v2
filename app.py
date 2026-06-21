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
        active_tab="search",
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
        active_tab="search",
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
        active_tab="search",
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
        ctx["active_tab"] = "dynamo"
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

    ctx["active_tab"] = "dynamo"
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8051"))
    app.run(host="127.0.0.1", port=port, debug=True)
