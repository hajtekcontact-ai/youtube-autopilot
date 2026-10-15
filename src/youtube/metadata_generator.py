"""
YouTube Video Metadata Generator and SEO Optimizer.

Generates production-quality metadata for regular videos and Shorts:
  - High-CTR, SEO-optimized titles (max 100 chars)
  - Comprehensive descriptions with chapters/timestamps, links, call to actions,
    and AI-generated content disclosures (max 5000 chars)
  - Relevant tags adhering to YouTube's 500-char cumulative limit
  - YouTube Category ID mapping
  - Shorts-specific formatting and hashtag injection
  - Strict input validation and sanitization
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YouTube Category Mappings
# ---------------------------------------------------------------------------

YOUTUBE_CATEGORIES: Dict[str, str] = {
    "film_animation": "1",
    "autos_vehicles": "2",
    "music": "10",
    "pets_animals": "15",
    "sports": "17",
    "short_movies": "18",
    "travel_events": "19",
    "gaming": "20",
    "videoblogging": "21",
    "people_blogs": "22",
    "comedy": "23",
    "entertainment": "24",
    "news_politics": "25",
    "howto_style": "26",
    "education": "27",
    "science_technology": "28",
    "nonprofits_activism": "29",
}

# User-friendly alias mapping
CATEGORY_ALIASES: Dict[str, str] = {
    "tech": "28",
    "technology": "28",
    "science": "28",
    "science & technology": "28",
    "science_technology_education": "28",
    "ai": "28",
    "education": "27",
    "educational": "27",
    "tutorial": "26",
    "howto": "26",
    "gaming": "20",
    "games": "20",
    "entertainment": "24",
    "news": "25",
    "business": "28",
    "finance": "27",
}


# ---------------------------------------------------------------------------
# AI Disclosure Text Constants
# ---------------------------------------------------------------------------

AI_DISCLOSURE_EN = (
    "🤖 AI Content Disclosure:\n"
    "This video contains altered or synthetic content. Voiceover narration, script, "
    "and visual elements were created with AI assistance in full compliance with YouTube Community Guidelines."
)

AI_DISCLOSURE_AR = (
    "🤖 إفصاح محتوى الذكاء الاصطناعي:\n"
    "يحتوي هذا الفيديو على محتوى تم توليده أو تعديله باستخدام تقنيات الذكاء الاصطناعي "
    "(التعليق الصوتي، النص، والعناصر المرئية) التزاماً بإرشادات منتدى YouTube."
)


# ---------------------------------------------------------------------------
# Video Metadata Dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoMetadata:
    """Represents complete YouTube video metadata ready for API upload."""
    title: str
    description: str
    tags: List[str] = field(default_factory=list)
    category_id: str = "28"
    privacy_status: str = "private"
    made_for_kids: bool = False
    self_declared_made_for_kids: bool = False
    has_ai_disclosure: bool = True
    scheduled_time: Optional[str] = None
    language: str = "en"
    default_audio_language: str = "en"
    video_type: str = "regular"

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata instance to a dictionary."""
        return asdict(self)

    def to_api_body(self) -> Dict[str, Any]:
        """
        Build the dictionary structure expected by YouTube Data API v3
        videos().insert or videos().update resource body.
        """
        snippet: Dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "categoryId": str(self.category_id),
            "defaultLanguage": self.language,
            "defaultAudioLanguage": self.default_audio_language,
        }

        status: Dict[str, Any] = {
            "privacyStatus": self.privacy_status,
            "selfDeclaredMadeForKids": self.made_for_kids,
            "embeddable": True,
            "license": "youtube",
        }

        # Scheduled publishing requires privacyStatus="private"
        if self.scheduled_time and self.privacy_status == "private":
            status["publishAt"] = self.scheduled_time

        return {
            "snippet": snippet,
            "status": status,
        }

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate metadata against YouTube Data API v3 restrictions.

        Returns:
            Tuple of (is_valid, list_of_error_messages)
        """
        errors: List[str] = []

        # 1. Title validation
        if not self.title or not self.title.strip():
            errors.append("Title cannot be empty.")
        elif len(self.title) > 100:
            errors.append(f"Title exceeds 100 characters (current: {len(self.title)}).")
        if "<" in self.title or ">" in self.title:
            errors.append("Title contains invalid angle bracket characters (< or >).")

        # 2. Description validation
        if len(self.description) > 5000:
            errors.append(f"Description exceeds 5000 characters (current: {len(self.description)}).")
        if "<" in self.description or ">" in self.description:
            errors.append("Description contains invalid angle bracket characters (< or >).")

        # 3. Tags validation (YouTube limit is cumulative 500 characters)
        cumulative_tags_len = sum(len(t) for t in self.tags) + max(0, len(self.tags) - 1)
        if cumulative_tags_len > 500:
            errors.append(f"Cumulative tags length exceeds 500 characters (current: {cumulative_tags_len}).")
        if len(self.tags) > 50:
            errors.append(f"Number of tags exceeds 50 (current: {len(self.tags)}).")

        # 4. Privacy Status validation
        if self.privacy_status not in ("private", "unlisted", "public"):
            errors.append(f"Invalid privacy status '{self.privacy_status}'. Must be private, unlisted, or public.")

        # 5. Scheduled time validation
        if self.scheduled_time:
            if self.privacy_status != "private":
                errors.append("Scheduled publishing requires privacyStatus to be set to 'private'.")
            try:
                # Validate ISO 8601 UTC format
                dt_str = self.scheduled_time.replace("Z", "+00:00")
                parsed_dt = datetime.fromisoformat(dt_str)
                if parsed_dt.tzinfo is None:
                    errors.append("scheduled_time must include UTC timezone offset or 'Z'.")
            except Exception as e:
                errors.append(f"Invalid scheduled_time format ({e}). Expected ISO 8601 UTC timestamp.")

        return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Metadata Generator Class
# ---------------------------------------------------------------------------

class MetadataGenerator:
    """
    Automated YouTube SEO Metadata Generator.

    Crafts optimized titles, rich descriptions with chapters and disclosures,
    and highly targeted tags from structured script inputs.
    """

    def __init__(
        self,
        channel_name: str = "AI Tech Autopilot",
        default_niche: str = "science_technology",
        default_language: str = "en",
        subscribe_link: Optional[str] = None,
        contact_email: Optional[str] = None,
    ) -> None:
        """
        Initialize the MetadataGenerator.

        Args:
            channel_name: Name of the channel to brand in descriptions.
            default_niche: Default channel category niche.
            default_language: Default language ('en' or 'ar').
            subscribe_link: Optional custom YouTube subscribe link.
            contact_email: Optional contact email for descriptions.
        """
        self.channel_name = channel_name
        self.default_niche = default_niche
        self.default_language = default_language
        self.subscribe_link = subscribe_link
        self.contact_email = contact_email

    # -----------------------------------------------------------------------
    # Public Generation Interface
    # -----------------------------------------------------------------------

    def generate(
        self,
        topic: str = "",
        script: str = "",
        video_type: str = "regular",
        category: str = "science_technology",
        language: str = "en",
        **kwargs: Any,
    ) -> VideoMetadata:
        """Convenience method called by the production pipeline orchestrator."""
        script_data = {
            "title": topic,
            "description": script[:500] if script else "",
            "summary": script[:500] if script else "",
            "category": category,
            "language": language,
        }
        return self.generate_metadata(script_data, video_type=video_type)

    def generate_metadata(
        self,
        script_data: Dict[str, Any],
        video_type: str = "regular",
        privacy_status: str = "private",
        scheduled_time: Optional[str] = None,
        include_ai_disclosure: bool = True,
    ) -> VideoMetadata:
        """
        Generate complete, SEO-optimized metadata from script data.

        Args:
            script_data: Dictionary containing script details:
                - 'title' or 'topic': The main subject
                - 'summary' or 'description': Overview text
                - 'chapters' or 'sections': List of dicts with 'timestamp' and 'title'
                - 'keywords' or 'tags': List of keywords
                - 'category': Niche or category name
                - 'language': 'en' or 'ar'
                - 'resources': List of resource links
            video_type: 'regular' for standard landscape videos, or 'shorts' for YouTube Shorts.
            privacy_status: 'private', 'unlisted', or 'public'.
            scheduled_time: Optional ISO UTC timestamp string.
            include_ai_disclosure: Whether to append the AI content declaration.

        Returns:
            Validated VideoMetadata instance.
        """
        video_type_clean = video_type.lower().strip()
        is_shorts = video_type_clean == "shorts"

        lang = script_data.get("language", self.default_language).lower()
        raw_category = script_data.get("category", self.default_niche).lower()
        category_id = self.resolve_category_id(raw_category)

        # 1. Optimize Title
        raw_title = script_data.get("title") or script_data.get("topic") or "AI Technology Update"
        keywords = self._extract_keywords(script_data)
        title = self._optimize_title(raw_title, is_shorts=is_shorts, keywords=keywords)

        # 2. Build Description
        description = self._build_description(
            script_data=script_data,
            title=title,
            is_shorts=is_shorts,
            lang=lang,
            keywords=keywords,
            include_ai_disclosure=include_ai_disclosure,
        )

        # 3. Generate Tags (Max 500 cumulative chars)
        tags = self._generate_tags(
            title=title,
            keywords=keywords,
            is_shorts=is_shorts,
            raw_category=raw_category,
        )

        metadata = VideoMetadata(
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy_status,
            made_for_kids=False,
            self_declared_made_for_kids=False,
            has_ai_disclosure=include_ai_disclosure,
            scheduled_time=scheduled_time,
            language=lang,
            default_audio_language=lang,
            video_type=video_type_clean,
        )

        # Validate and sanitize
        is_valid, validation_errors = metadata.validate()
        if not is_valid:
            logger.warning("Metadata validation warnings: %s. Sanitizing...", validation_errors)
            metadata = self._sanitize_metadata(metadata)

        return metadata

    # -----------------------------------------------------------------------
    # Category Resolution
    # -----------------------------------------------------------------------

    @classmethod
    def resolve_category_id(cls, category_name: str) -> str:
        """Resolve a category name or alias to its numeric YouTube Category ID."""
        cleaned = category_name.lower().strip()
        if cleaned in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[cleaned]
        if cleaned in YOUTUBE_CATEGORIES:
            return YOUTUBE_CATEGORIES[cleaned]
        if cleaned.isdigit():
            return cleaned
        return "28"  # Default to Science & Technology

    # -----------------------------------------------------------------------
    # Title Optimization
    # -----------------------------------------------------------------------

    def _optimize_title(self, raw_title: str, is_shorts: bool, keywords: List[str]) -> str:
        """Sanitize and optimize title for high CTR and SEO within 100 characters."""
        clean = re.sub(r"[<>]", "", raw_title).strip()
        # Clean extra whitespace
        clean = re.sub(r"\s+", " ", clean)

        if is_shorts:
            # Ensure #Shorts is present
            if "#Shorts" not in clean and "#shorts" not in clean:
                if len(clean) + 9 <= 100:
                    clean = f"{clean} #Shorts"
                else:
                    clean = f"{clean[:89].strip()} #Shorts"
        else:
            # For regular videos, if length permits, add front-loaded punchiness
            if len(clean) > 100:
                clean = clean[:97].rstrip() + "..."

        return clean[:100]

    # -----------------------------------------------------------------------
    # Description Building
    # -----------------------------------------------------------------------

    def _build_description(
        self,
        script_data: Dict[str, Any],
        title: str,
        is_shorts: bool,
        lang: str,
        keywords: List[str],
        include_ai_disclosure: bool,
    ) -> str:
        """Construct a structured, engaging, SEO-friendly video description."""
        parts: List[str] = []

        # 1. Hook / Lead Paragraph
        summary = script_data.get("summary") or script_data.get("description")
        if summary:
            clean_summary = re.sub(r"[<>]", "", summary).strip()
            parts.append(clean_summary)
        else:
            parts.append(
                f"In this video, we explore {title}. Discover the key insights, "
                f"latest breakthroughs, and everything you need to know."
            )

        # 2. Shorts-specific concise layout
        if is_shorts:
            if include_ai_disclosure:
                parts.append("---")
                parts.append(AI_DISCLOSURE_AR if lang.startswith("ar") else AI_DISCLOSURE_EN)

            # Targeted hashtags
            short_tags = ["#Shorts", "#Tech", "#AI"]
            for kw in keywords[:3]:
                tag_cand = f"#{re.sub(r'[^a-zA-Z0-9]', '', kw.title())}"
                if len(tag_cand) > 2 and tag_cand not in short_tags:
                    short_tags.append(tag_cand)
            parts.append(" ".join(short_tags))

            desc = "\n\n".join(parts)
            return desc[:5000]

        # 3. Chapters / Timestamps (for regular videos)
        chapters = script_data.get("chapters") or script_data.get("sections")
        if chapters and isinstance(chapters, list):
            chapter_lines: List[str] = ["⏱️ Chapters:"]
            has_zero = False
            for ch in chapters:
                if isinstance(ch, dict):
                    ts = ch.get("timestamp", "").strip()
                    ch_title = re.sub(r"[<>]", "", ch.get("title", "")).strip()
                    if ts and ch_title:
                        if ts in ("00:00", "0:00"):
                            has_zero = True
                        chapter_lines.append(f"{ts} - {ch_title}")

            # Ensure starting timestamp 00:00 exists for YouTube chapter indexing
            if not has_zero and len(chapter_lines) > 1:
                chapter_lines.insert(1, "00:00 - Introduction")

            if len(chapter_lines) >= 4:  # At least 3 chapters
                parts.append("\n".join(chapter_lines))

        # 4. Key Points / Highlights
        highlights = script_data.get("highlights") or script_data.get("key_points")
        if highlights and isinstance(highlights, list):
            hl_lines = ["📌 Key Highlights:"]
            for h in highlights[:5]:
                clean_h = re.sub(r"[<>]", "", str(h)).strip()
                hl_lines.append(f"• {clean_h}")
            parts.append("\n".join(hl_lines))

        # 5. Resources & Links
        res_lines = ["🔗 Links & Resources:"]
        if self.subscribe_link:
            res_lines.append(f"👉 Subscribe to {self.channel_name}: {self.subscribe_link}")
        resources = script_data.get("resources")
        if resources and isinstance(resources, list):
            for res in resources[:4]:
                if isinstance(res, dict):
                    res_title = re.sub(r"[<>]", "", res.get("title", "Resource"))
                    res_url = res.get("url", "")
                    res_lines.append(f"• {res_title}: {res_url}")
                elif isinstance(res, str):
                    res_lines.append(f"• {res}")
        if len(res_lines) > 1:
            parts.append("\n".join(res_lines))

        # 6. Call to Action & Channel Notice
        cta_lines = [
            "🔔 Don't forget to LIKE, SUBSCRIBE, and turn on the NOTIFICATION BELL to never miss an update!",
            "💬 Have questions or insights? Share your thoughts in the comments below!",
        ]
        if self.contact_email:
            cta_lines.append(f"✉️ Contact: {self.contact_email}")
        parts.append("\n".join(cta_lines))

        # 7. AI Content Disclosure (Mandatory for synthetic media)
        if include_ai_disclosure:
            parts.append("---")
            parts.append(AI_DISCLOSURE_AR if lang.startswith("ar") else AI_DISCLOSURE_EN)

        # 8. Hashtags block at the end
        hashtags: List[str] = []
        for kw in keywords[:6]:
            tag_clean = f"#{re.sub(r'[^a-zA-Z0-9]', '', kw.title())}"
            if len(tag_clean) > 2 and tag_clean not in hashtags:
                hashtags.append(tag_clean)
        if hashtags:
            parts.append(" ".join(hashtags))

        full_desc = "\n\n".join(parts)
        if len(full_desc) > 5000:
            full_desc = full_desc[:4990].rstrip() + "..."
        return full_desc

    # -----------------------------------------------------------------------
    # Tag Generation & Limitation
    # -----------------------------------------------------------------------

    def _generate_tags(
        self,
        title: str,
        keywords: List[str],
        is_shorts: bool,
        raw_category: str,
    ) -> List[str]:
        """Generate high-relevance tags strictly fitting YouTube's 500 character limit."""
        tags: List[str] = []

        if is_shorts:
            tags.append("Shorts")
            tags.append("YouTube Shorts")

        # Add base keywords
        for kw in keywords:
            clean_kw = re.sub(r"[<>]", "", kw).strip()
            if clean_kw and clean_kw not in tags:
                tags.append(clean_kw)

        # Add words from title
        title_words = re.findall(r"\b[A-Za-z]{3,}\b", title)
        for w in title_words[:6]:
            if w.lower() not in [t.lower() for t in tags]:
                tags.append(w)

        # Add niche category tag
        if raw_category and raw_category not in [t.lower() for t in tags]:
            tags.append(raw_category.replace("_", " ").title())

        # Always add channel branding tag
        if self.channel_name and self.channel_name not in tags:
            tags.append(self.channel_name)

        # Filter and enforce 500 total character limit
        final_tags: List[str] = []
        curr_len = 0

        for tag in tags:
            tag_clean = re.sub(r"[<>]", "", tag).strip()
            if not tag_clean or len(tag_clean) > 100:
                continue

            # In YouTube API, tags are joined by commas: tag1,tag2
            added_len = len(tag_clean) + (1 if final_tags else 0)
            if curr_len + added_len <= 500 and len(final_tags) < 50:
                final_tags.append(tag_clean)
                curr_len += added_len
            else:
                break

        return final_tags

    def _extract_keywords(self, script_data: Dict[str, Any]) -> List[str]:
        """Extract and clean keywords from script metadata."""
        raw_kw = script_data.get("keywords") or script_data.get("tags") or []
        if isinstance(raw_kw, str):
            raw_kw = [k.strip() for k in raw_kw.split(",") if k.strip()]

        cleaned: List[str] = []
        for k in raw_kw:
            c = re.sub(r"[<>]", "", str(k)).strip()
            if c and c not in cleaned:
                cleaned.append(c)

        if not cleaned:
            # Fallback default keywords
            cleaned = ["Artificial Intelligence", "Technology", "Automation", "Future Tech"]

        return cleaned

    # -----------------------------------------------------------------------
    # Sanitization
    # -----------------------------------------------------------------------

    def _sanitize_metadata(self, metadata: VideoMetadata) -> VideoMetadata:
        """Sanitize metadata attributes to guarantee compliance with YouTube API."""
        title = re.sub(r"[<>]", "", metadata.title).strip()
        if len(title) > 100:
            title = title[:97].rstrip() + "..."

        desc = re.sub(r"[<>]", "", metadata.description).strip()
        if len(desc) > 5000:
            desc = desc[:4995].rstrip() + "..."

        # Re-enforce tag cumulative length
        clean_tags: List[str] = []
        accum = 0
        for t in metadata.tags[:50]:
            t_clean = re.sub(r"[<>]", "", t).strip()
            add_len = len(t_clean) + (1 if clean_tags else 0)
            if accum + add_len <= 500:
                clean_tags.append(t_clean)
                accum += add_len

        metadata.title = title
        metadata.description = desc
        metadata.tags = clean_tags
        return metadata
