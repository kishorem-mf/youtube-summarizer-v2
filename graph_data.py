"""Graph data builder for the DynamoDB knowledge graph explorer."""
from __future__ import annotations
import storage

PLATFORM_COLOURS = {
    "youtube":       "#ff4d4f",
    "linkedin_post": "#a78bfa",
    "google_search": "#2dd4bf",
}


def scan_all_items() -> list[dict]:
    """Full paginated scan. Returns lightweight dicts (no transcript)."""
    table = storage._dynamo_table()
    keep = {"video_id", "detail", "title", "url", "search_term", "tags",
            "source_platform", "date", "views", "likes", "comments", "author", "summary", "headline"}
    kwargs: dict = {
        "ProjectionExpression": ", ".join(f"#f{i}" for i in range(len(keep))),
        "ExpressionAttributeNames": {f"#f{i}": k for i, k in enumerate(keep)},
    }
    items = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def build_graph(
    platform: str | None = None,
    search_term: str | None = None,
    tag: str | None = None,
) -> dict:
    """
    Returns { nodes: [...], links: [...] }

    Node types:
      search_term — one per unique search_term value
      tag         — one per unique tag string
      article     — one per DynamoDB item (deduped to best-detail per video_id)

    Edge types:
      term_tag    — search_term → tag  (if ≥1 article bridges them)
      tag_article — tag → article
    """
    raw = scan_all_items()

    if platform:
        raw = [i for i in raw if i.get("source_platform") == platform]
    if search_term:
        raw = [i for i in raw if i.get("search_term") == search_term]
    if tag:
        raw = [i for i in raw if tag in (i.get("tags") or [])]

    detail_rank = {"high": 3, "medium": 2, "low": 1}
    best: dict[str, dict] = {}
    for item in raw:
        vid = item.get("video_id", "")
        if not vid:
            continue
        prev = best.get(vid)
        if prev is None or detail_rank.get(item.get("detail", ""), 0) > detail_rank.get(prev.get("detail", ""), 0):
            best[vid] = item
    items = list(best.values())

    nodes: dict[str, dict] = {}
    links: list[dict] = []
    term_tag_seen: set[tuple] = set()

    def add_node(nid, **kwargs):
        if nid not in nodes:
            nodes[nid] = {"id": nid, **kwargs}

    for item in items:
        st    = (item.get("search_term") or "").strip()
        tags  = [t.strip() for t in (item.get("tags") or []) if t.strip()]
        vid   = item.get("video_id", "")
        plat  = item.get("source_platform", "youtube")
        title = (item.get("title") or item.get("author") or vid)[:80]

        if st:
            st_id = f"st::{st}"
            add_node(st_id, type="search_term", label=st, count=0)
            nodes[st_id]["count"] = nodes[st_id].get("count", 0) + 1

        art_id = f"art::{vid}"
        add_node(art_id,
                 type="article",
                 label=title,
                 platform=plat,
                 url=item.get("url", ""),
                 date=str(item.get("date", "")),
                 views=int(item.get("views") or 0),
                 likes=int(item.get("likes") or 0),
                 comments=int(item.get("comments") or 0),
                 author=item.get("author", ""),
                 search_term=st,
                 summary=(item.get("summary") or "")[:400],
                 video_id=vid)

        for t in tags:
            tag_id = f"tag::{t}"
            add_node(tag_id, type="tag", label=t, count=0)
            nodes[tag_id]["count"] = nodes[tag_id].get("count", 0) + 1
            links.append({"source": tag_id, "target": art_id, "type": "tag_article"})
            if st:
                st_id = f"st::{st}"
                key = (st_id, tag_id)
                if key not in term_tag_seen:
                    term_tag_seen.add(key)
                    links.append({"source": st_id, "target": tag_id, "type": "term_tag"})

    return {"nodes": list(nodes.values()), "links": links}


def get_meta() -> dict:
    """Return sorted lists of unique search_terms, tags, platforms."""
    items = scan_all_items()
    search_terms: set[str] = set()
    tags: set[str] = set()
    platforms: set[str] = set()
    for item in items:
        if item.get("search_term"):
            search_terms.add(item["search_term"].strip())
        for t in (item.get("tags") or []):
            if t.strip():
                tags.add(t.strip())
        if item.get("source_platform"):
            platforms.add(item["source_platform"])
    return {
        "search_terms": sorted(search_terms),
        "tags": sorted(tags),
        "platforms": sorted(platforms),
    }
