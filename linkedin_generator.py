"""LinkedIn post and carousel PDF generator.

Pulls video data from DynamoDB, calls Claude to generate post text and
carousel slide content, then renders a ReportLab PDF.
"""

from __future__ import annotations

import datetime
import json
import os

import httpx as _httpx
import anthropic as _anthropic_sdk

import storage
import prompts

# ─────────────────────────────────────────────
# Anthropic client (same pattern as summarizer.py)
# ─────────────────────────────────────────────

_anthropic = _anthropic_sdk.Anthropic(
    api_key=os.environ["ANTHROPIC_FOUNDRY_API_KEY"],
    base_url=os.environ.get(
        "ANTHROPIC_FOUNDRY_ENDPOINT",
        "https://nandamagatala-8810-resource.services.ai.azure.com/anthropic/v1",
    ),
    http_client=_httpx.Client(verify=False),
)
_MODEL = os.environ.get("ANTHROPIC_FOUNDRY_DEPLOYMENT", "claude-opus-4-8")

# ─────────────────────────────────────────────
# PDF constants
# ─────────────────────────────────────────────

_SLIDE_PX   = 1080
_SLIDE_PT   = _SLIDE_PX * 72 / 96   # ≈810 pt  (96 dpi → 72 pt/inch)
_PAD        = 54                      # pt — inner padding

# Colour palette (matches app dark theme)
_C_BG       = (0.059, 0.067, 0.094)   # #0f1115
_C_CARD     = (0.102, 0.114, 0.141)   # #1a1d24
_C_ACCENT   = (0.424, 0.388, 1.000)   # #6c63ff
_C_FG       = (0.906, 0.914, 0.933)   # #e7e9ee
_C_MUT      = (0.604, 0.639, 0.698)   # #9aa3b2
_C_WHITE    = (1.0, 1.0, 1.0)

# Extra palette entries used in backgrounds
_C_ACCENT2  = (0.180, 0.150, 0.480)   # deep indigo
_C_TEAL     = (0.059, 0.700, 0.600)   # #0fb399
_C_ROSE     = (0.800, 0.200, 0.400)   # warm rose accent


# ─────────────────────────────────────────────
# DynamoDB fetch
# ─────────────────────────────────────────────

def get_video(video_id: str, detail: str) -> dict | None:
    """Return the DynamoDB item for (video_id, detail), or None if not found."""
    table = storage._dynamo_table()
    resp  = table.get_item(Key={"video_id": video_id, "detail": detail})
    return resp.get("Item")


def list_recent(limit: int = 50) -> list[dict]:
    """Return up to `limit` recent DynamoDB items sorted newest first."""
    table = storage._dynamo_table()
    resp  = table.scan(Limit=limit)
    items = resp.get("Items", [])
    return sorted(items, key=lambda x: x.get("searched_on", ""), reverse=True)


# ─────────────────────────────────────────────
# LLM calls
# ─────────────────────────────────────────────

def generate_post_text(
    video_data: dict,
    post_style: str = "tips",
    customization: str = "",
    temperature: float = 0.7,
) -> str:
    """Call Claude to generate a LinkedIn text post. Returns post string."""
    user_msg = prompts.linkedin_post_prompt(
        title         = video_data.get("title", ""),
        channel       = video_data.get("channel", ""),
        summary       = video_data.get("summary", ""),
        tags          = list(video_data.get("tags", [])),
        url           = video_data.get("url", f"https://www.youtube.com/watch?v={video_data.get('video_id', '')}"),
        post_style    = post_style,
        customization = customization,
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=prompts.LINKEDIN_POST_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=600,
            temperature=round(float(temperature), 2),
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[Error generating post: {e}]"


def generate_slide_data(video_data: dict, post_style: str = "tips") -> list[dict]:
    """Call Claude to generate carousel slide dicts. Returns list of slide objects."""
    user_msg = prompts.linkedin_slides_prompt(
        title      = video_data.get("title", ""),
        channel    = video_data.get("channel", ""),
        summary    = video_data.get("summary", ""),
        tags       = list(video_data.get("tags", [])),
        post_style = post_style,
    )
    try:
        resp = _anthropic.messages.create(
            model=_MODEL,
            system=prompts.LINKEDIN_SLIDES_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=800,
        )
        raw  = resp.content[0].text.strip()
        data = json.loads(raw)
        return data.get("slides", [])
    except Exception as e:
        return [{"type": "title", "heading": video_data.get("title", "Video"), "subheading": f"Error: {e}"}]


# ─────────────────────────────────────────────
# PDF builder
# ─────────────────────────────────────────────

def build_pdf(slides: list[dict], video_data: dict) -> bytes:
    """Render a square carousel PDF with ReportLab. Returns raw PDF bytes."""
    from reportlab.pdfgen import canvas
    import io
    import random

    buf  = io.BytesIO()
    size = (_SLIDE_PT, _SLIDE_PT)
    c    = canvas.Canvas(buf, pagesize=size)

    channel = video_data.get("channel", "")
    yt_url  = video_data.get("url") or f"https://www.youtube.com/watch?v={video_data.get('video_id', '')}"
    tags    = list(video_data.get("tags", []))

    # Shuffle theme indices so each slide gets a different background
    theme_pool = list(range(5))
    random.shuffle(theme_pool)

    for idx, slide in enumerate(slides):
        stype      = slide.get("type", "insight")
        theme_idx  = theme_pool[idx % 5]
        _draw_slide(c, slide, stype, idx + 1, len(slides), channel, yt_url, tags, size, theme_idx)
        c.showPage()

    c.save()
    buf.seek(0)
    return buf.read()


def _bg_base(c, W, H):
    """Solid dark base drawn by every theme."""
    c.setFillColorRGB(*_C_BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)


def _bg1_dual_glow(c, W, H):
    """Theme 1 — Dual corner glow. Orbs centred off-canvas so only the edge bleeds in."""
    from reportlab.lib.colors import Color
    _bg_base(c, W, H)
    # Top-right glow (orb centre outside canvas)
    c.setFillColor(Color(*_C_ACCENT, alpha=0.18))
    c.circle(W + 60, H + 60, W * 0.55, fill=1, stroke=0)
    # Bottom-left glow
    c.setFillColor(Color(*_C_ACCENT2, alpha=0.20))
    c.circle(-60, -60, W * 0.45, fill=1, stroke=0)
    # Dot grid — bottom-right quadrant only (below text zone)
    c.setFillColor(Color(1, 1, 1, alpha=0.08))
    for col in range(5, 10):
        for row in range(1, 4):
            c.circle(col * W / 10, row * H / 10, 1.8, fill=1, stroke=0)
    # Thin rule under top bar
    c.setStrokeColor(Color(*_C_ACCENT, alpha=0.20))
    c.setLineWidth(0.5)
    c.line(0, H - 18, W, H - 18)
    c.setLineWidth(1)


def _bg2_left_panel(c, W, H):
    """Theme 2 — Bold left accent bar + diagonal ghost lines."""
    from reportlab.lib.colors import Color
    _bg_base(c, W, H)
    # Left accent bar
    c.setFillColor(Color(*_C_TEAL, alpha=0.55))
    c.rect(0, 0, 7, H, fill=1, stroke=0)
    c.setFillColor(Color(*_C_TEAL, alpha=0.20))
    c.rect(10, 0, 3, H * 0.55, fill=1, stroke=0)
    # Subtle diagonal lines (top-left to bottom-right)
    c.setStrokeColor(Color(1, 1, 1, alpha=0.04))
    c.setLineWidth(0.6)
    step = W / 6
    for i in range(8):
        x0 = -W + i * step
        c.line(x0, 0, x0 + W, H)
    # Bottom-right corner dots
    c.setFillColor(Color(*_C_TEAL, alpha=0.12))
    for col in range(6, 10):
        for row in range(1, 4):
            c.circle(col * W / 10, row * H / 10, 2.0, fill=1, stroke=0)
    c.setLineWidth(1)


def _bg3_concentric(c, W, H):
    """Theme 3 — Concentric rings bleeding in from top-right, small fill orb bottom-left."""
    from reportlab.lib.colors import Color
    _bg_base(c, W, H)
    # Concentric stroke rings centred off-canvas (top-right)
    cx, cy = W + 40, H + 40
    c.setLineWidth(0.6)
    for i, alpha in enumerate([0.18, 0.13, 0.09, 0.06, 0.04]):
        c.setStrokeColor(Color(*_C_ACCENT, alpha=alpha))
        c.circle(cx, cy, W * (0.40 + i * 0.12), fill=0, stroke=1)
    # Fill orb bottom-left corner
    c.setFillColor(Color(*_C_ACCENT2, alpha=0.22))
    c.circle(-50, -50, W * 0.38, fill=1, stroke=0)
    # Horizontal rule
    c.setStrokeColor(Color(*_C_ACCENT, alpha=0.15))
    c.setLineWidth(0.5)
    c.line(0, H - 18, W, H - 18)
    c.setLineWidth(1)


def _bg4_horizontal_bands(c, W, H):
    """Theme 4 — Layered horizontal bands + small corner squares."""
    from reportlab.lib.colors import Color
    _bg_base(c, W, H)
    # Bottom colour band (below text zone)
    c.setFillColor(Color(*_C_ACCENT, alpha=0.07))
    c.rect(0, 0, W, H * 0.18, fill=1, stroke=0)
    # Top colour band
    c.setFillColor(Color(*_C_ROSE, alpha=0.06))
    c.rect(0, H * 0.88, W, H * 0.12, fill=1, stroke=0)
    # Corner accent squares — top-left
    sq = 28
    gap = 6
    for i in range(3):
        for j in range(3):
            alpha = 0.20 - (i + j) * 0.04
            c.setFillColor(Color(*_C_ACCENT, alpha=max(alpha, 0.04)))
            c.rect(18 + i * (sq + gap), H - 18 - (j + 1) * (sq + gap), sq, sq,
                   fill=1, stroke=0)
    # Matching bottom-right
    for i in range(3):
        for j in range(3):
            alpha = 0.20 - (i + j) * 0.04
            c.setFillColor(Color(*_C_ROSE, alpha=max(alpha, 0.04)))
            c.rect(W - 18 - (i + 1) * (sq + gap), 18 + j * (sq + gap), sq, sq,
                   fill=1, stroke=0)
    # Thin rules
    c.setStrokeColor(Color(*_C_ACCENT, alpha=0.12))
    c.setLineWidth(0.5)
    c.line(0, H * 0.18, W, H * 0.18)
    c.line(0, H - 18, W, H - 18)
    c.setLineWidth(1)


def _bg5_diagonal(c, W, H):
    """Theme 5 — Diagonal accent band (corner to corner) + edge dot row."""
    from reportlab.lib.colors import Color
    _bg_base(c, W, H)
    # Diagonal parallelogram strip — bottom-left to top-right edge
    p = c.beginPath()
    bw = 55   # band half-width
    p.moveTo(0, bw * 2)
    p.lineTo(0, 0)
    p.lineTo(bw * 2, 0)
    p.lineTo(W, H - bw * 2)
    p.lineTo(W, H)
    p.lineTo(H - bw * 2, H)
    p.close()
    c.setFillColor(Color(*_C_ACCENT, alpha=0.07))
    c.drawPath(p, fill=1, stroke=0)
    # Thin diagonal line on band edge
    c.setStrokeColor(Color(*_C_ACCENT, alpha=0.22))
    c.setLineWidth(0.8)
    c.line(0, bw * 2, W, H - bw * 2)       # top edge of band
    c.line(bw * 2, 0, W - bw * 0.5, H)     # bottom edge of band
    # Dot row along right edge
    c.setFillColor(Color(1, 1, 1, alpha=0.08))
    for row in range(2, 9):
        c.circle(W - 22, row * H / 10, 2.0, fill=1, stroke=0)
    # Small teal orb top-left corner (off-canvas centre)
    c.setFillColor(Color(*_C_TEAL, alpha=0.18))
    c.circle(-40, H + 40, W * 0.32, fill=1, stroke=0)
    c.setLineWidth(1)


_BG_THEMES = [_bg1_dual_glow, _bg2_left_panel, _bg3_concentric,
              _bg4_horizontal_bands, _bg5_diagonal]


def _draw_slide(c, slide, stype, num, total, channel, yt_url, tags, size, theme_idx=0):
    from reportlab.lib.colors import Color
    W, H = size
    pad  = _PAD

    # Draw chosen background theme — isolated in its own graphics state
    # so alpha values set inside cannot leak into text rendering.
    c.saveState()
    _BG_THEMES[theme_idx % 5](c, W, H)
    c.restoreState()

    # Accent top bar
    c.setFillColorRGB(*_C_ACCENT)
    c.rect(0, H - 5, W, 5, fill=1, stroke=0)

    if stype == "title":
        _draw_title_slide(c, slide, channel, tags, W, H, pad)
    elif stype == "cta":
        _draw_cta_slide(c, slide, yt_url, channel, W, H, pad)
    else:
        _draw_insight_slide(c, slide, W, H, pad)

    # Slide number indicator (bottom-right)
    c.setFillColorRGB(*_C_MUT)
    c.setFont("Helvetica", 11)
    c.drawRightString(W - pad, pad * 0.6, f"{num} / {total}")


def _wrap_text(c, text, x, y, max_width, font, size, color, line_height=None):
    """Draw wrapped text starting at (x, y), returning y after last line."""
    from reportlab.lib.utils import simpleSplit
    if line_height is None:
        line_height = size * 1.45
    c.setFont(font, size)
    c.setFillColorRGB(*color)
    lines = simpleSplit(text, font, size, max_width)
    for line in lines:
        c.drawString(x, y, line)
        y -= line_height
    return y


def _draw_title_slide(c, slide, channel, tags, W, H, pad):
    heading    = slide.get("heading", "")
    subheading = slide.get("subheading", "")

    # Large heading — centred vertically
    from reportlab.lib.utils import simpleSplit
    usable = W - pad * 2

    # Draw heading
    h_size = 42
    c.setFont("Helvetica-Bold", h_size)
    lines  = simpleSplit(heading, "Helvetica-Bold", h_size, usable)
    total_h = len(lines) * h_size * 1.3
    y_start = H / 2 + total_h / 2 + 20

    c.setFillColorRGB(*_C_WHITE)
    y = y_start
    for line in lines:
        c.drawCentredString(W / 2, y, line)
        y -= h_size * 1.3

    # Subheading
    y -= 10
    c.setFont("Helvetica", 22)
    c.setFillColorRGB(*_C_MUT)
    sub_lines = simpleSplit(subheading, "Helvetica", 22, usable)
    for line in sub_lines:
        c.drawCentredString(W / 2, y, line)
        y -= 22 * 1.4

    # Channel credit (bottom-left)
    c.setFont("Helvetica", 13)
    c.setFillColorRGB(*_C_MUT)
    c.drawString(pad, pad * 0.6 + 14, channel)

    # Tag chips (bottom centre area)
    if tags:
        _draw_tags(c, tags[:4], W / 2, pad + 28, W)


def _draw_insight_slide(c, slide, W, H, pad):
    heading = slide.get("heading", "")
    body    = slide.get("body", "")
    usable  = W - pad * 2

    # Accent line left edge
    c.setFillColorRGB(*_C_ACCENT)
    c.rect(pad - 10, H * 0.3, 4, H * 0.4, fill=1, stroke=0)

    # Heading
    y = H * 0.72
    _wrap_text(c, heading, pad, y, usable, "Helvetica-Bold", 36, _C_WHITE, line_height=36 * 1.3)

    # Body
    y = H * 0.54
    _wrap_text(c, body, pad, y, usable, "Helvetica", 22, _C_MUT, line_height=22 * 1.55)


def _draw_cta_slide(c, slide, yt_url, channel, W, H, pad):
    heading = slide.get("heading", "Watch the full video")
    body    = slide.get("body", "")
    usable  = W - pad * 2

    # Accent background strip
    c.setFillColorRGB(*_C_ACCENT)
    c.rect(0, H * 0.45, W, H * 0.12, fill=1, stroke=0)

    # Heading on accent strip
    c.setFont("Helvetica-Bold", 30)
    c.setFillColorRGB(*_C_WHITE)
    c.drawCentredString(W / 2, H * 0.48 + 14, heading)

    # Body below
    y = H * 0.40
    _wrap_text(c, body, pad, y, usable, "Helvetica", 20, _C_MUT)

    # Branding
    c.setFont("Helvetica", 12)
    c.setFillColorRGB(*_C_MUT)
    c.drawCentredString(W / 2, pad * 0.6 + 14, f"Source: {channel} on YouTube")


def _draw_tags(c, tags, cx, y, W):
    """Draw small tag chips centred around cx at height y."""
    c.setFont("Helvetica", 11)
    chip_h   = 18
    chip_pad = 8
    gap      = 6
    widths   = [c.stringWidth(t, "Helvetica", 11) + chip_pad * 2 for t in tags]
    total_w  = sum(widths) + gap * (len(tags) - 1)
    x        = cx - total_w / 2

    for tag, w in zip(tags, widths):
        c.setFillColorRGB(0.165, 0.102, 0.227)   # dark purple chip
        c.roundRect(x, y, w, chip_h, 4, fill=1, stroke=0)
        c.setFillColorRGB(0.753, 0.518, 0.988)   # #c084fc
        c.drawString(x + chip_pad, y + 4, tag)
        x += w + gap


# ─────────────────────────────────────────────
# Output persistence
# ─────────────────────────────────────────────

def save_outputs(video_id: str, detail: str, post_text: str, pdf_bytes: bytes) -> dict:
    """Save post text and PDF to outputs/linkedin/YYYY-MM-DD/. Returns paths dict."""
    today   = datetime.date.today().isoformat()
    out_dir = os.path.join(os.path.dirname(__file__), "outputs", "linkedin", today)
    os.makedirs(out_dir, exist_ok=True)

    stem    = f"{video_id}_{detail}"
    txt_path = os.path.join(out_dir, f"{stem}_post.txt")
    pdf_path = os.path.join(out_dir, f"{stem}_carousel.pdf")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(post_text)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    return {"txt": txt_path, "pdf": pdf_path}
