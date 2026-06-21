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


def _creativity_instruction(temperature: float) -> str:
    """Translate a 0–1 creativity slider value into a writing instruction."""
    t = float(temperature)
    if t <= 0.2:
        return ("Writing style: strictly data-driven. Use only facts, numbers, and specifics "
                "from the source material. No creative language or embellishment.")
    elif t <= 0.4:
        return ("Writing style: grounded and factual. Stay close to the source material "
                "but write in clean, clear prose.")
    elif t <= 0.6:
        return ("Writing style: balanced. Accurate to the source but written engagingly "
                "for a professional audience.")
    elif t <= 0.8:
        return ("Writing style: creative and energetic. Use vivid language and a strong "
                "voice while remaining grounded in the facts.")
    else:
        return ("Writing style: highly creative. Be bold — use compelling language, "
                "strong hooks, and an original voice. Push beyond the literal.")


def linkedin_post_prompt(
    title: str,
    channel: str,
    summary: str,
    tags: list,
    url: str = "",
    post_style: str = "tips",
    customization: str = "",
    temperature: float = 0.7,
) -> str:
    """Return the user message for generating a LinkedIn text post."""
    tag_str      = ", ".join(tags) if tags else "none"
    style_instr  = _POST_STYLE_INSTRUCTIONS.get(post_style, _POST_STYLE_INSTRUCTIONS["tips"])
    creativity   = _creativity_instruction(temperature)
    custom_block = (
        f"\nAdditional instructions from the author:\n{customization.strip()}\n"
        if customization and customization.strip() else ""
    )
    return (
        f"Create a LinkedIn post based on the following YouTube video summary.\n\n"
        f"Video title: {title}\n"
        f"Channel: {channel}\n"
        f"Topic tags: {tag_str}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Post style — {post_style.replace('_', ' ').title()}:\n{style_instr}\n\n"
        f"{creativity}\n"
        f"{custom_block}\n"
        f"General requirements:\n"
        f"- Hook in the first line (no 'Just watched' or 'Great video' openers)\n"
        f"- End with a concise call-to-action (no URLs, no links, no video references)\n"
        f"- 5-7 hashtags on the last line\n"
        f"- Total length: 150-250 words\n"
        f"- Professional tone, no hype language\n"
        f"- Do NOT include any URLs, hyperlinks, or website addresses anywhere in the post"
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
        f"Create a LinkedIn carousel (5-8 slides) based on the following content.\n\n"
        f"Title: {title}\n"
        f"Source: {channel}\n"
        f"Tags: {tag_str}\n"
        f"Post style: {post_style.replace('_', ' ').title()}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Style-specific slide guidance:\n{style_instr}\n\n"
        f"Slide structure:\n"
        f"- Slide 1: type=title. heading=attention-grabbing title (not the video title verbatim). "
        f"subheading=one-line value prop that reflects the {post_style.replace('_', ' ')} angle.\n"
        f"- Slides 2 to N-1: type=insight. heading=short label (max 8 words). body=1-2 sentences "
        f"strictly based on the summary — no invented details.\n"
        f"- Last slide: type=cta. heading=a short action-oriented phrase (e.g. 'Start Today', 'Try This Now', "
        f"'Your Next Step'). body=one sentence encouraging the reader to apply or share the insight. "
        f"No references to videos, watching, or any media.\n\n"
        f"Return JSON only. Example structure:\n"
        f'{{"slides": [{{"type": "title", "heading": "...", "subheading": "..."}}, '
        f'{{"type": "insight", "heading": "...", "body": "..."}}, '
        f'{{"type": "cta", "heading": "Start Today", "body": "..."}}]}}'
    )


# ─────────────────────────────────────────────
# Mixer prompts
# ─────────────────────────────────────────────

MIXER_SCORE_SYSTEM = (
    "You are an expert in supply chain management and enterprise technology. "
    "Your task is to evaluate whether a given technology is a strong solution fit "
    "for a given supply chain problem. Be analytical and specific. "
    "Return ONLY valid JSON — no markdown fences, no explanation outside the JSON."
)


def mixer_score_prompt(sc_title: str, sc_summary: str, tech_title: str, tech_summary: str) -> str:
    """Return the user message for scoring technology fit against a supply chain problem."""
    return (
        f"Evaluate how well the following technology addresses the supply chain problem described below.\n\n"
        f"SUPPLY CHAIN PROBLEM\n"
        f"Title: {sc_title}\n"
        f"Summary:\n{sc_summary}\n\n"
        f"TECHNOLOGY SOLUTION\n"
        f"Title: {tech_title}\n"
        f"Summary:\n{tech_summary}\n\n"
        f"Score the fit on a scale of 1 to 10 where:\n"
        f"1-3 = poor fit (technology does not address the core problem)\n"
        f"4-6 = moderate fit (partial overlap, some relevant capabilities)\n"
        f"7-8 = strong fit (technology directly addresses key aspects of the problem)\n"
        f"9-10 = exceptional fit (technology is purpose-built or highly optimised for this exact problem)\n\n"
        f"Return JSON with exactly these keys:\n"
        f'{{"score": <integer 1-10>, "headline": "<one sentence verdict>", '
        f'"reasoning": "<3-5 sentences explaining the score — be specific about which aspects of the '
        f'technology address which aspects of the supply chain problem, and where gaps remain>"}}'
    )


def mixer_post_prompt(
    sc_title: str, sc_summary: str,
    tech_title: str, tech_summary: str,
    score: int, reasoning: str,
    customization: str = "",
) -> str:
    """Return the user message for generating a Mixer LinkedIn post."""
    custom_block = (
        f"\nAdditional instructions from the author:\n{customization.strip()}\n"
        if customization and customization.strip() else ""
    )
    return (
        f"Write a LinkedIn post that bridges a supply chain problem with a technology solution.\n\n"
        f"SUPPLY CHAIN PROBLEM\n"
        f"Title: {sc_title}\n"
        f"Summary:\n{sc_summary}\n\n"
        f"TECHNOLOGY SOLUTION\n"
        f"Title: {tech_title}\n"
        f"Summary:\n{tech_summary}\n\n"
        f"Fit Score: {score}/10\n"
        f"Analyst Reasoning:\n{reasoning}\n\n"
        f"Post style — Problem → Solution:\n"
        f"Open by naming the specific supply chain pain point. Then reveal how the technology "
        f"addresses it. Use the fit score reasoning as the analytical backbone — be specific, "
        f"not generic. Structure: Problem → Why it matters → How the technology solves it → "
        f"Key insight or caveat → Call to action.\n\n"
        f"{custom_block}"
        f"General requirements:\n"
        f"- Hook in the first line (no 'Just watched' or 'Great video' openers)\n"
        f"- Professional B2B tone, no hype language\n"
        f"- End with 5-7 relevant hashtags on their own line\n"
        f"- Total length: 150-250 words\n"
        f"- Do NOT include any URLs, hyperlinks, or website addresses anywhere in the post"
    )


MIXER_SLIDES_SYSTEM = (
    "You are a LinkedIn carousel designer. Return ONLY valid JSON — no markdown fences, "
    "no explanation. The JSON must have a top-level key 'slides' containing a list of "
    "slide objects. Each slide has: type (title|insight|cta), heading, and body (except "
    "title slides which use subheading instead of body). Keep text short — slides are "
    "read at a glance. Heading: max 8 words. Body/subheading: max 25 words."
)


def mixer_slides_prompt(
    sc_title: str, sc_summary: str,
    tech_title: str, tech_summary: str,
    score: int, reasoning: str,
) -> str:
    """Return the user message for generating Mixer carousel slide data as JSON."""
    return (
        f"Create a LinkedIn carousel (6-8 slides) that tells the story of a supply chain "
        f"problem being solved by a specific technology.\n\n"
        f"SUPPLY CHAIN PROBLEM\n"
        f"Title: {sc_title}\n"
        f"Summary:\n{sc_summary}\n\n"
        f"TECHNOLOGY SOLUTION\n"
        f"Title: {tech_title}\n"
        f"Summary:\n{tech_summary}\n\n"
        f"Fit Score: {score}/10\n"
        f"Analyst Reasoning:\n{reasoning}\n\n"
        f"Slide structure:\n"
        f"- Slide 1: type=title. heading=a punchy headline that names the problem-solution pairing. "
        f"subheading=one-line value proposition.\n"
        f"- Slide 2: type=insight. heading='The Problem'. body=core supply chain pain point in one sentence.\n"
        f"- Slide 3: type=insight. heading='Why It Persists'. body=root cause or structural reason.\n"
        f"- Slides 4-N-1: type=insight. Each slide = one key way the technology addresses the problem. "
        f"Use specific details from the summaries.\n"
        f"- Second-to-last slide: type=insight. heading='The Fit Score: {score}/10'. "
        f"body=one-sentence verdict from the analyst reasoning.\n"
        f"- Last slide: type=cta. heading='Your Move'. "
        f"body=one sentence encouraging the reader to evaluate this pairing for their own operations.\n\n"
        f"Return JSON only. Example structure:\n"
        f'{{"slides": [{{"type": "title", "heading": "...", "subheading": "..."}}, '
        f'{{"type": "insight", "heading": "...", "body": "..."}}, '
        f'{{"type": "cta", "heading": "Your Move", "body": "..."}}]}}'
    )
