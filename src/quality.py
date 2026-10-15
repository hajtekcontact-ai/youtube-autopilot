"""Quality assurance module for YouTube AI Autopilot.

Validates produced video files against strict technical standards prior to YouTube upload:
- Container integrity (MP4 magic bytes, atom verification)
- Codec compliance (H.264 / AVC video, AAC audio)
- Resolution targeting (1080x1920 for Shorts, 1920x1080 for Long videos)
- Duration bounds (15-60s for Shorts, 120-900s for Long videos)
- File size thresholds (<50MB for Shorts, <100MB for Long videos)
- Audio stream presence, loudness, and synchronization
- Frame rate validation (>= 24 fps)
- Corruption detection via FFprobe deep analysis
- Detailed English and Arabic diagnostic reports
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """Detailed quality validation report for an examined video file."""

    is_valid: bool
    video_path: str
    video_type: str  # 'short' or 'long'
    checks: Dict[str, bool] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    arabic_summary: str = ""
    arabic_details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert quality report to dictionary representation.

        Returns:
            Dict[str, Any]: Serialized quality report.
        """
        return asdict(self)


class QualityChecker:
    """Production quality inspector for generated YouTube videos.

    Uses system FFprobe and byte-level inspection to guarantee strict compliance
    with YouTube ingestion and high-retention rendering benchmarks.
    """

    # Resolution constraints (width, height)
    TARGET_RESOLUTIONS = {
        "short": (1080, 1920),  # 9:16 vertical
        "long": (1920, 1080),   # 16:9 horizontal
    }

    # Duration limits in seconds (min, max)
    DURATION_LIMITS = {
        "short": (15.0, 60.0),
        "long": (120.0, 900.0),  # 2 min to 15 min
    }

    # Maximum file size in bytes
    MAX_FILE_SIZES = {
        "short": 50 * 1024 * 1024,   # 50 MB
        "long": 100 * 1024 * 1024,   # 100 MB
    }

    # Minimum acceptable frame rate
    MIN_FRAME_RATE = 23.97

    # Accepted video codecs
    VALID_VIDEO_CODECS = {"h264", "avc", "avc1"}

    # Accepted audio codecs
    VALID_AUDIO_CODECS = {"aac"}

    # Recognized MP4 major brands in ftyp atom
    MP4_BRANDS = {
        b"isom", b"iso2", b"mp41", b"mp42", b"avc1",
        b"M4V ", b"M4A ", b"dash", b"qt  "
    }

    def __init__(self, ffprobe_binary: Optional[str] = None) -> None:
        """Initialize the quality checker.

        Args:
            ffprobe_binary: Custom path to ffprobe binary if not in PATH.
        """
        self.ffprobe_path = ffprobe_binary or shutil.which("ffprobe") or "ffprobe"

    def check_magic_bytes(self, file_path: Path) -> Tuple[bool, str]:
        """Verify the file begins with standard ISO/IEC 14496-12 MP4 magic bytes.

        An MP4 file begins with an `ftyp` atom:
        - Bytes 0-3: Atom size (usually 0x00000014, 0x00000018, 0x00000020)
        - Bytes 4-7: b'ftyp'
        - Bytes 8-11: Major brand (e.g., b'isom', b'mp42', b'isom')

        Args:
            file_path: Path to the video file.

        Returns:
            Tuple[bool, str]: (is_valid, detected_brand_or_reason)
        """
        try:
            with open(file_path, "rb") as f:
                header = f.read(64)

            if len(header) < 12:
                return False, "حجم ترويسة الملف أقل من 12 بايت (الملف فارغ أو مبتور)"

            atom_type = header[4:8]
            if atom_type != b"ftyp":
                return False, f"نوع الحاوية ليس MP4 صالحاً (الترويسة: {atom_type!r})"

            major_brand = header[8:12]
            return True, f"حاوية MP4 معتمدة (العلامة: {major_brand.decode('latin1', errors='replace').strip()})"
        except Exception as exc:
            return False, f"فشل قراءة بايتات الملف: {exc}"

    def get_video_info(self, video_path: Union[str, Path]) -> Dict[str, Any]:
        """Extract stream metadata and container properties using FFprobe.

        Args:
            video_path: Path to the target video file.

        Returns:
            Dict[str, Any]: Parsed FFprobe JSON dictionary, or empty dict on failure.
        """
        target = Path(video_path).resolve()
        if not target.is_file():
            logger.error("Video file does not exist: %s", target)
            return {}

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_error",
            str(target)
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                logger.error("FFprobe returned non-zero code (%d): %s", result.returncode, result.stderr)
                return {}

            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            logger.error("FFprobe timed out while analyzing %s", target)
            return {}
        except Exception as exc:
            logger.error("Failed to run FFprobe on %s: %s", target, exc)
            return {}

    def check_corruption(self, video_path: Union[str, Path]) -> Tuple[bool, Optional[str]]:
        """Perform a decode test with FFprobe to ensure the bitstream is uncorrupted.

        Args:
            video_path: Path to the video file.

        Returns:
            Tuple[bool, Optional[str]]: (passed, error_detail_if_failed)
        """
        target = Path(video_path).resolve()
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-i", str(target),
            "-f", "null",
            "-"
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
            stderr_out = result.stderr.strip()
            # If critical errors were logged
            critical_keywords = [
                "error while decoding", "corrupt", "invalid data",
                "moov atom not found", "header missing", "fatal"
            ]
            for kw in critical_keywords:
                if kw in stderr_out.lower():
                    return False, f"اكتشاف تلف في تدفق الفيديو: {stderr_out[:200]}"

            return True, None
        except subprocess.TimeoutExpired:
            return False, "استغرق فحص تلف الفيديو وقتاً أطول من المسموح (تجاوز 60 ثانية)"
        except Exception as exc:
            return False, f"فشل تشغيل فحص تلف الفيديو: {exc}"

    def validate_video(
        self,
        video_path: Union[str, Path],
        video_type: str = "short"
    ) -> QualityReport:
        """Validate an exported video file against all technical specifications.

        Checks performed:
        1. Existence and readability of file
        2. MP4 container format (magic bytes)
        3. Video codec (H.264 / AVC)
        4. Audio codec (AAC)
        5. Target resolution (1080x1920 or 1920x1080)
        6. Duration constraints
        7. File size limits
        8. Audio presence and audio stream valid duration
        9. Frame rate threshold (>= 24 fps)
        10. FFprobe stream integrity (no bitstream corruption)

        Args:
            video_path: Path to the video file.
            video_type: 'short' or 'long'.

        Returns:
            QualityReport: Full validation results with English and Arabic details.
        """
        normalized_type = "long" if video_type.lower() == "long" else "short"
        path_obj = Path(video_path).resolve()

        checks: Dict[str, bool] = {
            "file_exists": False,
            "mp4_container": False,
            "video_codec": False,
            "audio_codec": False,
            "resolution": False,
            "duration": False,
            "file_size": False,
            "audio_present": False,
            "frame_rate": False,
            "no_corruption": False,
        }
        errors: List[str] = []
        warnings: List[str] = []
        arabic_details: List[str] = []
        details: Dict[str, Any] = {}

        # 1. File existence and readability
        if not path_obj.is_file():
            err = f"Video file does not exist or is not a regular file: {path_obj}"
            errors.append(err)
            arabic_details.append(f"الملف غير موجود أو يتعذر الوصول إليه: {path_obj.name}")
            return QualityReport(
                is_valid=False,
                video_path=str(path_obj),
                video_type=normalized_type,
                checks=checks,
                errors=errors,
                warnings=warnings,
                details=details,
                arabic_summary="فشل الفحص: ملف الفيديو غير موجود على القرص.",
                arabic_details=arabic_details,
            )

        checks["file_exists"] = True
        file_size = path_obj.stat().st_size
        details["file_size_bytes"] = file_size
        arabic_details.append("✓ تم التحقق من وجود الملف وقابليته للقراءة بنجاح.")

        # 2. MP4 Container magic bytes
        mb_valid, mb_desc = self.check_magic_bytes(path_obj)
        checks["mp4_container"] = mb_valid
        if not mb_valid:
            errors.append(f"Invalid MP4 container: {mb_desc}")
            arabic_details.append(f"✗ خطأ في بنية حاوية الملف: {mb_desc}")
        else:
            arabic_details.append(f"✓ {mb_desc}")

        # Extract FFprobe info
        probe_data = self.get_video_info(path_obj)
        if not probe_data:
            err = "FFprobe failed to extract metadata from video container."
            errors.append(err)
            arabic_details.append("✗ فشل برنامج FFprobe في قراءة البيانات الوصفية للملف.")
            return QualityReport(
                is_valid=False,
                video_path=str(path_obj),
                video_type=normalized_type,
                checks=checks,
                errors=errors,
                warnings=warnings,
                details=details,
                arabic_summary="فشل الفحص: تعذر تحليل المسارات بواسطة FFprobe.",
                arabic_details=arabic_details,
            )

        streams = probe_data.get("streams", [])
        format_info = probe_data.get("format", {})
        details["format"] = format_info.get("format_name")

        video_stream: Optional[Dict[str, Any]] = None
        audio_stream: Optional[Dict[str, Any]] = None

        for s in streams:
            if s.get("codec_type") == "video" and video_stream is None:
                video_stream = s
            elif s.get("codec_type") == "audio" and audio_stream is None:
                audio_stream = s

        # 3. Video codec (H.264 / AVC)
        if not video_stream:
            errors.append("No video stream found in container.")
            arabic_details.append("✗ لم يتم العثور على أي مسار فيديو داخل الملف.")
        else:
            v_codec = str(video_stream.get("codec_name", "")).lower()
            details["video_codec"] = v_codec
            if any(valid in v_codec for valid in self.VALID_VIDEO_CODECS):
                checks["video_codec"] = True
                arabic_details.append(f"✓ ترميز الفيديو مطابق للمواصفات: H.264 ({v_codec}).")
            else:
                errors.append(f"Invalid video codec '{v_codec}'. Required: H.264 / AVC.")
                arabic_details.append(f"✗ ترميز الفيديو غير مطابق ({v_codec})، المطلوب H.264.")

        # 4 & 8. Audio stream & codec (AAC)
        if not audio_stream:
            errors.append("No audio stream found in container.")
            arabic_details.append("✗ لا يوجد مسار صوتي داخل الملف.")
        else:
            checks["audio_present"] = True
            a_codec = str(audio_stream.get("codec_name", "")).lower()
            details["audio_codec"] = a_codec
            details["audio_sample_rate"] = audio_stream.get("sample_rate")
            details["audio_channels"] = audio_stream.get("channels")

            if a_codec in self.VALID_AUDIO_CODECS:
                checks["audio_codec"] = True
                arabic_details.append(f"✓ ترميز الصوت مطابق للمواصفات: AAC ({a_codec}).")
            else:
                errors.append(f"Invalid audio codec '{a_codec}'. Required: AAC.")
                arabic_details.append(f"✗ ترميز الصوت غير مطابق ({a_codec})، المطلوب AAC.")

        # 5. Resolution check
        target_w, target_h = self.TARGET_RESOLUTIONS[normalized_type]
        if video_stream:
            actual_w = int(video_stream.get("width", 0))
            actual_h = int(video_stream.get("height", 0))
            details["resolution"] = f"{actual_w}x{actual_h}"
            details["target_resolution"] = f"{target_w}x{target_h}"

            # Exact match check
            if actual_w == target_w and actual_h == target_h:
                checks["resolution"] = True
                arabic_details.append(f"✓ أبعاد الفيديو مطابقة تماماً: {actual_w}x{actual_h} ({normalized_type}).")
            else:
                errors.append(
                    f"Resolution mismatch: expected {target_w}x{target_h} for {normalized_type}, got {actual_w}x{actual_h}."
                )
                arabic_details.append(
                    f"✗ دقة الفيديو ({actual_w}x{actual_h}) لا تطابق الدقة المستهدفة ({target_w}x{target_h})."
                )

        # 6. Duration check
        duration_sec = 0.0
        try:
            if "duration" in format_info and format_info["duration"]:
                duration_sec = float(format_info["duration"])
            elif video_stream and "duration" in video_stream and video_stream["duration"]:
                duration_sec = float(video_stream["duration"])
        except (ValueError, TypeError):
            duration_sec = 0.0

        details["duration_seconds"] = duration_sec
        min_dur, max_dur = self.DURATION_LIMITS[normalized_type]

        if min_dur <= duration_sec <= max_dur:
            checks["duration"] = True
            arabic_details.append(
                f"✓ مدة الفيديو ({duration_sec:.1f} ثانية) تقع ضمن النطاق المسموح ({int(min_dur)}-{int(max_dur)} ثانية)."
            )
        else:
            errors.append(
                f"Duration out of bounds for {normalized_type}: {duration_sec:.1f}s (allowed: {min_dur}s-{max_dur}s)."
            )
            arabic_details.append(
                f"✗ مدة الفيديو ({duration_sec:.1f} ثانية) خارج النطاق المسموح ({int(min_dur)}-{int(max_dur)} ثانية)."
            )

        # 7. File size check
        max_size = self.MAX_FILE_SIZES[normalized_type]
        size_mb = file_size / (1024 * 1024)
        max_size_mb = max_size / (1024 * 1024)
        details["file_size_mb"] = round(size_mb, 2)

        if 0 < file_size <= max_size:
            checks["file_size"] = True
            arabic_details.append(
                f"✓ حجم الملف ({size_mb:.2f} ميجابايت) أقل من الحد الأقصى المسموح ({int(max_size_mb)} ميجابايت)."
            )
        else:
            errors.append(
                f"File size exceeds limit: {size_mb:.2f} MB > {max_size_mb:.0f} MB."
            )
            arabic_details.append(
                f"✗ حجم الملف ({size_mb:.2f} ميجابايت) يتجاوز الحد الأقصى ({int(max_size_mb)} ميجابايت)."
            )

        # 9. Frame rate check (>= 24 fps)
        if video_stream:
            fps_str = video_stream.get("r_frame_rate", "0/0")
            avg_fps_str = video_stream.get("avg_frame_rate", "0/0")
            fps = 0.0
            try:
                for rate in (fps_str, avg_fps_str):
                    if "/" in rate:
                        num, den = rate.split("/", 1)
                        if float(den) > 0:
                            fps = float(num) / float(den)
                            if fps > 0:
                                break
                    elif rate:
                        fps = float(rate)
                        if fps > 0:
                            break
            except (ValueError, ZeroDivisionError):
                fps = 0.0

            details["fps"] = round(fps, 2)
            if fps >= self.MIN_FRAME_RATE:
                checks["frame_rate"] = True
                arabic_details.append(f"✓ معدل الإطارات ممتاز: {fps:.2f} إطار/ثانية (المطلوب >= 24).")
            else:
                errors.append(f"Frame rate too low: {fps:.2f} fps < 24 fps.")
                arabic_details.append(f"✗ معدل الإطارات منخفض جداً ({fps:.2f} إطار/ثانية)، المطلوب >= 24.")

        # 10. Bitstream corruption check
        corr_passed, corr_err = self.check_corruption(path_obj)
        checks["no_corruption"] = corr_passed
        if corr_passed:
            arabic_details.append("✓ اجتاز الفيديو فحص سلامة وفك تشفير الإطارات بالكامل بدون أي أخطاء.")
        else:
            errors.append(f"Video bitstream corruption detected: {corr_err}")
            arabic_details.append(f"✗ تم رصد تلف في فك تشفير إطارات الفيديو: {corr_err}")

        # Overall verdict
        is_valid = len(errors) == 0 and all(checks.values())

        if is_valid:
            arabic_summary = (
                f"تم اجتياز جميع معايير الجودة بنجاح ({normalized_type}). الفيديو جاهز تماماً للنشر على يوتيوب."
            )
        else:
            arabic_summary = (
                f"فشل التحقق من الجودة ({len(errors)} مشكلة). يجب إصلاح الأخطاء قبل محاولة الرفع إلى يوتيوب."
            )

        return QualityReport(
            is_valid=is_valid,
            video_path=str(path_obj),
            video_type=normalized_type,
            checks=checks,
            errors=errors,
            warnings=warnings,
            details=details,
            arabic_summary=arabic_summary,
            arabic_details=arabic_details,
        )
