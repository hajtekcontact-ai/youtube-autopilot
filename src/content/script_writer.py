"""Script writer module for YouTube AI Autopilot.

Generates production-ready video scripts in natural American English using
Google Gemini API when available, with a procedural template fallback engine.
Supports Short (30-58s) and Long-form (5-10m) scripts across four storytelling
styles: narrative, listicle, explainer, and story.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Project imports with graceful fallback
try:
    from src.config import current_config, Config
except ImportError:
    current_config = None
    Config = None

try:
    from src.utils.helpers import safe_json_loads, truncate_text
except ImportError:
    def safe_json_loads(text: Any, default: Any = None) -> Any:
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            return default

    def truncate_text(text: str, max_len: int, ellipsis: str = "...") -> str:
        if not text or len(text) <= max_len:
            return text or ""
        return text[:max_len - len(ellipsis)] + ellipsis

try:
    from src.content.idea_generator import ContentIdea
except ImportError:
    ContentIdea = Any

# Optional Gemini API
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    genai = None
    HAS_GENAI = False


class ScriptWriter:
    """Production-grade YouTube script generation engine.

    Produces high-retention video voiceover scripts with structured sections:
    hook (first 3 seconds), intro, body sections, CTA, and outro.
    Enforces word counts calibrated for speech rates (135-150 words per minute).
    """

    SUPPORTED_STYLES: List[str] = ["narrative", "listicle", "explainer", "story"]
    SUPPORTED_TYPES: List[str] = ["short", "long"]

    # Average conversational American English speech rate: ~140 words per minute
    WORDS_PER_MINUTE = 140
    WORDS_PER_SECOND = WORDS_PER_MINUTE / 60.0  # ~2.33 words/sec

    # Target constraints
    SHORT_MIN_WORDS = 75
    SHORT_MAX_WORDS = 145
    SHORT_TARGET_DURATION_SEC = 45.0  # 30-58s

    LONG_MIN_WORDS = 750
    LONG_MAX_WORDS = 1500
    LONG_TARGET_DURATION_SEC = 420.0  # 5-10m (300-600s)

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        rpm_limit: int = 15,
    ) -> None:
        """Initialize the ScriptWriter.

        Args:
            api_key: Optional Gemini API key. Defaults to current_config.GEMINI_API_KEY.
            model_name: Optional Gemini model name. Defaults to current_config.GEMINI_MODEL.
            rpm_limit: Maximum API requests per minute for rate limiting.
        """
        self.api_key = api_key or (current_config.GEMINI_API_KEY if current_config else "")
        self.model_name = model_name or (current_config.GEMINI_MODEL if current_config else "gemini-1.5-flash")
        self.min_interval = 60.0 / max(1, rpm_limit)
        self.last_call_time = 0.0
        self._lock = threading.Lock()

        self._gemini_initialized = False
        self._init_gemini()

    def _init_gemini(self) -> None:
        """Initialize Google Gemini client if available."""
        if HAS_GENAI and self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self._gemini_initialized = True
                logger.info("ScriptWriter: Gemini API initialized successfully.")
            except Exception as exc:
                logger.warning("ScriptWriter: Failed to configure Gemini: %s. Using template engine.", exc)
                self._gemini_initialized = False
        else:
            self._gemini_initialized = False

    def _rate_limit(self) -> None:
        """Enforce rate limits on outgoing API requests."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call_time = time.time()

    @staticmethod
    def count_words(text: str) -> int:
        """Count the number of spoken words in a text string.

        Args:
            text: Narration text.

        Returns:
            int: Total word count.
        """
        if not text:
            return 0
        clean = re.sub(r"\[.*?\]|\(.*?\)", "", text)  # remove cues
        words = re.findall(r"\b[a-zA-Z0-9'-]+\b", clean)
        return len(words)

    @classmethod
    def estimate_duration_seconds(cls, word_count: int, wpm: int = WORDS_PER_MINUTE) -> float:
        """Calculate estimated speaking duration in seconds.

        Args:
            word_count: Number of words.
            wpm: Speaking rate in words per minute.

        Returns:
            float: Duration in seconds rounded to 1 decimal place.
        """
        if word_count <= 0:
            return 0.0
        duration = (word_count / float(wpm)) * 60.0
        return round(duration, 1)

    def _build_gemini_prompt(
        self,
        topic: str,
        title: str,
        description: str,
        video_type: str,
        style: str,
        niche: str,
    ) -> str:
        """Construct comprehensive prompt with storytelling, fact-checking, and anti-plagiarism directives.

        Args:
            topic: Core topic.
            title: Headline.
            description: Angle summary.
            video_type: 'short' or 'long'.
            style: 'narrative', 'listicle', 'explainer', or 'story'.
            niche: Content category.

        Returns:
            str: Full instruction prompt.
        """
        if video_type == "short":
            word_guidance = (
                f"STRICT LENGTH REQUIREMENT: The total voiceover script MUST be between 85 and 130 words "
                f"(equivalent to 35 to 55 seconds of natural speech at 140 WPM). DO NOT exceed 140 words under any circumstance."
            )
            section_guidance = (
                "For a YouTube Short, keep sections rapid and punchy:\n"
                "1. Hook (First 3 seconds, 8-15 words): Immediate mystery or counter-intuitive premise. NO GREETINGS.\n"
                "2. Body (2 to 3 micro-sections): Fast progression, vivid explanation, zero filler.\n"
                "3. CTA & Outro (10-18 words): Quick subscribe request tied to the insight."
            )
        else:
            word_guidance = (
                f"STRICT LENGTH REQUIREMENT: The total voiceover script MUST be between 850 and 1350 words "
                f"(equivalent to 6 to 9 minutes of speech at 140 WPM). Structure deeply with detailed evidence and explanations."
            )
            section_guidance = (
                "For a Long-form video, craft a compelling multi-act documentary arc:\n"
                "1. Hook (First 3-5 seconds): High-stakes question or shocking historical/scientific anomaly.\n"
                "2. Intro: Establish the context, the mystery, and why it matters to humanity.\n"
                "3. Body (4 to 6 substantial sections): Deep dive with analogies, historical/scientific milestones, data points, and progressive revelations.\n"
                "4. CTA: Thoughtful request for subscription, comments, and community debate.\n"
                "5. Outro: Memorable philosophical takeaway or thought experiment that lingers."
            )

        prompt = f"""You are a world-class documentary scriptwriter for YouTube, writing in natural, conversational American English.

Topic: "{topic}"
Working Title: "{title}"
Description/Angle: "{description}"
Niche: "{niche}"
Format: "{video_type.upper()}" (Vertical Short or Horizontal Long-form)
Style: "{style.upper()}"

{word_guidance}

CRITICAL EDITORIAL RULES:
1. VOICE & TONE: Conversational, authoritative, clear, and engaging American English. Use vivid sensory verbs and active voice. Avoid academic jargon unless followed immediately by an intuitive everyday metaphor.
2. NO CLICKBAIT: Every hook must be 100% delivered upon truthfully. Do not make false promises or exaggerated claims.
3. ABSOLUTE ZERO FLUFF: NEVER begin with "Hey guys, welcome back to the channel" or "In this video, I will show you". Start directly in media res with the hook.
4. STRICT FACT-CHECKING DIRECTIVE: All factual claims, numbers, dates, and scientific laws must be strictly accurate according to established scientific consensus and historical records. If a theory is contested, explicitly frame it as a leading hypothesis rather than a proven certainty.
5. ANTI-PLAGIARISM DIRECTIVE: Write 100% original narration. Do not replicate phrases or transcripts from existing viral videos or encyclopedias. Synthesize the knowledge into a distinctive, proprietary storytelling voice.
6. STORYTELLING MECHANICS ({style.upper()}):
   - Narrative: Chronological causality, rising tension, turning point, and philosophical resolution.
   - Listicle: High-impact numbered insights with escalating fascination and surprising depth.
   - Explainer: First-principles decomposition, relatable mechanical analogies, and practical implications.
   - Story: Human/event-centered narrative with a protagonist, obstacle, breakthrough, and historical impact.

{section_guidance}

OUTPUT FORMAT:
You MUST respond with a single valid JSON object with the following schema:
{{
  "title": "Engaging final YouTube title (max 70 chars)",
  "hook": "Exact opening voiceover words for the first 3-5 seconds",
  "intro": "Context framing that bridges the hook into the core story",
  "script_sections": [
    {{
      "section_id": 1,
      "title": "Descriptive Section Heading",
      "content": "Full continuous narration text for this section...",
      "visual_cue": "Brief description of background visuals/b-roll to show"
    }}
  ],
  "cta": "Engaging call to action narration",
  "outro": "Final closing thought voiceover",
  "description": "Engaging 2-3 paragraph YouTube description with chapter timestamps and hashtags",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"]
}}

Return ONLY the raw JSON object. Do not include markdown code blocks or conversational commentary."""

        return prompt

    def _write_with_gemini(
        self,
        topic: str,
        title: str,
        description: str,
        video_type: str,
        style: str,
        niche: str,
    ) -> Optional[Dict[str, Any]]:
        """Call Gemini API to generate the script.

        Args:
            topic: Video topic.
            title: Video title.
            description: Angle summary.
            video_type: 'short' or 'long'.
            style: Script style.
            niche: Content category.

        Returns:
            Optional[Dict[str, Any]]: Parsed script data dictionary, or None on failure.
        """
        if not self._gemini_initialized or genai is None:
            return None

        self._rate_limit()

        prompt = self._build_gemini_prompt(
            topic=topic,
            title=title,
            description=description,
            video_type=video_type,
            style=style,
            niche=niche,
        )

        try:
            max_tokens = 4096 if video_type == "long" else 1500
            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config={"temperature": 0.65, "max_output_tokens": max_tokens},
            )
            response = model.generate_content(prompt)
            raw_text = response.text.strip()

            # Clean markdown code block if present
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text).strip()

            data = safe_json_loads(raw_text)
            if not isinstance(data, dict):
                logger.warning("Gemini script output was not a valid dictionary.")
                return None

            # Verify required fields
            required = ["title", "hook", "script_sections", "cta"]
            if not all(k in data for k in required) or not isinstance(data.get("script_sections"), list):
                logger.warning("Gemini script output missing required keys: %s", list(data.keys()))
                return None

            # Assemble full voiceover script text
            sections = data.get("script_sections", [])
            hook_text = data.get("hook", "").strip()
            intro_text = data.get("intro", "").strip()
            cta_text = data.get("cta", "").strip()
            outro_text = data.get("outro", "").strip()

            full_script = self.format_full_script(
                sections=sections,
                hook=hook_text,
                intro=intro_text,
                cta=cta_text,
                outro=outro_text,
            )

            word_count = self.count_words(full_script)
            est_duration = self.estimate_duration_seconds(word_count)

            # Ensure tags list
            tags = data.get("tags", [])
            if not isinstance(tags, list):
                tags = [str(tags)]
            if video_type == "short" and "#Shorts" not in tags and "Shorts" not in tags:
                tags.append("Shorts")

            # Ensure description
            desc = data.get("description", "")
            if not desc:
                desc = f"{data.get('title', title)}\n\n{description}\n\nSubscribe for daily deep dives into science and history."
            if video_type == "short" and "#Shorts" not in desc:
                desc += "\n\n#Shorts #Science #Education"

            result = {
                "title": data.get("title", title),
                "hook": hook_text,
                "intro": intro_text,
                "script_sections": sections,
                "cta": cta_text,
                "outro": outro_text,
                "full_script": full_script,
                "description": desc,
                "tags": tags,
                "word_count": word_count,
                "estimated_duration_seconds": est_duration,
                "video_type": video_type,
                "style": style,
                "source": "gemini",
            }
            return result

        except Exception as exc:
            logger.warning("Error generating script with Gemini: %s. Falling back to template engine.", exc)
            return None

    def _generate_template_script(
        self,
        topic: str,
        title: str,
        description: str,
        video_type: str,
        style: str,
        niche: str,
    ) -> Dict[str, Any]:
        """Procedural script generation fallback when Gemini API is unavailable.

        Constructs natural, highly engaging American English scripts for all 4 styles
        and both video formats (Short and Long).

        Args:
            topic: Video topic.
            title: Headline.
            description: Angle summary.
            video_type: 'short' or 'long'.
            style: 'narrative', 'listicle', 'explainer', or 'story'.
            niche: Content category.

        Returns:
            Dict[str, Any]: Complete structured script.
        """
        style = style.lower() if style.lower() in self.SUPPORTED_STYLES else "explainer"
        video_type = video_type.lower() if video_type.lower() in self.SUPPORTED_TYPES else "short"

        if video_type == "short":
            return self._build_template_short(topic, title, description, style, niche)
        else:
            return self._build_template_long(topic, title, description, style, niche)

    def _build_template_short(
        self,
        topic: str,
        title: str,
        description: str,
        style: str,
        niche: str,
    ) -> Dict[str, Any]:
        """Generate high-retention 35-50 second YouTube Short script (~85-125 words)."""
        clean_topic = topic.strip()

        if style == "listicle":
            hook = f"Here are three things you definitely didn't know about {clean_topic}."
            intro = "And number three will completely change the way you see it."
            sections = [
                {
                    "section_id": 1,
                    "title": "First Discovery",
                    "content": f"First: {description} Most people think it's simple, but the underlying mechanics are mind-bending.",
                    "visual_cue": f"Dynamic kinetic typography highlighting key terms of {clean_topic}",
                },
                {
                    "section_id": 2,
                    "title": "The Strange Reality",
                    "content": "Second: recent observations revealed that what we thought was a rare anomaly is actually happening all around us.",
                    "visual_cue": "Macro camera zoom with visual data overlay showing dynamic statistics",
                },
                {
                    "section_id": 3,
                    "title": "The Wildest Part",
                    "content": "And third: scientists estimate that this phenomenon controls fundamental laws we interact with every single day.",
                    "visual_cue": "Fast-paced visual comparison showing real-world scale and impact",
                },
            ]
            cta = "Subscribe if you learned something new today!"
            outro = f"Which fact surprised you the most about {clean_topic}?"

        elif style == "narrative":
            hook = f"What if everything you were told about {clean_topic} was fundamentally backwards?"
            intro = f"For decades, researchers hit a brick wall trying to solve this mystery."
            sections = [
                {
                    "section_id": 1,
                    "title": "The Breakthrough",
                    "content": f"{description} When researchers finally looked closer, the data revealed something entirely unexpected.",
                    "visual_cue": "Dramatic slow pan over glowing archival diagrams and scientific models",
                },
                {
                    "section_id": 2,
                    "title": "The Revelation",
                    "content": "Instead of breaking down, the system organized itself in a way classical physics never predicted.",
                    "visual_cue": "High-contrast visual animation demonstrating the turning point",
                },
            ]
            cta = "Hit subscribe to explore more incredible mysteries with us."
            outro = f"The story of {clean_topic} is only just beginning."

        elif style == "story":
            hook = f"In the middle of an ordinary experiment on {clean_topic}, something went completely off the charts."
            intro = "The team thought their instruments were malfunctioning."
            sections = [
                {
                    "section_id": 1,
                    "title": "The Anomaly",
                    "content": f"{description} But after repeating the test twelve times, the numbers were unmistakably real.",
                    "visual_cue": "Cinematic visual recreation of a high-tech laboratory readout spiking",
                },
                {
                    "section_id": 2,
                    "title": "The Discovery",
                    "content": "What started as an accidental glitch ended up rewriting our entire understanding of reality.",
                    "visual_cue": "Seamless transition to a visual breakdown of the modern discovery",
                },
            ]
            cta = "Drop a comment with your theory and subscribe for daily deep dives."
            outro = "Sometimes the greatest breakthroughs happen by pure accident."

        else:  # explainer (default)
            hook = f"Why does {clean_topic} behave in a way that defies common sense?"
            intro = "Here is the simple physics behind it in under 45 seconds."
            sections = [
                {
                    "section_id": 1,
                    "title": "The Core Mechanism",
                    "content": f"{description} Think of it like this: every moving part is calibrated to minimize resistance while maximizing output.",
                    "visual_cue": "Clean animated vector diagram illustrating the mechanics step by step",
                },
                {
                    "section_id": 2,
                    "title": "The Big Picture",
                    "content": "When you zoom out, this single principle explains why the entire process functions effortlessly.",
                    "visual_cue": "Macro camera pull-back showing full system harmony",
                },
            ]
            cta = "If this made sense, subscribe for daily science explainers!"
            outro = f"Now you understand {clean_topic} better than 99% of people."

        full_script = self.format_full_script(sections, hook, intro, cta, outro)
        word_count = self.count_words(full_script)
        est_duration = self.estimate_duration_seconds(word_count)

        desc = (
            f"{title}\n\n"
            f"{description}\n\n"
            f"Explore the fascinating science and history behind {clean_topic}. "
            f"Subscribe for daily educational shorts!\n\n"
            f"#Shorts #{niche.capitalize()} #Science #Education"
        )

        tags = [
            clean_topic.lower(),
            niche.lower(),
            "science explained",
            "educational shorts",
            "did you know",
            "mind blowing facts",
            "Shorts",
        ]

        return {
            "title": title,
            "hook": hook,
            "intro": intro,
            "script_sections": sections,
            "cta": cta,
            "outro": outro,
            "full_script": full_script,
            "description": desc,
            "tags": tags,
            "word_count": word_count,
            "estimated_duration_seconds": est_duration,
            "video_type": "short",
            "style": style,
            "source": "template",
        }

    def _build_template_long(
        self,
        topic: str,
        title: str,
        description: str,
        style: str,
        niche: str,
    ) -> Dict[str, Any]:
        """Generate substantive 6-8 minute documentary script (~800-1200 words)."""
        clean_topic = topic.strip()

        hook = (
            f"Right now, hidden beneath the surface of our everyday reality, {clean_topic} is quietly reshaping "
            f"everything scientists thought they knew about how the universe operates. Yet if you asked the average person on the street, "
            f"they wouldn't even know it exists."
        )
        intro = (
            f"For over a century, the scientific consensus regarded these fundamental rules as settled law. "
            f"Textbooks were written, billion-dollar industries were built on standard models, and generations of researchers took the baseline "
            f"for granted. But over the past few years, an extraordinary sequence of new experiments and high-precision observations has completely "
            f"upended that comfort. What started as tiny, unexplained statistical blips has exploded into a full-blown revolution. "
            f"In this documentary, we are dissecting the untold story of {clean_topic}—how it was discovered, the first-principles mechanics powering it, "
            f"the bitter scientific battles fought over its legitimacy, and why its ultimate resolution will dictate the future of our modern world."
        )

        sections = [
            {
                "section_id": 1,
                "title": "Act I: The Genesis of the Anomaly",
                "content": (
                    f"To comprehend the profound significance of {clean_topic}, we have to journey back to the initial mystery that baffled theorists. "
                    f"{description} When researchers first began collecting preliminary data, the readings made no sense under classical models. "
                    f"Instruments were repeatedly recalibrated, sensors were swapped out, and entire experimental runs were discarded under the assumption "
                    f"that atmospheric noise or human error had contaminated the results. But the anomaly refused to disappear. Instead, with every increase in "
                    f"measurement resolution, the signal grew sharper and more undeniable. Early papers published on the subject were met with intense skepticism "
                    f"from the academic establishment. Established institutions argued that accepting these findings would mean throwing out decades of verified "
                    f"physics and empirical observations. Yet the pioneers refused to back down, recognizing that nature was whispering a profound secret "
                    f"that our existing mathematics simply weren't equipped to interpret."
                ),
                "visual_cue": "Historical archival footage and high-definition documentary timeline montage",
            },
            {
                "section_id": 2,
                "title": "Act II: First Principles and Core Mechanics",
                "content": (
                    f"To make sense of what is actually happening here, we must strip away the secondary noise and examine {clean_topic} from first principles. "
                    f"At its core, the phenomenon relies on a delicate equilibrium between microscopic kinetic forces and macro-scale structural coherence. "
                    f"Think of it like an intricate mechanical clockwork where billions of moving components interact simultaneously. In a conventional system, "
                    f"energy dissipation rapidly dampens fluctuations, causing the system to decay toward baseline entropy. But in the case of {clean_topic}, "
                    f"the interaction pathways exhibit an astonishing degree of self-reinforcing resonance. Rather than leaking energy into the environment, "
                    f"the system channels force through highly localized topological channels. This explains why standard thermodynamic approximations failed: "
                    f"they assumed random dispersion, when in reality, the process is governed by strict deterministic geometry. When visualized through "
                    f"computational phase-space diagrams, what once looked like chaotic turbulence reveals itself as an exquisitely ordered dance of cause and effect."
                ),
                "visual_cue": "3D photorealistic animated schematic illustrating internal dynamics and energy transfer",
            },
            {
                "section_id": 3,
                "title": "Act III: The Experimental Turning Point",
                "content": (
                    f"The true breakthrough occurred when international teams were finally able to test {clean_topic} under extreme controlled conditions. "
                    f"Using cryogenic cooling, electromagnetic shielding, and high-speed laser interferometry, physicists isolated the interaction from "
                    f"external interference. What they observed during those crucial trials altered the trajectory of modern research forever. As variables were "
                    f"pushed to theoretical limits, the system demonstrated non-linear phase transitions that had previously only existed on theoretical whiteboards. "
                    f"Data curves matched computer predictions with astonishing sub-percent accuracy. Skeptics who had spent years arguing against the validity "
                    f"of the research were forced to concede that the empirical proof was airtight. The focus instantly shifted from questioning whether the "
                    f"phenomenon existed to frantically trying to map out its fundamental limits and operational rules."
                ),
                "visual_cue": "Dynamic data visualization of complex equations transforming into visual clarity",
            },
            {
                "section_id": 4,
                "title": "Act IV: Real-World Stakes and Global Applications",
                "content": (
                    f"Why does any of this matter beyond the walls of research laboratories? Because mastering {clean_topic} opens up technological and "
                    f"economic frontiers that sound like pure science fiction. In energy production, understanding these mechanisms could eliminate critical "
                    f"efficiency bottlenecks that have stalled next-generation power grids for decades. In materials science and medicine, the same principles "
                    f"allow engineers to manipulate molecular structures with surgical precision, unlocking self-healing composites, ultra-dense energy storage, "
                    f"and revolutionary therapeutic delivery mechanisms. Nations and multi-billion-dollar technology consortiums are now engaged in a fierce, "
                    f"quiet race to secure proprietary patents in this exact domain. The geopolitical stakes are colossal: whoever leads the industrial implementation "
                    f"of these principles will hold a decisive competitive advantage for the remainder of the twenty-first century."
                ),
                "visual_cue": "Modern industrial and laboratory footage showing state-of-the-art implementation",
            },
            {
                "section_id": 5,
                "title": "Act V: The Unanswered Questions and the Road Ahead",
                "content": (
                    f"Even as our technological applications sprint forward, the deepest foundational mysteries of {clean_topic} remain frustratingly open. "
                    f"Every breakthrough we celebrate seems to unmask three deeper, more perplexing enigmas. When we push our current theoretical frameworks "
                    f"to the absolute frontier—such as extreme gravitational gradients or quantum scale thresholds—our mathematical equations begin to diverge "
                    f"into infinity once again. This points toward an inescapable conclusion: we have not reached the end of physics; we have merely turned "
                    f"to a new chapter in an unfinished book. The answers will require a new generation of scientists daring enough to challenge our most sacred "
                    f"dogmas and follow the evidence wherever it leads."
                ),
                "visual_cue": "Slow cinematic camera drifting toward a vast star field or micro-scale horizon",
            },
        ]

        cta = (
            f"If you enjoyed this deep investigative documentary into the science and future of {clean_topic}, hit that like button right now. "
            f"Make sure to subscribe to the channel and turn on all notifications so you never miss our long-form documentary releases every single week."
        )
        outro = (
            f"The universe never ceases to humble us with its hidden complexity. We want to hear from you: "
            f"do you believe {clean_topic} will revolutionize modern society, or are we still missing the biggest piece of the puzzle? "
            f"Drop your thoughts and perspectives in the comments below. Until next time, keep questioning, and stay curious."
        )

        full_script = self.format_full_script(sections, hook, intro, cta, outro)
        word_count = self.count_words(full_script)
        est_duration = self.estimate_duration_seconds(word_count)

        desc = (
            f"{title}\n\n"
            f"{description}\n\n"
            f"In this comprehensive documentary, we take an in-depth look into the science, history, "
            f"and future of {clean_topic}.\n\n"
            f"TIMESTAMPS:\n"
            f"0:00 - The Mystery Behind {clean_topic}\n"
            f"1:15 - First Principles & Mechanics\n"
            f"3:30 - The Paradigm Shift\n"
            f"5:10 - Real-World Applications\n"
            f"7:00 - The Unanswered Questions\n\n"
            f"Subscribe to our channel for weekly documentaries on science, technology, history, and space.\n\n"
            f"#{niche.capitalize()} #Documentary #Science #Education"
        )

        tags = [
            clean_topic.lower(),
            niche.lower(),
            "documentary",
            "science documentary",
            "explained",
            "deep dive",
            "education",
            "history",
            "physics",
            "technology",
        ]

        return {
            "title": title,
            "hook": hook,
            "intro": intro,
            "script_sections": sections,
            "cta": cta,
            "outro": outro,
            "full_script": full_script,
            "description": desc,
            "tags": tags,
            "word_count": word_count,
            "estimated_duration_seconds": est_duration,
            "video_type": "long",
            "style": style,
            "source": "template",
        }

    @staticmethod
    def format_full_script(
        sections: List[Dict[str, Any]],
        hook: str = "",
        intro: str = "",
        cta: str = "",
        outro: str = "",
    ) -> str:
        """Concatenate all script components into a single clean voiceover narration.

        Removes production notes and visual cues so the text is pristine for TTS engines.

        Args:
            sections: List of script sections.
            hook: Opening hook text.
            intro: Introduction text.
            cta: Call to action text.
            outro: Outro text.

        Returns:
            str: Clean concatenated voiceover narration.
        """
        parts: List[str] = []
        if hook:
            parts.append(hook.strip())
        if intro:
            parts.append(intro.strip())

        for s in sections:
            content = s.get("content", "").strip()
            if content:
                # Remove visual cue artifacts like [Visual: ...] or (Camera zooms ...)
                clean_content = re.sub(r"\[.*?\]|\(.*?\)", "", content).strip()
                if clean_content:
                    parts.append(clean_content)

        if cta:
            parts.append(cta.strip())
        if outro:
            parts.append(outro.strip())

        return " ".join(parts)

    def write_script(
        self,
        idea: Optional[Union[str, Dict[str, Any], Any]] = None,
        video_type: str = "short",
        style: str = "explainer",
        target_duration: Optional[int] = None,
        topic: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a complete production video script for an idea.

        Attempts Gemini API generation with rate-limiting and prompt engineering,
        and seamlessly falls back to procedural template generation if offline or unavailable.

        Args:
            idea: Either a topic string, an idea dictionary, or a ContentIdea object.
            video_type: 'short' (30-58 seconds) or 'long' (5-10 minutes).
            style: 'narrative', 'listicle', 'explainer', or 'story'.
            target_duration: Optional target duration in seconds for tuning length.
            topic: Optional keyword alias for topic string.

        Returns:
            Dict[str, Any]: Structured script output dictionary containing title, hook,
                            intro, script_sections, cta, outro, full_script, script, description,
                            tags, word_count, and estimated_duration_seconds.
        """
        # Resolve target input from idea or topic
        target_input = idea if idea is not None else (topic or "Science Topic")

        # Parse idea input
        if isinstance(target_input, str):
            t_topic = target_input.strip()
            title = f"The Truth About {t_topic}"
            description = f"An in-depth breakdown of {t_topic} and why it matters."
            niche = "science"
        elif isinstance(target_input, dict):
            t_topic = target_input.get("topic", target_input.get("title", "Science Topic"))
            title = target_input.get("title", f"The Truth About {t_topic}")
            description = target_input.get("description", f"Exploring {t_topic}.")
            niche = target_input.get("niche", "science")
        else:
            # Assumed ContentIdea or similar object
            t_topic = getattr(target_input, "topic", "Science Topic")
            title = getattr(target_input, "title", f"The Truth About {t_topic}")
            description = getattr(target_input, "description", f"Exploring {t_topic}.")
            niche = getattr(target_input, "niche", "science")

        v_type = video_type.lower().strip() if video_type.lower().strip() in self.SUPPORTED_TYPES else "short"
        v_style = style.lower().strip() if style.lower().strip() in self.SUPPORTED_STYLES else "explainer"

        # Attempt Gemini Generation
        result: Optional[Dict[str, Any]] = None
        if self._gemini_initialized:
            result = self._write_with_gemini(
                topic=t_topic,
                title=title,
                description=description,
                video_type=v_type,
                style=v_style,
                niche=niche,
            )

        # Fallback to procedural templates if Gemini failed or is unavailable
        if not result:
            result = self._generate_template_script(
                topic=t_topic,
                title=title,
                description=description,
                video_type=v_type,
                style=v_style,
                niche=niche,
            )

        # Ensure 'script' key alias is present for external callers
        if "script" not in result:
            result["script"] = result.get("full_script", "")

        logger.info(
            "Script generated for '%s' (%s, %s): %d words, ~%.1fs (via %s)",
            t_topic,
            v_type,
            v_style,
            result["word_count"],
            result["estimated_duration_seconds"],
            result["source"],
        )

        return result
