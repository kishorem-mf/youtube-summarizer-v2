# DynamoDB Graph Explorer — Detailed Implementation Plan

## Goal
A D3.js force-directed graph that visualises DynamoDB content as an interconnected
knowledge graph. Nodes represent search terms, tags, and articles. Edges are driven
by shared tags, enabling cross-topic and cross-platform discovery.

---

## Node Types

| Type | Shape | Colour | Size | Label |
|---|---|---|---|---|
| `search_term` | Circle | Blue | 28px | search term text |
| `tag` | Diamond / rect | Orange | 18px | tag text |
| `article` | Circle | Green (YT) / Purple (LI) / Teal (Google) | 10px | title (truncated) |

## Edge Types

| Edge | Direction | Meaning |
|---|---|---|
| `search_term → tag` | Directed | ≥1 article in this search_term carries this tag |
| `tag → article` | Directed | Article has this tag |

Two articles that share a tag are **implicitly** connected via the shared tag node —
no direct article-to-article edges needed, keeps the graph readable.

---

## Data Flow

```
DynamoDB scan (all items)
    │
    ▼
graph_data.py  ─── build_graph()
    │   ├── deduplicate nodes by id
    │   ├── build search_term nodes  (one per unique search_term value)
    │   ├── build tag nodes          (one per unique tag across all items)
    │   ├── build article nodes      (one per item: video_id + detail as id)
    │   ├── edges: search_term → tag (where article bridges them)
    │   └── edges: tag → article
    ▼
/graph/data  →  { nodes: [...], links: [...] }   (JSON, consumed by D3)
```

---

## Backend — New Files & Routes

### `graph_data.py`  (new module)
```
build_graph(platform=None, search_term=None, tag=None, date_from=None)
    → { nodes: list[dict], links: list[dict] }

scan_all_items()
    → raw DynamoDB items via full table scan (paginated)

node_detail(item_id)
    → single item by video_id (for click panel)
```

- Uses existing `storage._dynamo_table()` — no new AWS config.
- Returns only fields needed for the graph; strips `transcript` to keep payload small.
- Supports optional filters so the UI can scope the graph without a full scan every time.

### New Flask routes in `app.py`

| Route | Method | Purpose |
|---|---|---|
| `/graph` | GET | Render `dynamo_graph_v2.html` (new tab page) |
| `/graph/data` | GET | Return graph JSON; query params: `platform`, `search_term`, `tag`, `date_from` |
| `/graph/node/<video_id>` | GET | Return full item JSON for the detail panel |
| `/graph/meta` | GET | Return lists of all unique tags, search_terms, platforms (for filter dropdowns) |

---

## Frontend — `templates/dynamo_graph_v2.html`

### Layout (3-column)
```
┌──────────────┬──────────────────────────────┬─────────────────┐
│  Filter      │     D3 Graph Canvas          │  Node Detail    │
│  Panel       │                              │  Panel          │
│  (220px)     │     (flex-fill)              │  (300px)        │
└──────────────┴──────────────────────────────┴─────────────────┘
```

### Filter Panel (left)
- Platform checkboxes: YouTube / LinkedIn / Google
- Search term multi-select (populated from `/graph/meta`)
- Tag multi-select with search input
- Date range picker (from / to)
- "Apply Filters" button → re-fetches `/graph/data` with params
- "Reset" button

### Graph Canvas (centre)
- D3 v7 force-directed simulation
  - `forceLink` — edge tension
  - `forceManyBody` — repulsion (strength scaled by node type)
  - `forceCollide` — prevent overlap
  - `forceCenter` — anchor to canvas centre
- On load: fetch `/graph/data`, render all nodes + edges
- **Node interactions:**
  - Hover → tooltip with label + stats (article count / tag count)
  - Click → highlight connected subgraph (dim others), populate detail panel
  - Double-click article node → open source URL in new tab
  - Drag nodes to reposition (D3 drag behaviour)
- **Edge rendering:**
  - Arrow markers on directed edges
  - Thin grey lines (search_term→tag); lighter lines (tag→article)
- **Controls (top-right overlay):**
  - Zoom in / out buttons
  - "Fit to screen" button
  - Toggle labels on/off
  - Legend (node type colour key)

### Node Detail Panel (right)
Populated on click of an article node:
- Title, author/channel, platform badge
- Published date, engagement stats (likes, views, comments)
- Tags (clickable — clicking a tag highlights that tag's subgraph)
- Summary text (scrollable)
- "Open source" link button
- "Copy summary" button

---

## Implementation Phases

### Phase 1 — Backend graph API
1. Write `graph_data.py`: `scan_all_items()` + `build_graph()`
2. Add `/graph/data` and `/graph/meta` routes
3. Test via `curl` — verify node/link counts match DynamoDB item count

### Phase 2 — Static graph render
1. Create `dynamo_graph_v2.html` with D3 force simulation
2. Render all nodes and edges; colour by type
3. Add zoom/pan and basic tooltips
4. Add "Graph" tab to `_nav.html`

### Phase 3 — Interactivity
1. Click → highlight subgraph (dim unrelated nodes/edges)
2. Click article node → populate detail panel from `/graph/node/<id>`
3. Drag, double-click to open URL
4. Toggle labels

### Phase 4 — Filters
1. Populate filter panel from `/graph/meta`
2. Wire "Apply Filters" to re-fetch `/graph/data` with query params and re-render
3. Tag-click in detail panel triggers same filter

### Phase 5 — Polish
1. Loading spinner during data fetch
2. Empty-state message when filters return 0 nodes
3. Node size scaled by article count (for search_term nodes) or tag frequency (for tag nodes)
4. Cluster hulls: faint coloured background grouping articles by search_term

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Graph library | D3 v7 (inline, no CDN) | Full control, no external dependency, fits existing inline-JS pattern |
| Data fetch | Single `/graph/data` JSON call on load | Simpler than streaming; graph fits in memory |
| No article-to-article edges | Omitted | Keeps edge count manageable; tag nodes serve as proxies |
| Transcript excluded from graph payload | Yes | Payload size — detail panel fetches it on demand |
| Full scan vs GSI | Full scan for graph | GSIs only help for keyed lookups; graph needs all items |

---

## Files Created / Modified

| File | Action |
|---|---|
| `graph_data.py` | **New** — graph build logic |
| `templates/dynamo_graph_v2.html` | **New** — graph UI |
| `app.py` | **Modified** — add `/graph` routes |
| `templates/_nav.html` | **Modified** — add Graph tab |

No DynamoDB schema changes required. No new AWS permissions required.
