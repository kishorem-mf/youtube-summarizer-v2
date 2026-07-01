# DB Explorer → LinkedIn Export Feature

## Context
The DB Explorer shows summarised content (YouTube, LinkedIn, Google) stored in DynamoDB.
The user wants to select one or more rows and generate a LinkedIn-ready artifact
(text post they can copy-paste to LinkedIn) from the selected items' summaries.
The existing `linkedin_generator.py` handles single-item carousel generation — this
feature adds multi-select in the DB Explorer UI and a new synthesis route that
aggregates multiple items into one LinkedIn post via Claude.

---

## What Gets Built (V1 — text post only)

1. Checkboxes on each DB Explorer result row + "select all" header checkbox
2. Floating export toolbar at bottom of viewport (appears when ≥1 row selected)
3. New route `POST /dynamo/export-linkedin` in `app.py` that calls Claude to
   synthesise a LinkedIn post from selected items and returns JSON
4. Slide-up preview panel: editable textarea + char counter + Copy button + Regenerate

**V2 (deferred):** multi-item carousel PDF reusing `linkedin_generator.build_pdf()`

---

## Data Flow

```
User checks rows in DB Explorer
    │
    ▼
JS collects: [{title, author, summary, tags, url, source_platform}, ...]
    │
    ▼
POST /dynamo/export-linkedin  { items: [...], style: "insights|tips|thread" }
    │
    ├── Build prompt: feed title + summary[:400] + tags per item to Claude
    ├── Claude returns delimiter-separated: post_text + hashtags
    └── Return JSON: { post_text, hashtags, char_count }
    │
    ▼
Frontend: slide-up preview panel → user edits → Copy → paste to LinkedIn
```

---

## Backend — New route in `app.py`

Add after the `/dynamo/filters` route. Copies the Anthropic client init pattern
from `linkedin_generator.py` lines 14–31 (same env vars, same `verify=False` httpx).

```python
@app.route("/dynamo/export-linkedin", methods=["POST"])
def dynamo_export_linkedin():
    from flask import jsonify
    import anthropic as _asdk, httpx as _httpx

    data  = request.get_json(force=True) or {}
    items = data.get("items", [])          # list of row dicts from JS
    style = data.get("style", "insights")  # insights | tips | thread

    if not items:
        return jsonify({"error": "No items selected"}), 400

    # Build condensed digest of each item
    digests = []
    for i, it in enumerate(items, 1):
        title   = it.get("title", "Untitled")
        author  = it.get("author") or it.get("channel", "")
        summary = (it.get("summary") or "")[:400]
        tags    = ", ".join(it.get("tags", []))
        digests.append(f"[{i}] {title}\nAuthor: {author}\nTags: {tags}\nSummary: {summary}")
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
        f"- Post body: max 1300 characters, professional but conversational tone\n"
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
            max_tokens=600,
        )
        raw = resp.content[0].text.strip()
        if "---HASHTAGS---" in raw:
            post_part, tag_part = raw.split("---HASHTAGS---", 1)
            post_text = post_part.strip()
            hashtags  = [f"#{t.strip().lstrip('#')}" for t in tag_part.strip().splitlines() if t.strip()]
        else:
            post_text = raw
            hashtags  = []
        return jsonify({"post_text": post_text, "hashtags": hashtags, "char_count": len(post_text)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

---

## Frontend — Changes to `dynamo_explorer.html`

### 1. CSS additions (into existing `<style>` block)
```css
input[type=checkbox].row-check { cursor:pointer; accent-color:var(--accent); width:15px; height:15px; }
tr.row-selected td { background:#1e1a24 !important; }
#export-toolbar { display:none; position:fixed; bottom:0; left:0; right:0;
  background:#1a1d24; border-top:1px solid var(--accent); padding:12px 24px;
  z-index:100; align-items:center; gap:16px; }
#export-toolbar.visible { display:flex; }
#export-count { color:var(--accent); font-weight:600; font-size:14px; }
button.li-export { background:var(--accent); color:#fff; border:0; border-radius:8px;
  padding:9px 22px; font-weight:600; cursor:pointer; font-size:14px; }
button.li-export:disabled { opacity:.5; cursor:default; }
#li-modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,.7); z-index:200; }
#li-modal-backdrop.visible { display:flex; align-items:center; justify-content:center; }
#li-modal { background:var(--card); border:1px solid var(--line); border-radius:14px;
  width:min(640px,95vw); padding:24px; display:flex; flex-direction:column; gap:14px; max-height:90vh; overflow-y:auto; }
#li-post-textarea { width:100%; min-height:200px; background:#0f1115; border:1px solid var(--line);
  color:var(--fg); border-radius:8px; padding:12px; font-size:14px; line-height:1.6; resize:vertical; }
#li-hashtags { color:var(--accent); font-size:13px; }
#li-charcount { font-size:12px; }
.li-modal-actions { display:flex; gap:10px; flex-wrap:wrap; }
button.li-copy { background:var(--good); color:#000; border:0; border-radius:8px;
  padding:9px 22px; font-weight:600; cursor:pointer; font-size:14px; }
button.li-regen { background:var(--line); color:var(--fg); border:0; border-radius:8px;
  padding:9px 22px; font-weight:600; cursor:pointer; font-size:14px; }
```

### 2. Table header — prepend `<th>` with "select all" checkbox (line ~161 in current file)
```html
<th><input type="checkbox" class="row-check" id="check-all" title="Select all"></th>
```
Also change `colspan="13"` to `colspan="14"` on the expand-row `<td>` (line ~238).

### 3. Table rows — prepend `<td>` with per-row checkbox (after `{% for r in rows %}`)
```html
<td style="width:36px;">
  <input type="checkbox" class="row-check row-item"
    data-row='{{ {"title": r.title, "author": r.get("author") or r.channel,
                  "summary": r.summary, "tags": r.get("tags", []),
                  "url": r.get("url",""), "source_platform": r.get("source_platform","")} | tojson | e }}'>
</td>
```

### 4. Export toolbar — add before closing `</div>` of `.wrap`
```html
<div id="export-toolbar">
  <span id="export-count">0 selected</span>
  <select id="export-style">
    <option value="insights">Insights</option>
    <option value="tips">Tips</option>
    <option value="thread">Thread</option>
  </select>
  <button class="li-export" id="export-btn" onclick="exportToLinkedIn()">Export to LinkedIn</button>
  <button class="li-export" style="background:var(--line);color:var(--fg);" onclick="clearSelection()">Clear</button>
</div>
```

### 5. Preview modal HTML — add after export toolbar
```html
<div id="li-modal-backdrop">
  <div id="li-modal">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <h3 style="margin:0;">LinkedIn Post Preview</h3>
      <button onclick="closeLiModal()" style="background:transparent;border:0;color:var(--mut);font-size:22px;cursor:pointer;line-height:1;">×</button>
    </div>
    <textarea id="li-post-textarea" oninput="updateCharCount()"></textarea>
    <div id="li-hashtags"></div>
    <div id="li-charcount"></div>
    <div class="li-modal-actions">
      <button class="li-copy" onclick="copyPost()">Copy Post</button>
      <button class="li-regen" onclick="exportToLinkedIn(true)">Regenerate</button>
    </div>
  </div>
</div>
```

### 6. JS — add to existing `<script>` block
```javascript
let selectedRows = [];

function updateSelectionState() {
  selectedRows = [];
  document.querySelectorAll('.row-item:checked').forEach(cb => {
    try { selectedRows.push(JSON.parse(cb.dataset.row)); } catch(e) {}
  });
  const toolbar = document.getElementById('export-toolbar');
  document.getElementById('export-count').textContent = selectedRows.length + ' selected';
  toolbar.classList.toggle('visible', selectedRows.length > 0);
  document.querySelectorAll('.row-item').forEach(cb => {
    cb.closest('tr').classList.toggle('row-selected', cb.checked);
  });
}

document.addEventListener('change', function(e) {
  if (e.target.id === 'check-all')
    document.querySelectorAll('.row-item').forEach(cb => cb.checked = e.target.checked);
  if (e.target.classList.contains('row-check')) updateSelectionState();
});

function clearSelection() {
  document.querySelectorAll('.row-check').forEach(cb => cb.checked = false);
  updateSelectionState();
}

function exportToLinkedIn(regen) {
  if (!selectedRows.length) return;
  const btn = document.getElementById('export-btn');
  btn.disabled = true; btn.textContent = 'Generating…';
  fetch('/dynamo/export-linkedin', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ items: selectedRows, style: document.getElementById('export-style').value })
  })
  .then(r => r.json())
  .then(data => {
    btn.disabled = false; btn.textContent = 'Export to LinkedIn';
    if (data.error) { alert('Error: ' + data.error); return; }
    document.getElementById('li-post-textarea').value = data.post_text || '';
    document.getElementById('li-hashtags').textContent = (data.hashtags || []).join('  ');
    updateCharCount();
    document.getElementById('li-modal-backdrop').classList.add('visible');
  })
  .catch(err => { btn.disabled = false; btn.textContent = 'Export to LinkedIn'; alert('Failed: ' + err); });
}

function updateCharCount() {
  const n = document.getElementById('li-post-textarea').value.length;
  const el = document.getElementById('li-charcount');
  el.textContent = n + ' / 1300 chars';
  el.style.color = n > 1300 ? 'var(--accent)' : 'var(--mut)';
}

function copyPost() {
  navigator.clipboard.writeText(document.getElementById('li-post-textarea').value)
    .then(() => { const b = document.querySelector('.li-copy'); b.textContent='Copied!'; setTimeout(()=>b.textContent='Copy Post',2000); });
}

function closeLiModal() {
  document.getElementById('li-modal-backdrop').classList.remove('visible');
}
```

---

## Files Modified

| File | Change |
|------|--------|
| `app.py` | Add `POST /dynamo/export-linkedin` route (~55 lines, after `/dynamo/filters`) |
| `templates/dynamo_explorer.html` | CSS additions, checkbox `<th>`/`<td>`, colspan bump, toolbar, modal, JS |

**Key reuses:**
- `linkedin_generator.py` lines 14–31: exact Anthropic/Azure client init pattern
- Existing `---QUESTIONS---` delimiter pattern: same approach with `---HASHTAGS---`
- Existing `tojson` Jinja filter already in use on the page

---

## Verification
1. `python app.py` → open DB Explorer → run Q3 (Browse recent)
2. Confirm checkboxes appear on every row + "select all" in header
3. Check 2–3 rows → floating toolbar appears at bottom with count
4. Click Export → button shows "Generating…" → modal appears with post text
5. Edit textarea → char counter updates live, turns red when >1300
6. Click Copy → "Copied!" confirms clipboard write
7. Click Regenerate → new post variant for same items
8. Click × → modal closes; toolbar still shows selected count
