# Mixer Tab — Implementation Plan

**Date:** 2026-06-22  
**Status:** Ready to implement — only `templates/mixer.html` is missing

---

## Goal

Add a **Mixer** tab where the user picks two cached video summaries (one supply-chain, one technology), asks the LLM to score the technology fit (1–10), and — if the score looks good — generates a LinkedIn post + carousel PDF.

---

## What Already Exists (do NOT touch)

| File | What's already done |
|------|---------------------|
| `app.py` | Routes `/mixer`, `/mixer/score`, `/mixer/generate`, `/mixer/download`, `/mixer/videos` all wired up |
| `mixer_generator.py` | `get_video`, `list_recent`, `score_fit`, `generate_post`, `generate_slides` fully implemented |
| `prompts.py` | `MIXER_SCORE_SYSTEM`, `mixer_score_prompt`, `mixer_post_prompt`, `mixer_slides_prompt` all present |
| `templates/_nav.html` | Mixer tab link already in the nav bar |
| `linkedin_generator.py` | `build_pdf`, `save_outputs` reused by mixer generate route |

**Only file to create:** `templates/mixer.html`

---

## UI Flow (3 Stages)

### Stage 1 — Selection
- Two dropdowns populated from `/mixer/videos` (same DynamoDB list)
  - Left: **Supply Chain** video (label filtered/tagged hint)
  - Right: **Technology** video
  - Each has a detail selector (low / medium / high), auto-filled from the item's stored detail
- **Score Fit** button → calls `/mixer/score` (JSON POST), shows spinner

### Stage 2 — Score Result (revealed after scoring)
- Score badge (1–10, colour-coded: red ≤4, amber 5–6, green ≥7)
- Headline (one-sentence verdict)
- Reasoning paragraph (3–5 sentences)
- Optional **Customization** textarea
- **Generate Post + PDF** button → submits form to `/mixer/generate`
- Score, headline, reasoning passed as hidden fields into the generate form

### Stage 3 — Output (rendered after generate)
- LinkedIn post textarea (copy-to-clipboard)
- Download Carousel PDF button → `/mixer/download?type=pdf&stem=...`
- Download Post TXT button → `/mixer/download?type=txt&stem=...`
- Files saved notice

---

## Template Variables (from `app.py` routes)

### GET `/mixer` → `mixer_page()`
```
active_tab="mixer"
post_text=None
error=None
sc_video_id=""
sc_detail="high"
tech_video_id=""
tech_detail="high"
score=None
headline=None
reasoning=None
customization=""
```

### POST `/mixer/generate` → `mixer_generate()` (returns same template)
```
+ post_text=<string>
+ pdf_stem=<string>   (used for download URL)
+ pdf_path, txt_path  (for display)
```

### `/mixer/score` — JSON endpoint (called via fetch)
Returns: `{ score, headline, reasoning, sc_title, tech_title }`

### `/mixer/videos` — JSON endpoint
Returns list of: `{ video_id, detail, title, channel, search_term, tags }`

---

## Design System (match existing pages)

CSS variables to reuse from `linkedin.html`:
```
--bg:#0f1115  --card:#1a1d24  --line:#2a2f3a  --fg:#e7e9ee
--mut:#9aa3b2  --accent:#6c63ff  --good:#3ecf8e  --warn:#f5a623  --red:#ff4d4f
```

Score colour rules:
- 1–4 → `var(--red)` (poor fit)
- 5–6 → `var(--warn)` (moderate)
- 7–10 → `var(--good)` (strong / exceptional)

Score badge: large number (48px), circular background matching colour.

---

## JavaScript Logic

1. **On page load** — fetch `/mixer/videos`, populate both dropdowns. Mark selected items from template vars (`sc_video_id`, `tech_video_id`) if returning from a generate.
2. **Score Fit click** — POST JSON to `/mixer/score`, show spinner, on success reveal score panel (Stage 2). On error show flash message.
3. **Generate form submit** — standard form POST to `/mixer/generate`. Show spinner with label "Generating LinkedIn content…". Score/headline/reasoning injected as hidden inputs from the JS score result.
4. **Copy to clipboard** — same pattern as `linkedin.html`.

---

## Key Constraints

- **Do NOT modify** `app.py`, `mixer_generator.py`, `prompts.py`, `linkedin_generator.py`, `storage.py`, `_nav.html`, or any other existing template.
- Only create `templates/mixer.html`.
- Match the visual style of `linkedin.html` exactly (same CSS vars, card layout, spinner pattern, copy button pattern).
- Spinner label changes: "Scoring fit…" during `/mixer/score`, "Generating LinkedIn content…" during form submit.
- Stage 2 (score panel) is hidden on initial load; shown via JS after a successful score call.
- Stage 3 (post + downloads) is shown only when `post_text` is truthy (Jinja condition).

---

## File to Create

| Path | Action |
|------|--------|
| `templates/mixer.html` | CREATE — complete Jinja2 + HTML template |

---

## Acceptance Criteria

- [ ] Mixer tab loads without errors
- [ ] Both dropdowns populate from DynamoDB cache
- [ ] Score Fit returns score, headline, reasoning and displays them
- [ ] Score badge colour matches severity (red/amber/green)
- [ ] Generate Post + PDF works and shows the LinkedIn post
- [ ] Download PDF and Download TXT links work
- [ ] No existing route or template is modified
