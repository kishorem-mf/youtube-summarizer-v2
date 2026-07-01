# Plan: Multi-Source DynamoDB Table Structure

## Context

The current table (`yt-summarizer-cache`) is tightly coupled to YouTube — the PK is named `video_id`, and fields like `channel`, `views`, `duration`, `url` are YouTube-specific. Adding Google Search (or any other source) requires a table design that works for any content type without breaking existing YouTube data.

---

## Current Table Structure (Problem Areas)

| Field | Problem for multi-source |
|-------|--------------------------|
| `video_id` (PK) | Name is YouTube-specific; Google results have no "video ID" |
| `channel` | YouTube concept; Google Search has an author/publisher |
| `views` | YouTube-specific; not applicable to articles |
| `duration` | Video-only concept |
| `source` | Currently means "how transcript was fetched" (youtube-transcript-api / apify), not "which platform" |
| `source_type` | Currently means "llm vs cache" — overloaded with `source` |

---

## Recommended Approach: Extend in Place (no new table)

DynamoDB PK names are just strings — `video_id` can hold any content identifier (YouTube ID, URL hash, article slug). No migration needed for existing rows.

### 1. Add `source_platform` field (new)
- `"youtube"` for all existing and new YouTube items
- `"google_search"` for Google Search results
- Enables filtering by platform in the explorer and dropdowns

### 2. Add `content_type` field (new)
- `"video"` for YouTube
- `"article"` for Google Search results
- Future: `"podcast"`, `"pdf"`, etc.

### 3. Treat `video_id` PK as generic `content_id`
- YouTube: use existing 11-char video ID (`dQw4w9WgXcQ`)
- Google Search: use a stable hash of the URL (`md5(url)[:12]`)
- No rename needed — DynamoDB doesn't enforce semantic meaning on PK names

### 4. Rename `source` → `fetch_method` (semantic clarification)
- Current values: `"youtube-transcript-api"`, `"apify"`
- Google Search values: `"requests"`, `"apify_scraper"`, etc.
- Makes it clear this field is about HOW content was fetched, not WHERE it came from

### 5. Sparse attributes for platform-specific fields
- YouTube items keep: `channel`, `views`, `duration`
- Google Search items add: `author`, `domain`, `published_date`
- Fields not applicable to a platform are simply absent (DynamoDB schemaless — no nulls needed)

### 6. Add GSI on `source_platform`
- PK: `source_platform`, SK: `searched_on`
- Enables "show me all Google Search results" queries efficiently

---

## Resulting Schema (after changes)

| Field | YouTube | Google Search | Notes |
|-------|---------|---------------|-------|
| `video_id` (PK) | `dQw4w9WgXcQ` | `md5(url)[:12]` | Used as generic content_id |
| `detail` (SK) | `high` | `high` | Unchanged |
| `source_platform` | `youtube` | `google_search` | **NEW** |
| `content_type` | `video` | `article` | **NEW** |
| `fetch_method` | `youtube-transcript-api` | `requests` | Renamed from `source` |
| `source_type` | `llm` / `cache` | `llm` / `cache` | Unchanged |
| `title` | ✓ | ✓ | Generic |
| `url` | ✓ | ✓ | Generic |
| `summary` | ✓ | ✓ | Generic |
| `transcript` | ✓ | ✓ (page text) | Generic |
| `tags` | ✓ | ✓ | Generic |
| `search_term` | ✓ | ✓ | Generic |
| `searched_on` | ✓ | ✓ | Generic |
| `channel` | ✓ | — | YouTube-only (sparse) |
| `views` | ✓ | — | YouTube-only (sparse) |
| `duration` | ✓ | — | YouTube-only (sparse) |
| `author` | — | ✓ | Google-only (sparse) |
| `domain` | — | ✓ | Google-only (sparse) |

---

## Code Changes Required

### `storage.py`
- `save_result()`: write `source_platform`, `content_type`; rename `source` → `fetch_method`
- `check_cache()`: no key change needed; add `source_platform` to returned dict
- `create_source_platform_gsi()`: new function, mirrors `create_search_term_gsi()`
- Backfill script: set `source_platform="youtube"`, `content_type="video"` on all existing rows

### `app.py`
- Pass `source_platform` and `content_type` into `save_result()` calls
- `/dynamo/filters`: include `source_platform` in response for a new dropdown in explorer

### `templates/dynamo_explorer.html`
- Add Q7 — Filter by Platform (`source_platform` dropdown)
- Tag/topic dropdowns can optionally be scoped to a platform

### `linkedin_generator.py` / `mixer_generator.py`
- No breaking changes — they read `title`, `channel`, `summary` etc. which remain present for YouTube items

---

## Migration Plan

1. Run backfill to set `source_platform="youtube"` + `content_type="video"` on all existing rows
2. Create `source_platform-index` GSI
3. Deploy code changes
4. New YouTube writes automatically get the new fields
5. Google Search writes use URL hash as `video_id`, set `source_platform="google_search"`

Zero downtime — existing rows remain readable throughout. Backfill is additive (no field removed).

---

## Verification
- Backfill: scan table, confirm all rows have `source_platform` set
- GSI: call `create_source_platform_gsi()`, wait for ACTIVE
- DB Explorer: Q7 returns YouTube rows only when `source_platform=youtube` selected
- Existing YouTube cache hits still work (PK/SK unchanged)
- New Google Search item saves and is retrieved by `check_cache(url_hash, detail)`
