"""Centralised prompt templates for all LLM calls in the app."""

# ─────────────────────────────────────────────
# LinkedIn generation prompts
# ─────────────────────────────────────────────

LINKEDIN_POST_SYSTEM = (
    "You are a professional LinkedIn content strategist. Write crisp, insight-led posts "
    "for a B2B technology audience. No fluff, no emoji spam, no 'I'm excited to share' "
    "openers. Lead with the sharpest insight. Use short paragraphs and line breaks for "
    "readability. End with 5-7 relevant hashtags on their own line."
)

# Style-specific writing instructions injected into the post prompt.
_POST_STYLE_INSTRUCTIONS = {
    "narrative": (
        "Write as a short story arc: set the scene, build tension around the core idea, "
        "and land on a clear resolution or lesson. First-person perspective is fine. "
        "Draw the reader in emotionally before delivering the insight."
    ),
    "problem_solution": (
        "Open by naming a sharp, specific pain point the target audience faces. "
        "Then pivot to what the video reveals as the solution or approach. "
        "Structure: Problem → Why it matters → Solution → Call to action."
    ),
    "tips": (
        "Present the content as a numbered or bulleted list of concrete, actionable tips. "
        "Each tip should be one punchy line followed by a brief elaboration. "
        "Lead with the most surprising or counterintuitive tip."
    ),
    "scenario": (
        "Open with a relatable real-world scenario or 'imagine you are…' situation. "
        "Use it to make the core insight tangible and personal. "
        "End by connecting the scenario back to a broader professional takeaway."
    ),
    "contrarian": (
        "Challenge a widely-held assumption or popular belief related to the topic. "
        "State the conventional view, then flip it using evidence from the video. "
        "Be bold but grounded — back the contrarian take with specifics, not just opinion."
    ),
    "case_study": (
        "Frame the insights as a real-world use case or implementation example. "
        "Ground every point in a concrete situation: who faced the problem, what they did, "
        "and what measurable outcome or lesson resulted. "
        "Use specific details — team size, tools, numbers, timelines — wherever the source material provides them. "
        "End with a transferable takeaway the reader can apply in their own context."
    ),
}


def linkedin_post_prompt(
    title: str,
    channel: str,
    summary: str,
    tags: list,
    url: str,
    post_style: str = "tips",
    customization: str = "",
) -> str:
    """Return the user message for generating a LinkedIn text post."""
    tag_str   = ", ".join(tags) if tags else "none"
    style_instr = _POST_STYLE_INSTRUCTIONS.get(post_style, _POST_STYLE_INSTRUCTIONS["tips"])
    custom_block = (
        f"\nAdditional instructions from the author:\n{customization.strip()}\n"
        if customization and customization.strip() else ""
    )
    return (
        f"Create a LinkedIn post based on the following YouTube video summary.\n\n"
        f"Video title: {title}\n"
        f"Channel: {channel}\n"
        f"YouTube URL: {url}\n"
        f"Topic tags: {tag_str}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Post style — {post_style.replace('_', ' ').title()}:\n{style_instr}\n"
        f"{custom_block}\n"
        f"General requirements:\n"
        f"- Hook in the first line (no 'Just watched' or 'Great video' openers)\n"
        f"- A closing call-to-action that references the video\n"
        f"- 5-7 hashtags on the last line\n"
        f"- Total length: 150-250 words\n"
        f"- Professional tone, no hype language"
    )


LINKEDIN_SLIDES_SYSTEM = (
    "You are a LinkedIn carousel designer. Return ONLY valid JSON — no markdown fences, "
    "no explanation. The JSON must have a top-level key 'slides' containing a list of "
    "slide objects. Each slide has: type (title|insight|cta), heading, and body (except "
    "title slides which use subheading instead of body). Keep text short — slides are "
    "read at a glance. Heading: max 8 words. Body/subheading: max 25 words."
)


# Per-style guidance for carousel slides — mirrors the post style intent.
_SLIDE_STYLE_INSTRUCTIONS = {
    "tips": (
        "Each insight slide should present one concrete, actionable tip drawn directly from the summary. "
        "Heading = the tip label. Body = one sentence of elaboration with a specific detail or number."
    ),
    "narrative": (
        "Structure the slides as a story arc: slide 2 sets the scene/context, middle slides build the "
        "tension or journey, the penultimate slide lands the resolution. Each body is one vivid sentence."
    ),
    "problem_solution": (
        "Slide 2 names the core problem. Slide 3 explains why it matters. "
        "Middle slides each present one element of the solution. Final insight slide states the outcome."
    ),
    "scenario": (
        "Open with a 'Imagine you are…' scenario slide. Subsequent slides walk through what happens "
        "step by step in that scenario, grounding each insight in the concrete situation."
    ),
    "contrarian": (
        "Slide 2 states the conventional wisdom. Slide 3 flips it. "
        "Middle slides each provide one piece of evidence or reasoning that supports the contrarian view."
    ),
    "case_study": (
        "Structure as a mini case study: slide 2 = the situation/context, slide 3 = the challenge faced, "
        "middle slides = what was done and key findings with specific details (numbers, tools, outcomes), "
        "penultimate slide = result, last insight slide = transferable lesson."
    ),
}


def linkedin_slides_prompt(title: str, channel: str, summary: str, tags: list, post_style: str = "tips") -> str:
    """Return the user message for generating carousel slide data as JSON."""
    tag_str     = ", ".join(tags) if tags else ""
    style_instr = _SLIDE_STYLE_INSTRUCTIONS.get(post_style, _SLIDE_STYLE_INSTRUCTIONS["tips"])
    return (
        f"Create a LinkedIn carousel (5-8 slides) for this YouTube video.\n\n"
        f"Video title: {title}\n"
        f"Channel: {channel}\n"
        f"Tags: {tag_str}\n"
        f"Post style: {post_style.replace('_', ' ').title()}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Style-specific slide guidance:\n{style_instr}\n\n"
        f"Slide structure:\n"
        f"- Slide 1: type=title. heading=attention-grabbing title (not the video title verbatim). "
        f"subheading=one-line value prop that reflects the {post_style.replace('_', ' ')} angle.\n"
        f"- Slides 2 to N-1: type=insight. heading=short label (max 8 words). body=1-2 sentences "
        f"strictly based on the summary — no invented details.\n"
        f"- Last slide: type=cta. heading='Watch the full video'. body=one sentence on what they will learn.\n\n"
        f"Return JSON only. Example structure:\n"
        f'{{"slides": [{{"type": "title", "heading": "...", "subheading": "..."}}, '
        f'{{"type": "insight", "heading": "...", "body": "..."}}, '
        f'{{"type": "cta", "heading": "Watch the full video", "body": "..."}}]}}'
    )
