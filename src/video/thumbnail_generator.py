"""
Thumbnail Generator Module for YouTube AI Autopilot.

Creates eye-catching, production-quality thumbnails using Pillow and NumPy.
Supports both standard YouTube landscape thumbnails (1280x720) and vertical
Shorts thumbnails (1080x1920), with high-contrast color palettes, category-based
theming, multi-layer drop shadows, dynamic text wrapping, auto-scaling typography,
and multiple modern design templates.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Configure logger
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Palette & Theme Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColorPalette:
    """Represents a coordinated color palette for thumbnail generation."""
    name: str
    bg_start: Tuple[int, int, int]
    bg_end: Tuple[int, int, int]
    primary_text: Tuple[int, int, int]
    accent_text: Tuple[int, int, int]
    badge_bg: Tuple[int, int, int]
    badge_text: Tuple[int, int, int]
    glow_color: Tuple[int, int, int]
    border_color: Tuple[int, int, int]


CATEGORY_PALETTES: Dict[str, ColorPalette] = {
    "technology": ColorPalette(
        name="technology",
        bg_start=(8, 14, 26),
        bg_end=(18, 32, 64),
        primary_text=(255, 255, 255),
        accent_text=(0, 242, 254),     # Electric Cyan
        badge_bg=(0, 180, 216),
        badge_text=(8, 14, 26),
        glow_color=(0, 242, 254),
        border_color=(0, 242, 254),
    ),
    "science": ColorPalette(
        name="science",
        bg_start=(6, 18, 22),
        bg_end=(14, 46, 56),
        primary_text=(255, 255, 255),
        accent_text=(0, 245, 160),     # Emerald Neon
        badge_bg=(0, 217, 245),
        badge_text=(6, 18, 22),
        glow_color=(0, 245, 160),
        border_color=(0, 245, 160),
    ),
    "education": ColorPalette(
        name="education",
        bg_start=(15, 23, 42),
        bg_end=(30, 41, 59),
        primary_text=(255, 255, 255),
        accent_text=(255, 210, 0),     # Gold
        badge_bg=(245, 158, 11),
        badge_text=(15, 23, 42),
        glow_color=(255, 210, 0),
        border_color=(245, 158, 11),
    ),
    "finance": ColorPalette(
        name="finance",
        bg_start=(8, 20, 15),
        bg_end=(16, 44, 32),
        primary_text=(255, 255, 255),
        accent_text=(52, 211, 153),    # Mint Neon
        badge_bg=(16, 185, 129),
        badge_text=(8, 20, 15),
        glow_color=(52, 211, 153),
        border_color=(52, 211, 153),
    ),
    "gaming": ColorPalette(
        name="gaming",
        bg_start=(18, 3, 38),
        bg_end=(48, 10, 78),
        primary_text=(255, 255, 255),
        accent_text=(247, 37, 133),    # Cyber Magenta
        badge_bg=(114, 9, 183),
        badge_text=(255, 255, 255),
        glow_color=(247, 37, 133),
        border_color=(247, 37, 133),
    ),
    "entertainment": ColorPalette(
        name="entertainment",
        bg_start=(24, 7, 15),
        bg_end=(64, 12, 38),
        primary_text=(255, 255, 255),
        accent_text=(255, 75, 43),     # Sunset Orange
        badge_bg=(255, 65, 108),
        badge_text=(255, 255, 255),
        glow_color=(255, 75, 43),
        border_color=(255, 75, 43),
    ),
    "default": ColorPalette(
        name="default",
        bg_start=(12, 16, 28),
        bg_end=(28, 36, 62),
        primary_text=(255, 255, 255),
        accent_text=(0, 229, 255),     # Vibrant Aqua
        badge_bg=(61, 90, 254),
        badge_text=(255, 255, 255),
        glow_color=(0, 229, 255),
        border_color=(0, 229, 255),
    ),
}

# Category emoji icon fallbacks
CATEGORY_DEFAULT_ICONS: Dict[str, str] = {
    "technology": "⚡",
    "science": "🔬",
    "education": "💡",
    "finance": "📈",
    "gaming": "🎮",
    "entertainment": "🔥",
    "default": "🚀",
}


# ---------------------------------------------------------------------------
# Font Manager Helper
# ---------------------------------------------------------------------------

class FontManager:
    """Discovers, caches, and provides TrueType fonts with graceful fallbacks."""

    CANDIDATE_PATHS = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/hack/Hack-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ]

    ARABIC_CANDIDATE_PATHS = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    _font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

    @classmethod
    def is_arabic_text(cls, text: str) -> bool:
        """Check if text contains Arabic Unicode characters."""
        for ch in text:
            if '\u0600' <= ch <= '\u06FF' or '\u0750' <= ch <= '\u077F' or '\u08A0' <= ch <= '\u08FF':
                return True
        return False

    @classmethod
    def get_font_path(cls, is_arabic: bool = False) -> Optional[str]:
        """Find the first available TrueType font on the host operating system."""
        candidates = cls.ARABIC_CANDIDATE_PATHS if is_arabic else cls.CANDIDATE_PATHS
        for path in candidates:
            if os.path.exists(path):
                return path
        # Fallback to any TTF found in Liberation or DejaVu
        for path in cls.CANDIDATE_PATHS:
            if os.path.exists(path):
                return path
        return None

    @classmethod
    def get_font(cls, size: int, is_arabic: bool = False) -> ImageFont.ImageFont:
        """Load a font at the specified size with caching."""
        font_path = cls.get_font_path(is_arabic=is_arabic)
        cache_key = (font_path or "default", size)
        if cache_key in cls._font_cache:
            return cls._font_cache[cache_key]

        if font_path and os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, size)
                cls._font_cache[cache_key] = font
                return font
            except Exception as e:
                logger.warning("Failed to load font from %s: %s. Using default font.", font_path, e)

        font = ImageFont.load_default()
        return font


# ---------------------------------------------------------------------------
# Thumbnail Generator Class
# ---------------------------------------------------------------------------

class ThumbnailGenerator:
    """
    High-performance, production-ready YouTube thumbnail generator.

    Produces eye-catching 1280x720 (Regular) and 1080x1920 (Shorts) thumbnails.
    Includes 6 distinct design templates:
      - 'bold_modern': Dark high-contrast gradient, luminous accent orbs, dual-color headline.
      - 'cyber_glow': Cyberpunk neon aesthetic with glowing perimeter borders and corner brackets.
      - 'tech_minimal': Ultra-clean, modern geometric layout with left accent bar and crisp typography.
      - 'breaking_bold': High-impact urgent alert style with bright solid highlight boxes behind words.
      - 'education_pro': Elegant semi-transparent frosted card container with structured title and subtitle.
      - 'cosmic_radial': Deep cosmic gradient with multi-point radial glows and radiant light bursts.
    """

    DIMENSIONS_REGULAR = (1280, 720)
    DIMENSIONS_SHORTS = (1080, 1920)

    SUPPORTED_STYLES = [
        "bold_modern",
        "cyber_glow",
        "tech_minimal",
        "breaking_bold",
        "education_pro",
        "cosmic_radial",
    ]

    def __init__(self, output_dir: Optional[Union[str, Path]] = None) -> None:
        """
        Initialize the ThumbnailGenerator.

        Args:
            output_dir: Optional default directory to save generated thumbnails.
                        Defaults to 'assets/thumbnails' relative to current working directory.
        """
        if output_dir is not None:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = Path("assets/thumbnails")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Public Generation Interface
    # -----------------------------------------------------------------------

    def generate_thumbnail(
        self,
        title: str,
        style: str = "bold_modern",
        video_type: str = "regular",
        category: str = "technology",
        icon: Optional[str] = None,
        output_path: Optional[Union[str, Path]] = None,
        badge_text: Optional[str] = None,
        subtitle: Optional[str] = None,
    ) -> str:
        """
        Generate an eye-catching thumbnail and save it to disk.

        Args:
            title: Bold headline text to display prominently.
            style: One of 'bold_modern', 'cyber_glow', 'tech_minimal', 'breaking_bold',
                   'education_pro', 'cosmic_radial'.
            video_type: 'regular' for 1280x720 landscape, or 'shorts' for 1080x1920 portrait.
            category: Niche/category ('technology', 'science', 'education', 'finance', 'gaming', 'entertainment').
            icon: Optional emoji or Unicode icon to display (e.g. '⚡', '🚀', '🤖').
            output_path: Optional explicit file path to write to (PNG/JPG).
            badge_text: Optional upper pill badge text (e.g., 'NEW', 'AI BREAKTHROUGH', 'TUTORIAL').
            subtitle: Optional secondary supporting text line.

        Returns:
            Absolute file path string to the generated thumbnail.
        """
        if not title or not title.strip():
            title = "NEW VIDEO"
        title = title.strip()

        video_type_clean = video_type.lower().strip()
        if video_type_clean not in ("regular", "shorts"):
            logger.warning("Unrecognized video_type '%s', defaulting to 'regular'", video_type)
            video_type_clean = "regular"

        style_clean = style.lower().strip()
        if style_clean not in self.SUPPORTED_STYLES:
            logger.warning("Unrecognized style '%s', defaulting to 'bold_modern'", style)
            style_clean = "bold_modern"

        category_clean = category.lower().strip()
        palette = CATEGORY_PALETTES.get(category_clean, CATEGORY_PALETTES["default"])

        if icon is None:
            icon = CATEGORY_DEFAULT_ICONS.get(category_clean, CATEGORY_DEFAULT_ICONS["default"])

        if badge_text is None:
            badge_text = category_clean.upper() if category_clean != "default" else "AI AUTOPILOT"

        width, height = self.DIMENSIONS_SHORTS if video_type_clean == "shorts" else self.DIMENSIONS_REGULAR

        # 1. Generate base image with selected template style
        if style_clean == "cyber_glow":
            img = self._render_cyber_glow(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)
        elif style_clean == "tech_minimal":
            img = self._render_tech_minimal(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)
        elif style_clean == "breaking_bold":
            img = self._render_breaking_bold(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)
        elif style_clean == "education_pro":
            img = self._render_education_pro(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)
        elif style_clean == "cosmic_radial":
            img = self._render_cosmic_radial(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)
        else:  # bold_modern (default)
            img = self._render_bold_modern(width, height, palette, title, subtitle, badge_text, icon, video_type_clean)

        # 2. Contrast and readability post-processing
        img = self._optimize_contrast(img)

        # 3. Determine destination path
        if output_path is not None:
            target_path = Path(output_path).resolve()
            target_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            safe_title = "".join(c if c.isalnum() else "_" for c in title[:30]).strip("_") or "thumb"
            filename = f"thumbnail_{video_type_clean}_{style_clean}_{safe_title}.jpg"
            target_path = (self.output_dir / filename).resolve()

        # 4. Save optimized image (JPEG 95% quality, baseline compatible with YouTube < 2MB requirements)
        if target_path.suffix.lower() in (".jpg", ".jpeg"):
            img.save(target_path, "JPEG", quality=95, optimize=True)
        else:
            img.save(target_path, "PNG", optimize=True)

        logger.info("Generated thumbnail successfully saved to: %s", target_path)
        return str(target_path)

    # -----------------------------------------------------------------------
    # Background Generation Helpers (NumPy Gradients)
    # -----------------------------------------------------------------------

    def _create_linear_gradient(
        self,
        width: int,
        height: int,
        color_start: Tuple[int, int, int],
        color_end: Tuple[int, int, int],
        direction: str = "diagonal",
    ) -> Image.Image:
        """Create a smooth 2D linear gradient array using NumPy."""
        x = np.linspace(0, 1, width, dtype=np.float32)
        y = np.linspace(0, 1, height, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)

        if direction == "horizontal":
            factor = xx
        elif direction == "vertical":
            factor = yy
        else:  # diagonal
            factor = (xx + yy) / 2.0

        c_start = np.array(color_start, dtype=np.float32)
        c_end = np.array(color_end, dtype=np.float32)

        grad = (1.0 - factor[..., None]) * c_start + factor[..., None] * c_end
        grad = np.clip(grad, 0, 255).astype(np.uint8)
        return Image.fromarray(grad, "RGB")

    def _create_radial_gradient(
        self,
        width: int,
        height: int,
        center_color: Tuple[int, int, int],
        outer_color: Tuple[int, int, int],
        center: Tuple[float, float] = (0.5, 0.5),
        radius_factor: float = 0.8,
    ) -> Image.Image:
        """Create a smooth radial gradient with customizable center point and falloff."""
        cx, cy = center
        x = np.linspace(0, 1, width, dtype=np.float32)
        y = np.linspace(0, 1, height, dtype=np.float32)
        xx, yy = np.meshgrid(x, y)

        # Aspect-ratio corrected Euclidean distance
        aspect = width / height
        dist = np.sqrt(((xx - cx) * aspect) ** 2 + (yy - cy) ** 2)
        dist = np.clip(dist / radius_factor, 0.0, 1.0)
        # Cosine smoothstep for smooth natural falloff
        smooth_factor = 0.5 * (1.0 - np.cos(dist * np.pi))

        c_center = np.array(center_color, dtype=np.float32)
        c_outer = np.array(outer_color, dtype=np.float32)

        grad = (1.0 - smooth_factor[..., None]) * c_center + smooth_factor[..., None] * c_outer
        grad = np.clip(grad, 0, 255).astype(np.uint8)
        return Image.fromarray(grad, "RGB")

    def _add_glow_orb(
        self,
        base_img: Image.Image,
        center: Tuple[int, int],
        radius: int,
        color: Tuple[int, int, int],
        alpha: int = 140,
    ) -> Image.Image:
        """Overlay a soft luminous light glow orb onto an existing image."""
        w, h = base_img.size
        glow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)

        cx, cy = center
        glow_draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=(*color, alpha),
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=radius // 2))

        out = Image.new("RGBA", (w, h))
        out.paste(base_img.convert("RGBA"), (0, 0))
        out.alpha_composite(glow_layer)
        return out.convert("RGB")

    def _draw_subtle_grid(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        step: int = 60,
        color: Tuple[int, int, int, int] = (255, 255, 255, 12),
    ) -> None:
        """Render a subtle modern tech grid background pattern."""
        for x in range(0, width, step):
            draw.line([(x, 0), (x, height)], fill=color, width=1)
        for y in range(0, height, step):
            draw.line([(0, y), (width, y)], fill=color, width=1)

    # -----------------------------------------------------------------------
    # Typography & Text Layout Utilities
    # -----------------------------------------------------------------------

    def _wrap_text_to_width(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        draw: ImageDraw.ImageDraw,
    ) -> List[str]:
        """Wrap text into multiple lines ensuring no line exceeds max_width."""
        words = text.split()
        if not words:
            return [""]

        lines: List[str] = []
        current_line: List[str] = []

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_width = bbox[2] - bbox[0]
            if line_width <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                else:
                    # Single word is wider than max_width, force wrap
                    lines.append(word)
                    current_line = []

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    def _find_optimal_font(
        self,
        text: str,
        max_width: int,
        max_height: int,
        starting_size: int,
        min_size: int = 36,
        is_arabic: bool = False,
    ) -> Tuple[ImageFont.ImageFont, List[str], int]:
        """Dynamically compute the largest font size and line split fitting the bounds."""
        dummy_img = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(dummy_img)

        curr_size = starting_size
        while curr_size >= min_size:
            font = FontManager.get_font(curr_size, is_arabic=is_arabic)
            lines = self._wrap_text_to_width(text, font, max_width, draw)
            line_height = int(curr_size * 1.25)
            total_height = len(lines) * line_height

            # Check if all lines fit within dimensions
            fits_width = all(
                (draw.textbbox((0, 0), line, font=font)[2] - draw.textbbox((0, 0), line, font=font)[0]) <= max_width
                for line in lines
            )
            fits_height = total_height <= max_height

            if fits_width and fits_height and len(lines) <= 4:
                return font, lines, line_height

            curr_size -= 4

        # Fallback to minimum size font
        font = FontManager.get_font(min_size, is_arabic=is_arabic)
        lines = self._wrap_text_to_width(text, font, max_width, draw)
        line_height = int(min_size * 1.25)
        return font, lines, line_height

    def _draw_text_with_shadow(
        self,
        draw: ImageDraw.ImageDraw,
        position: Tuple[int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: Tuple[int, int, int],
        shadow_color: Tuple[int, int, int] = (0, 0, 0),
        shadow_offset: int = 5,
        stroke_width: int = 3,
        stroke_fill: Tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        """Render text with a rich multi-pass drop shadow and crisp outline stroke."""
        x, y = position

        # Multi-layer drop shadow for depth
        for offset in range(shadow_offset, 0, -1):
            draw.text(
                (x + offset, y + offset),
                text,
                font=font,
                fill=shadow_color,
                stroke_width=stroke_width + 1,
                stroke_fill=shadow_color,
            )

        # Main text with stroke
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def _draw_badge(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        text: str,
        palette: ColorPalette,
        is_arabic: bool = False,
    ) -> Tuple[int, int]:
        """Draw an upper pill badge (e.g. category or 'NEW') and return (width, height)."""
        font = FontManager.get_font(28, is_arabic=is_arabic)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding_x = 22
        padding_y = 10
        badge_w = text_w + padding_x * 2
        badge_h = text_h + padding_y * 2

        # Draw rounded rectangle pill
        draw.rounded_rectangle(
            (x, y, x + badge_w, y + badge_h),
            radius=badge_h // 2,
            fill=palette.badge_bg,
            outline=palette.border_color,
            width=2,
        )

        # Draw badge text
        draw.text(
            (x + padding_x, y + padding_y - 2),
            text,
            font=font,
            fill=palette.badge_text,
        )

        return badge_w, badge_h

    # -----------------------------------------------------------------------
    # Template 1: Bold Modern (Default)
    # -----------------------------------------------------------------------

    def _render_bold_modern(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'bold_modern' template: High-energy gradient with glowing light orbs and 2-tone title."""
        base = self._create_linear_gradient(width, height, palette.bg_start, palette.bg_end, "diagonal")
        # Add glowing orbs
        base = self._add_glow_orb(
            base,
            (int(width * 0.8), int(height * 0.25)),
            radius=int(width * 0.3),
            color=palette.glow_color,
            alpha=65,
        )
        base = self._add_glow_orb(
            base,
            (int(width * 0.2), int(height * 0.8)),
            radius=int(width * 0.25),
            color=palette.accent_text,
            alpha=45,
        )

        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        self._draw_subtle_grid(draw, width, height, step=70, color=(255, 255, 255, 10))

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            # Safe zone: Center vertical (y: 350 to 1450)
            safe_x = 90
            safe_y = 420
            safe_w = width - 200
            safe_h = 750
            starting_font_size = 96
        else:
            # Regular landscape safe zone
            safe_x = 90
            safe_y = 120
            safe_w = int(width * 0.76)
            safe_h = height - 240
            starting_font_size = 82

        # Draw Badge
        badge_label = f"{icon}  {badge_text}" if icon else badge_text
        badge_w, badge_h = self._badge_w, _ = self._draw_badge(
            draw, safe_x, safe_y, badge_label, palette, is_arabic=is_arabic
        )

        # Title layout
        title_y = safe_y + badge_h + 35
        font, lines, line_height = self._find_optimal_font(
            title, safe_w, safe_h - badge_h - 40, starting_font_size, min_size=42, is_arabic=is_arabic
        )

        # Subtle dark backdrop plate behind title for guaranteed readability
        total_text_h = len(lines) * line_height
        backdrop_rect = (
            safe_x - 25,
            title_y - 20,
            safe_x + safe_w + 30,
            title_y + total_text_h + 20,
        )
        draw.rounded_rectangle(backdrop_rect, radius=18, fill=(0, 0, 0, 110))

        # Render lines with alternating/accent coloring
        for i, line in enumerate(lines):
            # Highlight first or last line with accent color
            line_color = palette.accent_text if (i == 0 and len(lines) > 1) or i == (len(lines) - 1) else palette.primary_text
            self._draw_text_with_shadow(
                draw,
                (safe_x, title_y + (i * line_height)),
                line,
                font,
                fill=line_color,
                shadow_offset=6,
                stroke_width=4,
            )

        # Render Subtitle if present
        if subtitle:
            sub_font = FontManager.get_font(34, is_arabic=FontManager.is_arabic_text(subtitle))
            sub_y = title_y + total_text_h + 30
            if sub_y + 40 < height - 60:
                self._draw_text_with_shadow(
                    draw,
                    (safe_x, sub_y),
                    subtitle,
                    sub_font,
                    fill=(220, 225, 235),
                    shadow_offset=4,
                    stroke_width=2,
                )

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Template 2: Cyber Glow
    # -----------------------------------------------------------------------

    def _render_cyber_glow(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'cyber_glow' template: Cyberpunk aesthetic, corner HUD brackets and glowing borders."""
        # Deep dark background with high-contrast radial glow
        base = self._create_radial_gradient(
            width, height, (30, 10, 50), palette.bg_start, center=(0.5, 0.4), radius_factor=0.9
        )
        base = self._add_glow_orb(
            base,
            (int(width * 0.5), int(height * 0.45)),
            radius=int(width * 0.35),
            color=palette.glow_color,
            alpha=70,
        )

        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Glowing outer frame border
        margin = 35
        draw.rectangle(
            (margin, margin, width - margin, height - margin),
            outline=(*palette.border_color, 160),
            width=3,
        )

        # Corner HUD brackets (aesthetic futuristic marks)
        bracket_len = 50
        # Top-left
        draw.line(
            [(margin, margin + bracket_len), (margin, margin), (margin + bracket_len, margin)],
            fill=palette.accent_text,
            width=7,
        )
        # Top-right
        draw.line(
            [(width - margin - bracket_len, margin), (width - margin, margin), (width - margin, margin + bracket_len)],
            fill=palette.accent_text,
            width=7,
        )
        # Bottom-left
        draw.line(
            [(margin, height - margin - bracket_len), (margin, height - margin), (margin + bracket_len, height - margin)],
            fill=palette.accent_text,
            width=7,
        )
        # Bottom-right
        draw.line(
            [
                (width - margin - bracket_len, height - margin),
                (width - margin, height - margin),
                (width - margin, height - margin - bracket_len),
            ],
            fill=palette.accent_text,
            width=7,
        )

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            safe_x = 90
            safe_y = 480
            safe_w = width - 180
            safe_h = 700
            starting_font_size = 92
        else:
            safe_x = 100
            safe_y = 130
            safe_w = width - 200
            safe_h = height - 260
            starting_font_size = 80

        # Draw top HUD Badge
        badge_label = f"/// {badge_text.upper()} {icon}" if icon else f"/// {badge_text.upper()}"
        font_badge = FontManager.get_font(26, is_arabic=is_arabic)
        draw.text((safe_x, safe_y), badge_label, font=font_badge, fill=palette.accent_text)

        title_y = safe_y + 55
        font, lines, line_height = self._find_optimal_font(
            title, safe_w, safe_h - 70, starting_font_size, min_size=40, is_arabic=is_arabic
        )

        for i, line in enumerate(lines):
            fill_c = palette.accent_text if i % 2 == 1 else (255, 255, 255)
            self._draw_text_with_shadow(
                draw,
                (safe_x, title_y + (i * line_height)),
                line,
                font,
                fill=fill_c,
                shadow_color=(0, 0, 0),
                shadow_offset=7,
                stroke_width=4,
                stroke_fill=(10, 5, 20),
            )

        if subtitle:
            sub_y = title_y + len(lines) * line_height + 25
            sub_font = FontManager.get_font(32, is_arabic=FontManager.is_arabic_text(subtitle))
            draw.text((safe_x, sub_y), f">> {subtitle}", font=sub_font, fill=(200, 210, 230))

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Template 3: Tech Minimal
    # -----------------------------------------------------------------------

    def _render_tech_minimal(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'tech_minimal' template: Clean obsidian theme, vertical accent bar, crisp typography."""
        base = self._create_linear_gradient(width, height, (12, 14, 20), (22, 26, 36), "vertical")
        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            bar_x = 70
            bar_y = 440
            bar_h = 750
            safe_x = bar_x + 50
            safe_w = width - safe_x - 70
            safe_h = 700
            safe_y = bar_y
            starting_font_size = 90
        else:
            bar_x = 90
            bar_y = 120
            bar_h = height - 240
            safe_x = bar_x + 45
            safe_w = width - safe_x - 90
            safe_h = bar_h
            safe_y = bar_y
            starting_font_size = 78

        # Vertical colored accent bar
        draw.rounded_rectangle((bar_x, bar_y, bar_x + 12, bar_y + bar_h), radius=6, fill=palette.accent_text)

        # Upper Tag
        tag_text = f"{icon}  {badge_text}" if icon else badge_text
        tag_font = FontManager.get_font(28, is_arabic=is_arabic)
        draw.text((safe_x, safe_y), tag_text.upper(), font=tag_font, fill=palette.accent_text)

        title_y = safe_y + 55
        font, lines, line_height = self._find_optimal_font(
            title, safe_w, safe_h - 75, starting_font_size, min_size=40, is_arabic=is_arabic
        )

        for i, line in enumerate(lines):
            fill_c = palette.primary_text if i == 0 else palette.accent_text
            self._draw_text_with_shadow(
                draw,
                (safe_x, title_y + (i * line_height)),
                line,
                font,
                fill=fill_c,
                shadow_offset=5,
                stroke_width=3,
            )

        if subtitle:
            sub_y = title_y + len(lines) * line_height + 25
            sub_font = FontManager.get_font(30, is_arabic=FontManager.is_arabic_text(subtitle))
            draw.text((safe_x, sub_y), subtitle, font=sub_font, fill=(160, 174, 192))

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Template 4: Breaking Bold
    # -----------------------------------------------------------------------

    def _render_breaking_bold(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'breaking_bold' template: Dramatic contrast with bold highlight text boxes behind words."""
        # Dark vignette background
        base = self._create_radial_gradient(width, height, (35, 30, 20), (10, 10, 12), center=(0.4, 0.5), radius_factor=0.9)
        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            safe_x = 80
            safe_y = 440
            safe_w = width - 160
            safe_h = 750
            starting_font_size = 94
        else:
            safe_x = 90
            safe_y = 110
            safe_w = width - 180
            safe_h = height - 220
            starting_font_size = 84

        # Red/Gold Alert Badge
        alert_label = f"🔥 {badge_text.upper()}"
        alert_w, alert_h = self._draw_badge(draw, safe_x, safe_y, alert_label, palette, is_arabic=is_arabic)

        title_y = safe_y + alert_h + 35
        font, lines, line_height = self._find_optimal_font(
            title, safe_w, safe_h - alert_h - 45, starting_font_size, min_size=42, is_arabic=is_arabic
        )

        for i, line in enumerate(lines):
            curr_y = title_y + (i * line_height)
            bbox = draw.textbbox((safe_x, curr_y), line, font=font)
            line_w = bbox[2] - bbox[0]
            box_h = bbox[3] - bbox[1] + 16

            # Alternate highlight boxes (Bright yellow/accent background box behind key words)
            if i % 2 == 0:
                box_color = palette.accent_text
                text_color = (10, 10, 15)  # Dark contrast text on bright box
            else:
                box_color = (255, 255, 255)
                text_color = (0, 0, 0)

            # Draw highlight plate behind the line
            draw.rectangle(
                (safe_x - 12, curr_y - 6, safe_x + line_w + 16, curr_y + box_h),
                fill=(*box_color, 245),
            )
            # Text inside box
            draw.text((safe_x, curr_y - 2), line, font=font, fill=text_color)

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Template 5: Education Pro
    # -----------------------------------------------------------------------

    def _render_education_pro(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'education_pro' template: Semi-transparent frosted card container with clean hierarchy."""
        base = self._create_linear_gradient(width, height, palette.bg_start, palette.bg_end, "diagonal")
        base = self._add_glow_orb(
            base,
            (int(width * 0.85), int(height * 0.15)),
            radius=int(width * 0.28),
            color=palette.glow_color,
            alpha=55,
        )

        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            card_x = 60
            card_y = 380
            card_w = width - 120
            card_h = 900
            starting_font_size = 88
        else:
            card_x = 75
            card_y = 75
            card_w = width - 150
            card_h = height - 150
            starting_font_size = 76

        # Draw Frosted glass style container card
        draw.rounded_rectangle(
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=24,
            fill=(15, 23, 42, 185),
            outline=(255, 255, 255, 40),
            width=2,
        )

        inner_x = card_x + 50
        inner_y = card_y + 45
        inner_w = card_w - 100

        # Badge pill inside card
        badge_label = f"{icon}  {badge_text}" if icon else badge_text
        b_w, b_h = self._draw_badge(draw, inner_x, inner_y, badge_label, palette, is_arabic=is_arabic)

        title_y = inner_y + b_h + 30
        max_h = card_h - b_h - 110
        font, lines, line_height = self._find_optimal_font(
            title, inner_w, max_h, starting_font_size, min_size=38, is_arabic=is_arabic
        )

        for i, line in enumerate(lines):
            line_c = palette.primary_text if i < len(lines) - 1 else palette.accent_text
            self._draw_text_with_shadow(
                draw,
                (inner_x, title_y + (i * line_height)),
                line,
                font,
                fill=line_c,
                shadow_offset=4,
                stroke_width=2,
            )

        if subtitle:
            sub_y = title_y + len(lines) * line_height + 25
            sub_font = FontManager.get_font(32, is_arabic=FontManager.is_arabic_text(subtitle))
            draw.text((inner_x, sub_y), subtitle, font=sub_font, fill=(203, 213, 225))

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Template 6: Cosmic Radial
    # -----------------------------------------------------------------------

    def _render_cosmic_radial(
        self,
        width: int,
        height: int,
        palette: ColorPalette,
        title: str,
        subtitle: Optional[str],
        badge_text: str,
        icon: str,
        video_type: str,
    ) -> Image.Image:
        """Render 'cosmic_radial' template: Deep cosmic space gradient with glowing radiant bursts."""
        base = self._create_radial_gradient(
            width, height, (35, 12, 60), (5, 7, 18), center=(0.7, 0.35), radius_factor=0.9
        )
        base = self._add_glow_orb(
            base,
            (int(width * 0.7), int(height * 0.35)),
            radius=int(width * 0.32),
            color=palette.glow_color,
            alpha=80,
        )
        base = self._add_glow_orb(
            base,
            (int(width * 0.25), int(height * 0.75)),
            radius=int(width * 0.22),
            color=palette.accent_text,
            alpha=50,
        )

        img = base.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Draw subtle cosmic particles / stars
        np.random.seed(42)
        star_x = np.random.randint(0, width, size=45)
        star_y = np.random.randint(0, height, size=45)
        star_r = np.random.randint(1, 3, size=45)
        for sx, sy, sr in zip(star_x, star_y, star_r):
            draw.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=(255, 255, 255, 180))

        is_arabic = FontManager.is_arabic_text(title)

        if video_type == "shorts":
            safe_x = 90
            safe_y = 450
            safe_w = width - 180
            safe_h = 750
            starting_font_size = 94
        else:
            safe_x = 90
            safe_y = 120
            safe_w = int(width * 0.72)
            safe_h = height - 240
            starting_font_size = 84

        badge_label = f"{icon}  {badge_text}" if icon else badge_text
        b_w, b_h = self._draw_badge(draw, safe_x, safe_y, badge_label, palette, is_arabic=is_arabic)

        title_y = safe_y + b_h + 35
        font, lines, line_height = self._find_optimal_font(
            title, safe_w, safe_h - b_h - 40, starting_font_size, min_size=40, is_arabic=is_arabic
        )

        for i, line in enumerate(lines):
            fill_c = palette.accent_text if i == 0 else palette.primary_text
            self._draw_text_with_shadow(
                draw,
                (safe_x, title_y + (i * line_height)),
                line,
                font,
                fill=fill_c,
                shadow_offset=6,
                stroke_width=4,
            )

        if subtitle:
            sub_y = title_y + len(lines) * line_height + 25
            sub_font = FontManager.get_font(32, is_arabic=FontManager.is_arabic_text(subtitle))
            draw.text((safe_x, sub_y), subtitle, font=sub_font, fill=(216, 180, 254))

        img.alpha_composite(overlay)
        return img.convert("RGB")

    # -----------------------------------------------------------------------
    # Post-Processing & Optimization
    # -----------------------------------------------------------------------

    def _optimize_contrast(self, image: Image.Image) -> Image.Image:
        """
        Apply gentle contrast optimization and unsharp masking to make
        text and visual elements pop on mobile screens and YouTube feeds.
        """
        # Slight sharpening filter for crisp text borders
        sharpened = image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=110, threshold=3))
        return sharpened
