"""Visual Generator for YouTube AI Autopilot.

Creates high-definition video frames, typography cards, statistical dashboards,
animated progress bars, geometric particles, and section transitions using Pillow (PIL)
and NumPy. Supports both landscape (1920x1080) and portrait/Shorts (1080x1920) resolutions.
"""

import math
import os
import random
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Supported Standard Resolutions
RES_LANDSCAPE: Tuple[int, int] = (1920, 1080)
RES_PORTRAIT: Tuple[int, int] = (1080, 1920)

# Default Framerate
DEFAULT_FPS: int = 30

# UI Status Strings in Arabic
UI_MESSAGES_AR: Dict[str, str] = {
    "generating_frames": "جاري إنشاء الإطارات المرئية بجودة عالية...",
    "generating_title": "جاري تصميم شاشة العنوان الرئيسية...",
    "generating_transition": "جاري معالجة انتقال المشهد المرئي...",
    "invalid_resolution": "دقة الفيديو غير صالحة، يجب أن تكون أفقية أو عمودية",
    "section_rendered": "تم الانتهاء من رسم القسم المرئي {index} بنجاح",
}


@dataclass
class ColorTheme:
    """Defines the color palette and aesthetics for a video theme."""

    name: str
    bg_top: Tuple[int, int, int]
    bg_bottom: Tuple[int, int, int]
    primary_accent: Tuple[int, int, int]
    secondary_accent: Tuple[int, int, int]
    text_primary: Tuple[int, int, int]
    text_secondary: Tuple[int, int, int]
    card_bg: Tuple[int, int, int, int]
    card_border: Tuple[int, int, int, int]
    particle_colors: List[Tuple[int, int, int]]


# Curated Aesthetic Themes
THEMES: Dict[str, ColorTheme] = {
    "dark_tech": ColorTheme(
        name="dark_tech",
        bg_top=(10, 14, 23),
        bg_bottom=(18, 25, 44),
        primary_accent=(0, 229, 255),  # Cyber Cyan
        secondary_accent=(121, 40, 202),  # Neon Purple
        text_primary=(255, 255, 255),
        text_secondary=(160, 175, 200),
        card_bg=(16, 22, 36, 220),
        card_border=(0, 229, 255, 120),
        particle_colors=[(0, 229, 255), (121, 40, 202), (0, 180, 255)],
    ),
    "ocean_blue": ColorTheme(
        name="ocean_blue",
        bg_top=(3, 25, 38),
        bg_bottom=(13, 59, 102),
        primary_accent=(72, 202, 228),  # Azure Teal
        secondary_accent=(0, 119, 182),  # Deep Azure
        text_primary=(255, 255, 255),
        text_secondary=(180, 210, 230),
        card_bg=(8, 35, 60, 220),
        card_border=(72, 202, 228, 120),
        particle_colors=[(72, 202, 228), (0, 119, 182), (144, 224, 239)],
    ),
    "sunset": ColorTheme(
        name="sunset",
        bg_top=(43, 9, 56),
        bg_bottom=(92, 29, 74),
        primary_accent=(255, 123, 0),  # Solar Amber
        secondary_accent=(255, 0, 127),  # Vibrant Magenta
        text_primary=(255, 255, 255),
        text_secondary=(240, 190, 210),
        card_bg=(55, 15, 50, 220),
        card_border=(255, 123, 0, 120),
        particle_colors=[(255, 123, 0), (255, 0, 127), (255, 180, 50)],
    ),
    "nature_green": ColorTheme(
        name="nature_green",
        bg_top=(10, 28, 18),
        bg_bottom=(22, 59, 39),
        primary_accent=(46, 196, 182),  # Mint Turquoise
        secondary_accent=(87, 204, 153),  # Emerald Leaf
        text_primary=(255, 255, 255),
        text_secondary=(180, 220, 200),
        card_bg=(14, 42, 28, 220),
        card_border=(46, 196, 182, 120),
        particle_colors=[(46, 196, 182), (87, 204, 153), (203, 243, 240)],
    ),
    "space_purple": ColorTheme(
        name="space_purple",
        bg_top=(11, 0, 26),
        bg_bottom=(36, 0, 70),
        primary_accent=(157, 78, 221),  # Violet Flare
        secondary_accent=(224, 170, 255),  # Starlight Lavender
        text_primary=(255, 255, 255),
        text_secondary=(210, 190, 240),
        card_bg=(24, 6, 45, 220),
        card_border=(157, 78, 221, 120),
        particle_colors=[(157, 78, 221), (224, 170, 255), (123, 44, 191)],
    ),
}


@dataclass
class Particle:
    """Represents a floating geometric shape in the background."""

    x: float
    y: float
    vx: float
    vy: float
    radius: float
    shape: str  # "circle", "diamond", "hexagon", "star"
    color: Tuple[int, int, int]
    base_alpha: int
    pulse_freq: float


class VisualGenerator:
    """Generates motion typography, infographics, and styled frame sequences."""

    def __init__(self, fps: int = DEFAULT_FPS) -> None:
        """Initialize the visual generator.

        Args:
            fps: Video framerate (default: 30 fps).
        """
        self.fps = fps
        self._font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._bg_cache: Dict[Tuple[str, Tuple[int, int]], Image.Image] = {}
        self._find_system_fonts()

    def _find_system_fonts(self) -> None:
        """Discover available TrueType fonts on the host system."""
        bold_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]
        regular_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ]

        self.bold_font_path = None
        for p in bold_candidates:
            if os.path.exists(p):
                self.bold_font_path = p
                break

        self.regular_font_path = None
        for p in regular_candidates:
            if os.path.exists(p):
                self.regular_font_path = p
                break

    def _get_font(self, font_type: str, size: int) -> ImageFont.ImageFont:
        """Load and cache fonts with dynamic sizing and fallback."""
        key = (font_type, size)
        if key in self._font_cache:
            return self._font_cache[key]

        font_path = self.bold_font_path if font_type == "bold" else self.regular_font_path

        if font_path:
            try:
                font = ImageFont.truetype(font_path, size)
                self._font_cache[key] = font
                return font
            except Exception:
                pass

        try:
            return ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    def _parse_resolution(self, res: Union[str, Tuple[int, int]]) -> Tuple[int, int]:
        """Normalize resolution into standard (width, height) integers."""
        if isinstance(res, tuple) and len(res) == 2:
            return (int(res[0]), int(res[1]))

        if isinstance(res, str):
            res_lower = res.strip().lower()
            if res_lower in ("landscape", "16:9", "1080p"):
                return RES_LANDSCAPE
            if res_lower in ("portrait", "short", "shorts", "vertical", "9:16"):
                return RES_PORTRAIT
            match = re.match(r"^(\d+)[xX:](\d+)$", res_lower)
            if match:
                return (int(match.group(1)), int(match.group(2)))

        return RES_LANDSCAPE

    def _get_theme(self, style_name: Optional[str]) -> ColorTheme:
        """Fetch color theme by name with graceful default fallback."""
        if not style_name:
            return THEMES["dark_tech"]
        return THEMES.get(style_name.lower().strip(), THEMES["dark_tech"])

    def create_gradient_background(
        self,
        resolution: Tuple[int, int],
        theme: ColorTheme,
    ) -> Image.Image:
        """Create a smooth vertical gradient background using NumPy."""
        w, h = resolution
        cache_key = (theme.name, resolution)
        if cache_key in self._bg_cache:
            return self._bg_cache[cache_key].copy()

        y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
        c1 = np.array(theme.bg_top, dtype=np.float32)
        c2 = np.array(theme.bg_bottom, dtype=np.float32)
        gradient = (c1 * (1.0 - y) + c2 * y).astype(np.uint8)
        # Repeat column vector across width
        gradient_arr = np.repeat(gradient, w, axis=1)

        img = Image.fromarray(gradient_arr, mode="RGB")
        self._bg_cache[cache_key] = img.copy()
        return img

    def _init_particles(self, count: int, resolution: Tuple[int, int], theme: ColorTheme) -> List[Particle]:
        """Initialize floating geometric particles with deterministic randomness."""
        w, h = resolution
        random_gen = random.Random(42)
        particles: List[Particle] = []
        shapes = ["circle", "diamond", "hexagon", "star"]

        for _ in range(count):
            p = Particle(
                x=random_gen.uniform(0, w),
                y=random_gen.uniform(0, h),
                vx=random_gen.uniform(-0.5, 0.5),
                vy=random_gen.uniform(-0.8, -0.2),
                radius=random_gen.uniform(3.0, 9.0),
                shape=random_gen.choice(shapes),
                color=random_gen.choice(theme.particle_colors),
                base_alpha=random_gen.randint(40, 110),
                pulse_freq=random_gen.uniform(0.03, 0.08),
            )
            particles.append(p)
        return particles

    def _draw_particle(
        self,
        draw: ImageDraw.ImageDraw,
        p: Particle,
        frame_idx: int,
        resolution: Tuple[int, int],
    ) -> None:
        """Render a single animated particle shape with pulsing opacity."""
        w, h = resolution
        px = (p.x + p.vx * frame_idx) % w
        py = (p.y + p.vy * frame_idx) % h
        alpha = int(p.base_alpha + 30 * math.sin(frame_idx * p.pulse_freq))
        alpha = max(10, min(255, alpha))
        color = (*p.color, alpha)
        r = p.radius

        if p.shape == "circle":
            draw.ellipse([px - r, py - r, px + r, py + r], fill=color)
        elif p.shape == "diamond":
            points = [(px, py - r * 1.3), (px + r, py), (px, py + r * 1.3), (px - r, py)]
            draw.polygon(points, fill=color)
        elif p.shape == "hexagon":
            angles = [math.radians(a) for a in range(0, 360, 60)]
            points = [(px + r * math.cos(a), py + r * math.sin(a)) for a in angles]
            draw.polygon(points, fill=color)
        elif p.shape == "star":
            draw.line([(px - r, py), (px + r, py)], fill=color, width=2)
            draw.line([(px, py - r), (px, py + r)], fill=color, width=2)

    def _draw_icon(
        self,
        draw: ImageDraw.ImageDraw,
        icon_name: str,
        cx: int,
        cy: int,
        size: int,
        color: Tuple[int, int, int],
    ) -> None:
        """Draw clean geometric vector icons inside cards."""
        hs = size // 2
        name = icon_name.lower().strip() if icon_name else "chart"

        if name in ("chart", "stat", "trend"):
            # 3 Ascending Bar Chart + Trend Arrow
            b_w = size // 5
            draw.rectangle([cx - hs, cy + hs - int(size * 0.35), cx - hs + b_w, cy + hs], fill=color)
            draw.rectangle([cx - hs + int(b_w * 1.5), cy + hs - int(size * 0.65), cx - hs + int(b_w * 2.5), cy + hs], fill=color)
            draw.rectangle([cx - hs + b_w * 3, cy - hs, cx - hs + b_w * 4, cy + hs], fill=color)
            # Arrow
            draw.line([cx - hs, cy, cx + hs, cy - hs], fill=color, width=3)
            draw.polygon([(cx + hs, cy - hs), (cx + hs - 10, cy - hs), (cx + hs, cy - hs + 10)], fill=color)
        elif name in ("bulb", "fact", "idea"):
            # Lightbulb Circle + Base
            draw.ellipse([cx - int(hs * 0.7), cy - hs, cx + int(hs * 0.7), cy + int(hs * 0.4)], outline=color, width=4)
            draw.rectangle([cx - int(hs * 0.3), cy + int(hs * 0.4), cx + int(hs * 0.3), cy + int(hs * 0.8)], fill=color)
            # Filament rays
            draw.line([cx - hs, cy - int(hs * 0.3), cx - int(hs * 0.8), cy - int(hs * 0.3)], fill=color, width=3)
            draw.line([cx + int(hs * 0.8), cy - int(hs * 0.3), cx + hs, cy - int(hs * 0.3)], fill=color, width=3)
        elif name in ("rocket", "launch"):
            # Rocket fuselage
            fuselage = [(cx, cy - hs), (cx + int(hs * 0.6), cy + int(hs * 0.4)), (cx, cy + int(hs * 0.2)), (cx - int(hs * 0.6), cy + int(hs * 0.4))]
            draw.polygon(fuselage, fill=color)
            draw.polygon([(cx - 6, cy + int(hs * 0.3)), (cx + 6, cy + int(hs * 0.3)), (cx, cy + hs)], fill=(255, 120, 0))
        elif name in ("quote", "text"):
            # Stylized twin quotation marks
            draw.rectangle([cx - int(hs * 0.8), cy - int(hs * 0.5), cx - int(hs * 0.2), cy + int(hs * 0.2)], fill=color)
            draw.polygon([(cx - int(hs * 0.8), cy + int(hs * 0.2)), (cx - int(hs * 0.2), cy + int(hs * 0.2)), (cx - int(hs * 0.6), cy + hs)], fill=color)
            draw.rectangle([cx + int(hs * 0.2), cy - int(hs * 0.5), cx + int(hs * 0.8), cy + int(hs * 0.2)], fill=color)
            draw.polygon([(cx + int(hs * 0.2), cy + int(hs * 0.2)), (cx + int(hs * 0.8), cy + int(hs * 0.2)), (cx + int(hs * 0.4), cy + hs)], fill=color)
        else:
            # Diamond target
            draw.polygon([(cx, cy - hs), (cx + hs, cy), (cx, cy + hs), (cx - hs, cy)], outline=color, width=4)
            draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=color)

    def _wrap_text(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> List[str]:
        """Wrap text cleanly based on measured pixel width."""
        words = text.split()
        if not words:
            return []

        lines: List[str] = []
        current_line: List[str] = []

        for word in words:
            test_line = " ".join(current_line + [word])
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]

        if current_line:
            lines.append(" ".join(current_line))
        return lines

    def _draw_rounded_card(
        self,
        img: Image.Image,
        bbox: Tuple[int, int, int, int],
        bg_color: Tuple[int, int, int, int],
        border_color: Tuple[int, int, int, int],
        radius: int = 24,
    ) -> None:
        """Draw an alpha-blended glassmorphism card on the image."""
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        draw_ov.rounded_rectangle(bbox, radius=radius, fill=bg_color, outline=border_color, width=2)
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

    def generate_title_card(
        self,
        title: str,
        subtitle: str = "",
        resolution: Union[str, Tuple[int, int]] = RES_LANDSCAPE,
        style: str = "dark_tech",
        output_path: Optional[str] = None,
    ) -> str:
        """Create an impactful opening title card image.

        Args:
            title: Headline text to display prominently.
            subtitle: Supporting secondary tagline.
            resolution: Dimensions (landscape 1920x1080 or portrait 1080x1920).
            style: Color theme key.
            output_path: Optional destination path; generates temp path if None.

        Returns:
            Absolute path to the rendered title card image.
        """
        res = self._parse_resolution(resolution)
        theme = self._get_theme(style)
        w, h = res
        is_portrait = h > w

        bg = self.create_gradient_background(res, theme)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Draw decorative background grid and particles
        particles = self._init_particles(30, res, theme)
        for p in particles:
            self._draw_particle(draw, p, 15, res)

        # Scale fonts according to aspect ratio
        title_size = 64 if is_portrait else 72
        sub_size = 32 if is_portrait else 36
        badge_size = 22 if is_portrait else 24

        font_title = self._get_font("bold", title_size)
        font_sub = self._get_font("regular", sub_size)
        font_badge = self._get_font("bold", badge_size)

        # Glassmorphic Main Card
        card_margin_x = int(w * 0.08)
        card_margin_y = int(h * 0.16) if is_portrait else int(h * 0.14)
        card_box = (card_margin_x, card_margin_y, w - card_margin_x, h - card_margin_y)
        draw.rounded_rectangle(card_box, radius=32, fill=theme.card_bg, outline=theme.card_border, width=3)

        # Top Category Tag / Badge
        badge_text = "AI AUTOPILOT • SPECIAL REPORT"
        badge_bbox = font_badge.getbbox(badge_text)
        badge_w = badge_bbox[2] - badge_bbox[0] + 32
        badge_h = badge_bbox[3] - badge_bbox[1] + 16
        badge_x = (w - badge_w) // 2
        badge_y = card_margin_y + 45
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            radius=12,
            fill=(*theme.primary_accent, 40),
            outline=(*theme.primary_accent, 180),
            width=2,
        )
        draw.text((badge_x + 16, badge_y + 7), badge_text, fill=theme.primary_accent, font=font_badge)

        # Center Title Text
        max_text_width = card_box[2] - card_box[0] - 80
        wrapped_title = self._wrap_text(title, font_title, max_text_width)
        title_line_height = int(title_size * 1.3)
        total_title_h = len(wrapped_title) * title_line_height

        start_title_y = (card_box[1] + card_box[3] - total_title_h) // 2 - 20
        for i, line in enumerate(wrapped_title):
            bbox = font_title.getbbox(line)
            lw = bbox[2] - bbox[0]
            lx = (w - lw) // 2
            ly = start_title_y + i * title_line_height
            # Drop shadow
            draw.text((lx + 3, ly + 3), line, fill=(0, 0, 0, 160), font=font_title)
            draw.text((lx, ly), line, fill=theme.text_primary, font=font_title)

        # Subtitle
        if subtitle:
            wrapped_sub = self._wrap_text(subtitle, font_sub, max_text_width)
            sub_start_y = start_title_y + total_title_h + 35
            for i, s_line in enumerate(wrapped_sub):
                s_bbox = font_sub.getbbox(s_line)
                slw = s_bbox[2] - s_bbox[0]
                slx = (w - slw) // 2
                sly = sub_start_y + i * int(sub_size * 1.3)
                draw.text((slx, sly), s_line, fill=theme.text_secondary, font=font_sub)

        # Subtle Glowing Accent Line at Card Bottom
        accent_line_y = card_box[3] - 40
        draw.line([(card_box[0] + 60, accent_line_y), (card_box[2] - 60, accent_line_y)], fill=(*theme.primary_accent, 160), width=3)

        # Merge layers
        final_img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

        if not output_path:
            temp_fd, output_path = tempfile.mkstemp(prefix="title_card_", suffix=".png")
            os.close(temp_fd)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        final_img.save(output_path, "PNG")
        return os.path.abspath(output_path)

    def generate_transition(
        self,
        transition_type: str = "fade",
        resolution: Union[str, Tuple[int, int]] = RES_LANDSCAPE,
        style: str = "dark_tech",
        frame_count: int = 15,
        output_dir: Optional[str] = None,
    ) -> List[str]:
        """Generate a series of transition frames between video sections.

        Supports transition types: 'fade', 'wipe', 'zoom', 'slide'.

        Args:
            transition_type: Transition animation style.
            resolution: Frame resolution tuple or name.
            style: Color theme key.
            frame_count: Number of transition frames (15 frames = 0.5s at 30fps).
            output_dir: Target directory to save PNG frames.

        Returns:
            List of generated frame file paths in sequential order.
        """
        res = self._parse_resolution(resolution)
        theme = self._get_theme(style)
        w, h = res
        ttype = transition_type.lower().strip()

        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="transition_frames_")
        os.makedirs(output_dir, exist_ok=True)

        base_bg = self.create_gradient_background(res, theme)
        frame_paths: List[str] = []

        for i in range(frame_count):
            t = (i + 1) / float(frame_count)  # Progress from 0.0 to 1.0
            frame = base_bg.copy()
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            if ttype == "wipe":
                # Horizontal neon wipe bar with trailing fade
                wipe_x = int(w * t)
                draw.rectangle([(0, 0), (wipe_x, h)], fill=(*theme.bg_bottom, int(220 * t)))
                draw.line([(wipe_x, 0), (wipe_x, h)], fill=theme.primary_accent, width=8)
            elif ttype == "slide":
                # Diagonal accent sweep
                offset = int((w + h) * t)
                pts = [(0, 0), (min(w, offset), 0), (0, min(h, offset))]
                draw.polygon(pts, fill=(*theme.primary_accent, int(150 * (1.0 - t))))
            elif ttype == "zoom":
                # Radial expanding pulse ring
                radius = int((max(w, h) * 0.75) * t)
                cx, cy = w // 2, h // 2
                draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=(*theme.primary_accent, int(200 * (1.0 - t))), width=12)
            else:
                # Smooth Fade to Theme Dark Shade
                fade_alpha = int(255 * math.sin(t * math.pi))
                draw.rectangle([(0, 0), (w, h)], fill=(*theme.bg_top, fade_alpha))

            comp = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
            frame_file = os.path.join(output_dir, f"trans_{i:04d}.png")
            comp.save(frame_file, "PNG")
            frame_paths.append(frame_file)

        return frame_paths

    def _render_single_frame(
        self,
        frame_idx: int,
        total_section_frames: int,
        global_progress: float,
        section_data: Dict[str, Any],
        resolution: Tuple[int, int],
        theme: ColorTheme,
        particles: List[Particle],
    ) -> Image.Image:
        """Render a single frame composed of text, stats, icons, and progress bars."""
        w, h = resolution
        is_portrait = h > w

        # Create gradient base
        base_img = self.create_gradient_background(resolution, theme)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Draw animated geometric particles
        for p in particles:
            self._draw_particle(draw, p, frame_idx, resolution)

        # Content Card Placement
        if is_portrait:
            card_x1 = int(w * 0.08)
            card_x2 = int(w * 0.92)
            card_y1 = int(h * 0.18)  # Leave room for Shorts top header
            card_y2 = int(h * 0.78)  # Leave room for Shorts bottom buttons/captions
        else:
            card_x1 = int(w * 0.10)
            card_x2 = int(w * 0.90)
            card_y1 = int(h * 0.12)
            card_y2 = int(h * 0.88)

        card_w = card_x2 - card_x1
        card_h = card_y2 - card_y1

        # Render Glass Card
        draw.rounded_rectangle(
            (card_x1, card_y1, card_x2, card_y2),
            radius=28,
            fill=theme.card_bg,
            outline=theme.card_border,
            width=2,
        )

        # Section Metadata
        title = section_data.get("title", "")
        body = section_data.get("body", "")
        sec_type = section_data.get("type", "fact").lower()
        stat_val = section_data.get("stat_value", "")
        stat_lbl = section_data.get("stat_label", "")
        icon_name = section_data.get("icon", sec_type)

        # Dynamic Fonts based on resolution
        font_badge = self._get_font("bold", 22 if is_portrait else 24)
        font_title = self._get_font("bold", 44 if is_portrait else 52)
        font_body = self._get_font("regular", 28 if is_portrait else 34)
        font_stat = self._get_font("bold", 72 if is_portrait else 88)
        font_stat_lbl = self._get_font("bold", 26 if is_portrait else 30)

        # 1. Badge Pill with Section Type & Icon
        badge_tag = f"• {sec_type.upper()} •"
        draw.rounded_rectangle(
            (card_x1 + 36, card_y1 + 30, card_x1 + 220, card_y1 + 68),
            radius=10,
            fill=(*theme.primary_accent, 45),
            outline=(*theme.primary_accent, 160),
            width=1,
        )
        draw.text((card_x1 + 54, card_y1 + 36), badge_tag, fill=theme.primary_accent, font=font_badge)

        # 2. Section Icon
        icon_cx = card_x2 - 60
        icon_cy = card_y1 + 50
        self._draw_icon(draw, icon_name, icon_cx, icon_cy, size=36, color=theme.primary_accent)

        # 3. Section Title (with subtle slide-in during first 15 frames)
        slide_offset = max(0, int(20 * (1.0 - min(1.0, frame_idx / 12.0))))
        wrapped_title = self._wrap_text(title, font_title, card_w - 70)
        title_y = card_y1 + 95 + slide_offset

        for i, line in enumerate(wrapped_title[:2]):  # Maximum 2 lines for title
            draw.text((card_x1 + 38, title_y + i * int(font_title.size * 1.25)), line, fill=theme.text_primary, font=font_title)

        cur_y = title_y + len(wrapped_title[:2]) * int(font_title.size * 1.25) + 25

        # 4. Animated Stat / Fact Display
        if stat_val:
            # Animate numeric progress for numbers
            display_stat = stat_val
            num_match = re.match(r"^([^\d]*)(\d+)(.*)$", stat_val)
            if num_match:
                prefix, num_str, suffix = num_match.groups()
                target_num = int(num_str)
                count_ratio = min(1.0, frame_idx / float(max(1, min(30, total_section_frames))))
                animated_num = int(target_num * count_ratio)
                display_stat = f"{prefix}{animated_num}{suffix}"

            # Stat Box Container
            stat_box_y = cur_y + 10
            stat_box_h = 120 if is_portrait else 135
            draw.rounded_rectangle(
                (card_x1 + 35, stat_box_y, card_x2 - 35, stat_box_y + stat_box_h),
                radius=18,
                fill=(*theme.secondary_accent, 35),
                outline=(*theme.secondary_accent, 140),
                width=2,
            )
            # Big Stat Number
            draw.text((card_x1 + 55, stat_box_y + 15), display_stat, fill=theme.primary_accent, font=font_stat)
            # Stat Label / Description
            if stat_lbl:
                draw.text(
                    (card_x1 + 58, stat_box_y + 15 + int(font_stat.size * 1.05)),
                    stat_lbl.upper(),
                    fill=theme.text_secondary,
                    font=font_stat_lbl,
                )
            cur_y = stat_box_y + stat_box_h + 30

        # 5. Explanatory Narration Body Text
        if body:
            max_body_lines = 4 if is_portrait else 5
            wrapped_body = self._wrap_text(body, font_body, card_w - 70)
            body_line_h = int(font_body.size * 1.35)

            for i, bline in enumerate(wrapped_body[:max_body_lines]):
                draw.text((card_x1 + 38, cur_y + i * body_line_h), bline, fill=theme.text_secondary, font=font_body)

        # 6. Animated Progress Bar at Bottom of Card
        section_progress = min(1.0, max(0.0, frame_idx / float(max(1, total_section_frames))))
        bar_x1 = card_x1 + 35
        bar_x2 = card_x2 - 35
        bar_y = card_y2 - 35
        bar_width = bar_x2 - bar_x1

        # Background track
        draw.rounded_rectangle((bar_x1, bar_y, bar_x2, bar_y + 10), radius=5, fill=(*theme.primary_accent, 40))

        # Filled active progress
        fill_x2 = bar_x1 + int(bar_width * section_progress)
        if fill_x2 > bar_x1:
            draw.rounded_rectangle((bar_x1, bar_y, fill_x2, bar_y + 10), radius=5, fill=theme.primary_accent)
            # Glowing indicator dot at leading edge
            draw.ellipse([fill_x2 - 6, bar_y - 3, fill_x2 + 6, bar_y + 13], fill=theme.primary_accent)

        # Global Video Progress Indicator at Screen Edge
        global_bar_y = h - 6
        draw.rectangle([(0, global_bar_y), (int(w * global_progress), h)], fill=theme.primary_accent)

        # Merge base gradient with rendered overlay
        return Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")

    def generate_frames(
        self,
        script_sections: List[Dict[str, Any]],
        resolution: Union[str, Tuple[int, int]] = RES_LANDSCAPE,
        style: str = "dark_tech",
        output_dir: Optional[str] = None,
    ) -> List[str]:
        """Produce full video frame sequences matching the narrative sections.

        Generates at 30 fps with smooth typography, animated progress indicators,
        and continuous geometric particles.

        Args:
            script_sections: List of section dicts (title, body, duration, etc.).
            resolution: Landscape (1920x1080) or Portrait (1080x1920).
            style: Color scheme name.
            output_dir: Destination folder for output PNG files.

        Returns:
            List of generated absolute file paths for every video frame.
        """
        res = self._parse_resolution(resolution)
        theme = self._get_theme(style)

        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="video_render_frames_")
        os.makedirs(output_dir, exist_ok=True)

        particles = self._init_particles(35, res, theme)
        frame_paths: List[str] = []

        # Determine total duration across all sections
        total_seconds = sum(sec.get("duration", 4.0) for sec in script_sections)
        total_video_frames = max(1, int(total_seconds * self.fps))
        current_global_frame = 0

        frame_counter = 1
        for sec_idx, section in enumerate(script_sections):
            duration_sec = float(section.get("duration", 4.0))
            section_frame_count = max(1, int(duration_sec * self.fps))

            for f_idx in range(section_frame_count):
                global_progress = min(1.0, current_global_frame / float(total_video_frames))
                frame_img = self._render_single_frame(
                    frame_idx=f_idx,
                    total_section_frames=section_frame_count,
                    global_progress=global_progress,
                    section_data=section,
                    resolution=res,
                    theme=theme,
                    particles=particles,
                )

                frame_file = os.path.join(output_dir, f"frame_{frame_counter:06d}.png")
                frame_img.save(frame_file, "PNG")
                frame_paths.append(frame_file)

                frame_counter += 1
                current_global_frame += 1

        return frame_paths
