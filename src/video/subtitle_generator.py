"""Subtitle Generator for YouTube AI Autopilot.

Generates precise SRT subtitle tracks from script text and audio duration using
speech-rate word timing estimation, safe text wrapping (max 42 characters/line,
max 2 lines/block), and builds FFmpeg commands to hardcode/burn subtitles with
safe margin positioning for landscape (10% bottom margin) and portrait/Shorts (20% bottom margin).
"""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Subtitle Layout & Formatting Constraints
MAX_CHARS_PER_LINE: int = 42
MAX_LINES_PER_BLOCK: int = 2
MIN_BLOCK_DURATION: float = 0.8
MAX_BLOCK_DURATION: float = 4.2

# UI Status Strings in Arabic
UI_MESSAGES_AR: Dict[str, str] = {
    "generating_srt": "جاري حساب توقيت الكلمات وإنشاء ملف الترجمة (SRT)...",
    "burn_command_ready": "تم تجهيز أمر FFmpeg لدمج الترجمة مع الفيديو",
    "error_empty_script": "نص الاسكريبت فارغ، تعذر توليد ملف الترجمة",
    "error_invalid_duration": "مدة الصوت غير صالحة، يجب أن تكون أكبر من الصفر",
    "error_srt_not_found": "ملف الترجمة المحدد غير موجود على القرص",
}


@dataclass
class WordTiming:
    """Represents a tokenized word with estimated start and end timestamps."""

    word: str
    start: float
    end: float
    pause_after: float = 0.0


@dataclass
class SubtitleBlock:
    """Represents a formatted subtitle segment for SRT export."""

    index: int
    start_time: float
    end_time: float
    lines: List[str]

    @property
    def text(self) -> str:
        """Formatted subtitle content joined with newlines."""
        return "\n".join(self.lines)


class SubtitleGenerator:
    """Generates SRT files and FFmpeg subtitle burning commands."""

    def __init__(self, default_font: str = "DejaVu Sans") -> None:
        """Initialize the subtitle generator.

        Args:
            default_font: Name of the TrueType font for subtitle rendering.
        """
        self.default_font = default_font

    def _parse_resolution(self, res: Union[str, Tuple[int, int]]) -> Tuple[int, int]:
        """Normalize resolution input into (width, height)."""
        if isinstance(res, tuple) and len(res) == 2:
            return (int(res[0]), int(res[1]))

        if isinstance(res, str):
            res_lower = res.strip().lower()
            if res_lower in ("landscape", "16:9", "1080p"):
                return (1920, 1080)
            if res_lower in ("portrait", "short", "shorts", "vertical", "9:16"):
                return (1080, 1920)
            match = re.match(r"^(\d+)[xX:](\d+)$", res_lower)
            if match:
                return (int(match.group(1)), int(match.group(2)))

        return (1920, 1080)

    @staticmethod
    def _format_srt_timestamp(seconds: float) -> str:
        """Format seconds into standard SRT timestamp HH:MM:SS,mmm."""
        total_ms = max(0, int(round(seconds * 1000.0)))
        ms = total_ms % 1000
        total_sec = total_ms // 1000
        sec = total_sec % 60
        total_min = total_sec // 60
        minute = total_min % 60
        hour = total_min // 60
        return f"{hour:02d}:{minute:02d}:{sec:02d},{ms:03d}"

    def _estimate_word_timings(self, script_text: str, total_duration: float) -> List[WordTiming]:
        """Estimate timing of individual words based on character length and punctuation pauses.

        Args:
            script_text: Full narration script.
            total_duration: Actual measured audio duration in seconds.

        Returns:
            List of WordTiming instances matching the total duration.
        """
        clean_text = re.sub(r"\s+", " ", script_text).strip()
        raw_words = clean_text.split()
        if not raw_words:
            return []

        # Calculate relative weights based on word length and punctuation pauses
        weights: List[float] = []
        pauses: List[float] = []

        for w in raw_words:
            base_weight = max(1, len(w))
            pause_weight = 0.0

            if w.endswith((".", "!", "?", "...", ":")):
                pause_weight = 3.5  # Significant sentence pause
            elif w.endswith((",", ";", "-", "—")):
                pause_weight = 1.8  # Clause breathing pause

            weights.append(float(base_weight))
            pauses.append(pause_weight)

        total_weight = sum(weights) + sum(pauses)
        if total_weight <= 0:
            total_weight = 1.0

        time_per_weight_unit = total_duration / total_weight

        timings: List[WordTiming] = []
        current_time = 0.0

        for word, w_weight, p_weight in zip(raw_words, weights, pauses):
            word_duration = w_weight * time_per_weight_unit
            pause_duration = p_weight * time_per_weight_unit
            start_t = current_time
            end_t = start_t + word_duration

            timings.append(
                WordTiming(
                    word=word,
                    start=start_t,
                    end=end_t,
                    pause_after=pause_duration,
                )
            )
            current_time = end_t + pause_duration

        return timings

    def _group_into_blocks(self, timings: List[WordTiming]) -> List[SubtitleBlock]:
        """Group word timings into balanced 1-2 line subtitle blocks.

        Enforces MAX_CHARS_PER_LINE (42 characters) and MAX_LINES_PER_BLOCK (2 lines).
        """
        if not timings:
            return []

        blocks: List[SubtitleBlock] = []
        cur_words: List[WordTiming] = []
        block_idx = 1

        def build_lines(words: List[WordTiming]) -> List[str]:
            full_str = " ".join(wt.word for wt in words)
            if len(full_str) <= MAX_CHARS_PER_LINE:
                return [full_str]

            # Split into 2 balanced lines
            token_strs = [wt.word for wt in words]
            best_split = len(token_strs) // 2
            best_diff = 9999

            for i in range(1, len(token_strs)):
                line1 = " ".join(token_strs[:i])
                line2 = " ".join(token_strs[i:])
                if len(line1) <= MAX_CHARS_PER_LINE and len(line2) <= MAX_CHARS_PER_LINE:
                    diff = abs(len(line1) - len(line2))
                    if diff < best_diff:
                        best_diff = diff
                        best_split = i

            line1 = " ".join(token_strs[:best_split])
            line2 = " ".join(token_strs[best_split:])
            return [line1, line2]

        for wt in timings:
            cur_words.append(wt)
            current_full_text = " ".join(w.word for w in cur_words)

            # Check if block capacity reached
            is_sentence_end = wt.word.endswith((".", "!", "?"))
            max_char_capacity = MAX_CHARS_PER_LINE * MAX_LINES_PER_BLOCK - 4
            exceeds_capacity = len(current_full_text) >= max_char_capacity
            long_enough = len(cur_words) >= 5 or len(current_full_text) >= 32

            if (is_sentence_end and long_enough) or exceeds_capacity:
                start_t = cur_words[0].start
                end_t = max(start_t + MIN_BLOCK_DURATION, cur_words[-1].end)
                lines = build_lines(cur_words)

                blocks.append(
                    SubtitleBlock(
                        index=block_idx,
                        start_time=start_t,
                        end_time=end_t,
                        lines=lines,
                    )
                )
                block_idx += 1
                cur_words = []

        if cur_words:
            start_t = cur_words[0].start
            end_t = max(start_t + MIN_BLOCK_DURATION, cur_words[-1].end)
            lines = build_lines(cur_words)
            blocks.append(
                SubtitleBlock(
                    index=block_idx,
                    start_time=start_t,
                    end_time=end_t,
                    lines=lines,
                )
            )

        # Prevent overlapping start/end timestamps between consecutive blocks
        for i in range(len(blocks) - 1):
            if blocks[i].end_time > blocks[i + 1].start_time:
                blocks[i].end_time = max(blocks[i].start_time + 0.5, blocks[i + 1].start_time - 0.05)

        return blocks

    def generate_srt(
        self,
        script_text: str,
        total_duration: float,
        output_path: str,
    ) -> str:
        """Generate a valid SRT subtitle file from narration script text.

        Args:
            script_text: Narration script text.
            total_duration: Audio duration in seconds.
            output_path: Target .srt file path.

        Returns:
            Absolute path to the saved SRT file.

        Raises:
            ValueError: If script is empty or duration is invalid.
        """
        if not script_text or not script_text.strip():
            raise ValueError(UI_MESSAGES_AR["error_empty_script"])
        if total_duration <= 0.0:
            raise ValueError(UI_MESSAGES_AR["error_invalid_duration"])

        abs_out = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(abs_out), exist_ok=True)

        timings = self._estimate_word_timings(script_text, total_duration)
        blocks = self._group_into_blocks(timings)

        with open(abs_out, "w", encoding="utf-8") as srt_file:
            for b in blocks:
                start_str = self._format_srt_timestamp(b.start_time)
                end_str = self._format_srt_timestamp(b.end_time)
                srt_file.write(f"{b.index}\n")
                srt_file.write(f"{start_str} --> {end_str}\n")
                srt_file.write(f"{b.text}\n\n")

        return abs_out

    def burn_subtitles_command(
        self,
        video_path: str,
        srt_path: str,
        output_path: str,
        resolution: Union[str, Tuple[int, int]] = (1920, 1080),
    ) -> List[str]:
        """Construct an FFmpeg command to hardcode/burn subtitles into the video.

        Applies white text with high-contrast black outline, centered alignment,
        and safe bottom margins (10% for landscape, 20% for Shorts).

        Args:
            video_path: Path to source MP4 video.
            srt_path: Path to SRT subtitle file.
            output_path: Destination path for burned video.
            resolution: Dimensions (width, height) or keyword.

        Returns:
            List of FFmpeg command arguments ready for subprocess execution.
        """
        res = self._parse_resolution(resolution)
        w, h = res
        is_portrait = h > w

        # Safe Margins & Typography Rules
        if is_portrait:
            # 20% safe margin from bottom for YouTube Shorts UI overlays
            font_size = 32
            margin_v = int(h * 0.20)  # ~384px on 1080x1920
        else:
            # 10% safe margin from bottom for standard landscape videos
            font_size = 24
            margin_v = int(h * 0.10)  # ~108px on 1920x1080

        # Escape SRT path for FFmpeg subtitles filter syntax
        escaped_srt = os.path.abspath(srt_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

        # ASS Style specifications:
        # PrimaryColour=&H00FFFFFF& (White text)
        # OutlineColour=&H00000000& (Black border)
        # BorderStyle=1 (Outline + shadow)
        # Outline=2.5, Shadow=1
        # Alignment=2 (Bottom-center)
        style_spec = (
            f"FontName={self.default_font},"
            f"FontSize={font_size},"
            f"PrimaryColour=&H00FFFFFF&,"
            f"OutlineColour=&H00000000&,"
            f"BorderStyle=1,"
            f"Outline=2.5,"
            f"Shadow=1,"
            f"Alignment=2,"
            f"MarginV={margin_v}"
        )

        subtitles_filter = f"subtitles='{escaped_srt}':force_style='{style_spec}'"

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            subtitles_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "copy",
            str(output_path),
        ]

        return command
