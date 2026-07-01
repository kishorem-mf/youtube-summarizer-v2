# Plan: Google Search Test Module (Apify)

## Context

The existing app searches YouTube via the YouTube Data API / yt-dlp and summarises video transcripts. The goal is to add a **Google Search tab** that searches for articles on the same topics using the Apify `apify/google-search-scraper` actor, displays results in the same card layout as YouTube, and lets the user open an article and generate an AI summary. This lives in the existing Flask app as new `/google` routes — easy to promote to a first-class tab once validated.

---

## New Files

| File | Purpose |
|------|---------|
| `google_search.py` | Apify search + article fetch + summarise |
| `templates/google_search.html` | Search form + results (mirrors `index.html`) |
| `templates/google_article.html` | Article summary page (mirrors `video_page.html`) |

## Modified Files

| File | Change |
|------|--------|
| `app.py` | Add `/google`, `/google/run`, `/google/summary`, `/google/article` routes |
| `templates/_nav.html` | Add "Google Search" tab |

---

## `google_search.py`

### 1. Search — `search_google(query, max_results, date_filter)`

Calls Apify `apify/google-search-scraper` actor via the same run-sync endpoint pattern used in `summarizer.py`:

```
POST https://api.apify.com/v2/acts/apify~google-search-scraper/run-sync-get-dataset-items
     ?token={APIFY_API_KEY}&timeout=120

Body:
{
  "queries": query,
  "maxPagesPerQuery": 1,
  "resultsPerPage": max_results,
  "dateRange": date_filter  # "anytime"|"pastDay"|"pastWeek"|"pastMonth"|"pastYear"
}
```

**Date filter mapping** (match existing YouTube UI labels → Apify values):

| UI label | YouTube value | Apify value |
|----------|--------------|-------------|
| Any time | `""` | `"anytime"` |
| Last 24 hours | `"today"` | `"pastDay"` |
| Last week | `"week"` | `"pastWeek"` |
| Last month | `"month"` | `"pastMonth"` |
| Last year | `"year"` | `"pastYear"` |

**Returns list of article dicts:**
```python
{
  "id":          md5(url)[:12],   # used as content_id in DynamoDB
  "title":       str,
  "url":         str,
  "domain":      str,             # e.g. "techcrunch.com"
  "date":        str,             # YYYY-MM-DD if available, else ""
  "description": str,             # snippet from Google
  "search_term": str,
}
```

### 2. Fetch Article Text — `fetch_article_text(url)`

```python
GET url  (requests, SSL verify=False, timeout=30, User-Agent header)
→ extract visible text via BeautifulSoup (strip nav/footer/ads)
→ return plain text string (first 16,000 chars)
```

Fallback: if direct fetch fails (403, paywall, timeout), return empty string with an error flag.

### 3. Summarise — `summarize_article(article, text, detail)`

Reuses the **same Claude client and prompt pattern** from `summarizer.py`:
- Same Anthropic Azure Foundry endpoint (`ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_DEPLOYMENT`)
- Same detail levels (low/medium/high) and token limits
- Same `_generate_tags(summary)` call for tag extraction
- System prompt adapted: "market-intelligence analyst summarising a web article"

**Returns same result shape as `fetch_and_summarize()`:**
```python
{
  "transcript": text,     # raw article text
  "summary":   str,
  "tags":      list[str],
  "source":    "apify_google_search",
  "wordCount": int,
  "language":  "en",
  "error":     str,
}
```

### 4. Storage — reuse `storage.save_result()` / `check_cache()`

- Pass `video_id = md5(url)[:12]` as the content identifier
- Pass `source_platform="google_search"`, `content_type="article"` (new fields — additive)
- `check_cache(url_hash, detail)` works unchanged (PK + SK lookup)

### 5. `run_topics(term_pairs, max_results, date_filter)`

Mirrors `summarizer.run_terms()`:
- `ThreadPoolExecutor` with 4 workers
- One `search_google()` call per term
- Returns `{term: {"results": [article, ...]}}` and errors dict

---

## App Routes (`app.py`)

### `GET /google`
Render `google_search.html` with:
- Same `groups`, `date_windows`, `detail_options` as the YouTube search form
- `active_tab="google"`, `results=None`

### `POST /google/run`
- Parse same form fields as `/run` (terms, custom, max_results, date_filter, detail)
- Call `google_search.run_topics(term_pairs, max_results, date_filter)`
- Render `google_search.html` with results

### `POST /google/summary` (JSON)
- Input: `{url, title, domain, date, description, detail, search_term}`
- Compute `content_id = md5(url)[:12]`
- Check `storage.check_cache(content_id, detail)`
- On miss: `google_search.fetch_article_text(url)` → `google_search.summarize_article()`
- Save to DynamoDB in background thread (same as `/video_summary`)
- Return JSON (same shape as `/video_summary`)

### `GET /google/article`
- Query params: url, title, domain, date, description, search_term
- Render `google_article.html` (article standalone page)

---

## Templates

### `google_search.html`
Mirrors `index.html` with:
- Same search form (terms checkboxes, custom field, max_results, date_filter, detail)
- Results section: cards per article showing title, domain, date, description snippet
- "View Summary" link → `/google/article?url=...&title=...&domain=...`
- No thumbnail (articles have none) — use domain favicon or placeholder icon

### `google_article.html`
Mirrors `video_page.html` with:
- Article title, domain, date, description
- Clickable URL → opens article in new tab
- Detail selector (Low / Medium / High)
- "Get Summary" button → POST to `/google/summary`
- Collapsible result panels: Summary + Article Text
- Copy-to-clipboard on summary

---

## Nav Update (`_nav.html`)

Add one tab:
```html
<a href="/google" class="app-tab {% if active_tab == 'google' %}active{% endif %}">Google Search</a>
```

---

## Environment Variables (no new vars needed)

| Variable | Reused from | Purpose |
|----------|------------|---------|
| `APIFY_API_KEY` | `summarizer.py:29` | Same token, new actor |
| `ANTHROPIC_FOUNDRY_API_KEY` | `summarizer.py:23` | Same Claude endpoint |
| `DYNAMO_TABLE` | `storage.py:18` | Same table |

---

## Dependencies

- `beautifulsoup4` — article text extraction (likely already installed; check `requirements.txt`)
- `requests` — already used in `summarizer.py` as `_req`
- All other deps already present

---

## Verification

1. Start app: `venv\Scripts\python app.py`
2. Navigate to `http://127.0.0.1:8051/google`
3. Select a topic (e.g. "observability"), date = "Last month", click Search
4. Confirm results appear as cards with title, domain, date, snippet
5. Click "View Summary" → opens `/google/article` page
6. Click "Get Summary" → summary + tags appear
7. Run again for same article → instant cache hit (DB Explorer Q1 with `md5(url)[:12]`)
8. DB Explorer: scan recent items, confirm `source_platform=google_search` present on new rows
