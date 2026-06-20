# Plan: LinkedIn Post + PDF Generator Module

## Context

The app already fetches YouTube transcripts, generates AI summaries, and caches them in DynamoDB. This module repurposes that cached knowledge into professional LinkedIn content — both a text post (hook + bullets + hashtags) and a carousel-style PDF (title card → insight slides → CTA slide) — without leaving the web app. Prompts for all LLM calls live in a dedicated `prompts.py` so they can be tuned independently of the generation logic.

---

## New Files

### `prompts.py` (root level)
Central registry for all LLM prompts. Also a migration target for the existing inline summarizer prompt in `summarizer.py`.

```python
def linkedin_post_prompt(title, channel, summary, tags, url) -> str:
    """Returns user message to generate a LinkedIn text post."""

def linkedin_slides_prompt(title, channel, summary, tags) -> str:
    """Returns prompt to generate carousel slide content as structured JSON."""
```

Each function returns a `str` (user message). The caller passes it to Claude with a fixed system instruction defined in the same file (`LINKEDIN_SYSTEM_PROMPT`).

Slide content prompt asks Claude to return JSON:
```json
{
  "slides": [
    {"type": "title", "heading": "...", "subheading": "..."},
    {"type": "insight", "heading": "...", "body": "..."},
    {"type": "cta", "heading": "...", "body": "...", "url": "..."}
  ]
}
```

---

### `linkedin_generator.py` (root level)
Core module with no Flask dependency.

| Function | Responsibility |
|---|---|
| `get_video(video_id, detail)` | Fetch item from DynamoDB via `storage._dynamo_table()` |
| `generate_post_text(video_data)` | Call Claude with `prompts.linkedin_post_prompt(...)`, return post string |
| `generate_slide_data(video_data)` | Call Claude with `prompts.linkedin_slides_prompt(...)`, return parsed slide list |
| `build_pdf(slides, video_data)` | Build carousel PDF with ReportLab, return `bytes` |
| `save_outputs(video_id, post_text, pdf_bytes)` | Save `.txt` and `.pdf` to `outputs/linkedin/YYYY-MM-DD/` |

Claude calls reuse the existing `boto3` bedrock-runtime client pattern from `summarizer.py`.

---

### `templates/linkedin.html`
New page following the same dark-theme CSS variables and `_nav.html` include pattern.

**Layout:**
1. **Select panel** — dropdown of recent DynamoDB items (video title + ID), detail level selector, Generate button
2. **Post preview** — generated LinkedIn text in a copyable `<textarea>`
3. **Download buttons** — "Download Post (.txt)" and "Download PDF"

---

## Modified Files

### `app.py`
Two new routes:
- `GET/POST /linkedin` — render form, handle generate, serve preview
- `GET /linkedin/download` — serve generated PDF or TXT as attachment

### `templates/_nav.html`
Add "LinkedIn" tab alongside Search, Terms, and DB Explorer.

---

## PDF Carousel Design (ReportLab)

- **Page size:** 1080×1080px (square — LinkedIn carousel standard)
- **Slide 1 (Title):** Video title (large), channel name, tags row
- **Slides 2–N (Insight):** Bold heading, body text, slide number
- **Last slide (CTA):** "Watch the full video", YouTube URL, branding line

**Colour palette:** `#0a0a0f` background, `#6c63ff` accent, white text  
**Font:** Helvetica (ReportLab built-in, no install)

---

## Output Files

Saved to `outputs/linkedin/YYYY-MM-DD/`:
- `{video_id}_{detail}_post.txt`
- `{video_id}_{detail}_carousel.pdf`

---

## Dependencies

```
reportlab
```

No other new dependencies — Claude calls reuse existing `boto3` bedrock-runtime.

---

## Verification

1. `pip install reportlab` in venv, start app
2. Navigate to `http://127.0.0.1:8051/linkedin`
3. Select a video from DynamoDB, click **Generate**
4. Confirm post text renders in preview textarea
5. Click **Download PDF** — verify title slide, insight slides, CTA slide
6. Check `outputs/linkedin/YYYY-MM-DD/` for both `.txt` and `.pdf`
7. Verify LinkedIn nav tab appears on all other pages
