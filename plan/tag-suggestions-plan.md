# Tag Suggestions — Curate Search List from DynamoDB Tags

## Goal
A new "Curate" tab that scans all summarised DynamoDB items, analyses tag
frequencies per search group, and surfaces the top 3 tags (not already in the
search list) as suggested additions — one-click to add them.

---

## How It Works (Algorithm)

```
DynamoDB full scan
    │
    ▼
For each item: resolve search_term → group  (via search_terms.json mapping)
    │
    ▼
Per group: count tag frequency across all items in that group
    │
    ▼
Exclude tags already present as a search term (in ANY group, case-insensitive)
    │
    ▼
Rank remaining tags by frequency → top 3 per group = suggestions
```

**Why exclude across all groups?** Avoids suggesting "snowflake" for the
"Observability" group when it's already a search term under "Data platform".

---

## Example Output

| Group | Suggested Tag | Frequency | Action |
|---|---|---|---|
| Data platform | medallion architecture | 14 | + Add |
| Data platform | data lakehouse | 11 | + Add |
| Data platform | data mesh | 9 | + Add |
| Supply chain | s&op | 7 | + Add |
| lakehouse | delta lake | 6 | + Add |

---

## Data Flow

```
/curate  (GET)
    │
    ├── tag_suggestions.build_suggestions()
    │       ├── graph_data.scan_all_items()       ← reuse existing scanner
    │       ├── search_terms.get_groups()          ← current search list
    │       ├── term → group mapping
    │       ├── count tags per group
    │       ├── filter already-present terms
    │       └── return top-N per group
    │
    └── render curate.html  with suggestions

/curate/add  (POST)
    │
    ├── receives { group, term }
    ├── calls search_terms.save_terms() to append term to group
    └── redirects back to /curate  (flash confirmation)
```

---

## Backend — New File `tag_suggestions.py`

```python
build_suggestions(top_n=3) -> dict[group_name, list[SuggestionDict]]

SuggestionDict = {
    "tag":       str,   # the tag text
    "count":     int,   # how many articles in this group carry this tag
    "platforms": list,  # which platforms it appears on (for context)
    "samples":   list,  # up to 2 article titles as examples
}
```

Reuses `graph_data.scan_all_items()` — no extra DynamoDB calls.

---

## New Flask Routes in `app.py`

| Route | Method | Purpose |
|---|---|---|
| `/curate` | GET | Render curate.html with suggestions |
| `/curate/add` | POST | Add a suggested tag as a new search term to a group |
| `/curate/dismiss` | POST | Dismiss a suggestion (stored in session so it doesn't reappear) |
| `/curate/refresh` | POST | Force re-scan DynamoDB and re-render |

---

## Frontend — `templates/curate.html`

### Layout
```
┌──────────────────────────────────────────────────────┐
│  Nav bar                                             │
├──────────────────────────────────────────────────────┤
│  Page header: "Search List Curation"                 │
│  Subtitle: "Top tags from your DynamoDB content,     │
│  not yet in your search list"                        │
│  [ Refresh scan ]  Last scanned: 2 mins ago          │
├──────────────────────────────────────────────────────┤
│  Group: Data platform          (3 suggestions)       │
│  ┌──────────────────────────────────────────────┐    │
│  │ medallion architecture   14 articles  [+ Add] [✕]│
│  │ data lakehouse            11 articles  [+ Add] [✕]│
│  │ data mesh                  9 articles  [+ Add] [✕]│
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  Group: Supply chain           (2 suggestions)       │
│  ...                                                 │
└──────────────────────────────────────────────────────┘
```

### Per-suggestion row
- Tag name (bold)
- Article count badge (e.g. "14 articles")
- Platform pills (YouTube / LinkedIn / Google) showing which platforms surfaced it
- Expandable sample titles (click to reveal up to 2 article titles as proof)
- **[+ Add]** button → POST `/curate/add` → appends to group in `search_terms.json`
- **[✕]** dismiss button → POST `/curate/dismiss` → hides from this session

### States
- Groups with 0 suggestions: collapsed with "No new suggestions" note
- Loading spinner while DynamoDB scan runs (triggered on Refresh)
- Flash confirmation when a term is added: "✓ 'medallion architecture' added to Data platform"

---

## Implementation Phases

### Phase 1 — Backend
1. Write `tag_suggestions.py` with `build_suggestions(top_n=3)`
2. Add `/curate` GET route and `/curate/add` POST route
3. Test via curl: `GET /curate` should return rendered page with suggestion data

### Phase 2 — Basic UI
1. Create `curate.html` with group cards and suggestion rows
2. Wire [+ Add] button to POST `/curate/add`
3. Add "Curate" tab to `_nav.html`

### Phase 3 — Enhancements
1. Dismiss button + server-side session tracking
2. Expandable sample titles per suggestion
3. Platform pills per suggestion
4. Refresh button with loading state
5. `top_n` slider (default 3, max 10) in page header

---

## Files Created / Modified

| File | Action |
|---|---|
| `tag_suggestions.py` | **New** — suggestion algorithm |
| `templates/curate.html` | **New** — Curate tab UI |
| `app.py` | **Modified** — add `/curate` routes |
| `templates/_nav.html` | **Modified** — add Curate tab |

No DynamoDB schema changes. No new AWS permissions. Reuses `graph_data.scan_all_items()`.

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Top N per group | 3 (configurable) | Enough signal without overwhelming; slider lets user see more |
| Exclusion scope | Across ALL groups | Prevents duplicate coverage across groups |
| Case handling | Lowercase comparison | "Snowflake" and "snowflake" are the same term |
| Add action | Appends to `search_terms.json` via existing `save_terms()` | No new persistence layer needed |
| Dismiss persistence | Flask session | Lightweight; resets on restart which is fine for a curation tool |
| Scan freshness | On-demand (Refresh button) | Full scan can be slow; don't block page load on every visit |
