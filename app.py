"""Local web app: pick search terms, fetch top + trending YouTube videos,
and view AI summaries. Run with `python app.py` then open localhost:8051.

Note: port 8051 is used by default so this can run alongside the original
app on port 8050. Override with the PORT env var if needed.
"""

import os
import re
import datetime as dt

from dotenv import load_dotenv

load_dotenv()  # must run before importing summarizer (reads env at import)

from flask import Flask, render_template, request, redirect, url_for  # noqa: E402

from search_terms import SEARCH_GROUPS, all_terms, build_query  # noqa: E402
import summarizer  # noqa: E402

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
        groups=SEARCH_GROUPS,
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
        selected = all_terms()

    try:
        max_results = max(1, min(15, int(request.form.get("max_results", DEFAULT_MAX))))
    except ValueError:
        max_results = DEFAULT_MAX
    deep = request.form.get("deep") == "on"

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
    term_pairs = [(label, build_query(label)) for label in selected]

    results, videos_by_id, errors = summarizer.run_terms(
        term_pairs, max_results=max_results, deep=deep,
        date_filter=date_filter or None, sort_order=sort_order, detail=detail,
    )

    window_label = dict(DATE_WINDOWS).get(date_filter, "Any time")
    today = dt.date.today().isoformat()
    _save_digest(today, selected, results, deep, window_label)

    return render_template(
        "index.html",
        groups=SEARCH_GROUPS,
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
        deep=deep,
        today=today,
        total_videos=len(videos_by_id),
        transcript_result=None,
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
        groups=SEARCH_GROUPS,
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


def _save_digest(today, terms, results, deep, window_label="Any time"):
    """Write a markdown digest to outputs/ for the record."""
    lines = [f"# YouTube Summary Digest — {today}", ""]
    lines.append(
        f"**Terms:** {len(terms)} · **Uploaded:** {window_label} · "
        f"**Deep transcripts:** {'yes' if deep else 'no'}"
    )
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
            lines.append(v.get("summary", ""))
            lines.append("")
    path = os.path.join(OUTPUTS, f"digest_{today}.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8051"))
    app.run(host="127.0.0.1", port=port, debug=True)
