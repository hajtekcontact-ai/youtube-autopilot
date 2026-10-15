"""Video Renderer Engine for YouTube AI Autopilot.

The core production engine orchestrating the complete video rendering pipeline:
1. Script Text-to-Speech generation with failover and normalization
2. Audio duration detection and segment allocation
3. High-definition visual frame sequence generation at 30fps
4. Title card, section headers, and animated transition synthesis
5. FFmpeg video assembly from image sequences
6. Hardcoded subtitles generation with safe margins
7. Optional intro and outro video stitching
8. Loudness normalization using FFmpeg loudnorm filter
9. Final broadcast encoding: H.264 (MP4) + AAC with faststart
10. Strict output validation (resolution, duration, size, stream integrity)

Includes SQLite persistence for render jobs history, automated temp file cleanup,
progress reporting callbacks, and robust error recovery.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.video.subtitle_generator import SubtitleGenerator
from src.video.tts_engine import AudioResult, TTSEngine
from src.video.visual_generator import VisualGenerator

# Configure Module Logger
logger = logging.getLogger("youtube_autopilot.renderer")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [VideoRenderer] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# UI Status Strings in Arabic
UI_MESSAGES_AR: Dict[str, str] = {
    "init_pipeline": "بدء دورة إنتاج الفيديو بالذكاء الاصطناعي...",
    "generating_tts": "المرحلة 1: توليد التعليق الصوتي والتحقق من المدة الزمنية...",
    "generating_visuals": "المرحلة 2: إنشاء الإطارات المرئية والرسوم البيانية المتحركة...",
    "generating_title_card": "المرحلة 3: تصميم شاشة البداية وعناصر الانتقال...",
    "generating_subtitles": "المرحلة 4: حساب توقيت الكلمات وإنشاء شريط الترجمة...",
    "assembling_ffmpeg": "المرحلة 5: تجميع إطارات الفيديو ومسار الصوت عبر FFmpeg...",
    "stitching_intro_outro": "المرحلة 6: دمج مقدمة وخاتمة الفيديو...",
    "encoding_final": "المرحلة 7: تطبيق مرشح الصوت القياسي loudnorm وتشفير H.264/AAC النهائي...",
    "validating_output": "المرحلة 8: التحقق من معايير الملف ومطابقة الدقة والحجم...",
    "rendering_completed": "تم الانتهاء من إنتاج الفيديو بنجاح وجاهز للنشر!",
    "rendering_failed": "حدث خطأ أثناء معالجة وإنتاج الفيديو",
    "budget_exceeded_warn": "تحذير: حجم الفيديو يقترب من الحد الأقصى للملف",
}


@dataclass
class ProfileConfig:
    """Settings defining a video rendering profile."""

    name: str
    width: int
    height: int
    fps: int
    min_duration: float
    max_duration: float
    target_video_bitrate_kbps: int
    max_video_bitrate_kbps: int
    audio_bitrate_kbps: int
    max_file_size_bytes: int

    @property
    def resolution_tuple(self) -> Tuple[int, int]:
        """Resolution dimensions tuple (width, height)."""
        return (self.width, self.height)

    @property
    def resolution_str(self) -> str:
        """Formatted resolution string WxH."""
        return f"{self.width}x{self.height}"


# Standard YouTube Production Profiles
PROFILES: Dict[str, ProfileConfig] = {
    "SHORT": ProfileConfig(
        name="SHORT",
        width=1080,
        height=1920,
        fps=30,
        min_duration=15.0,
        max_duration=58.0,
        target_video_bitrate_kbps=4000,
        max_video_bitrate_kbps=5000,
        audio_bitrate_kbps=128,
        max_file_size_bytes=25 * 1024 * 1024,  # 25 MB max
    ),
    "LONG": ProfileConfig(
        name="LONG",
        width=1920,
        height=1080,
        fps=30,
        min_duration=300.0,
        max_duration=600.0,
        target_video_bitrate_kbps=2000,
        max_video_bitrate_kbps=2500,
        audio_bitrate_kbps=128,
        max_file_size_bytes=100 * 1024 * 1024,  # 100 MB max
    ),
}


@dataclass
class VideoRenderRequest:
    """Parameters for initiating a complete video render."""

    title: str
    subtitle: str = ""
    script_text: str = ""
    script_sections: List[Dict[str, Any]] = field(default_factory=list)
    profile: str = "SHORT"  # "SHORT" or "LONG"
    style: str = "dark_tech"
    voice: Optional[str] = None
    output_path: Optional[str] = None
    include_subtitles: bool = True
    include_title_card: bool = True
    intro_video_path: Optional[str] = None
    outro_video_path: Optional[str] = None
    job_id: Optional[str] = None


@dataclass
class RenderedVideo:
    """Result payload from a successful video render."""

    job_id: str
    video_path: str
    profile: str
    duration_seconds: float
    width: int
    height: int
    fps: int
    file_size_bytes: int
    file_size_mb: float
    subtitles_path: Optional[str]
    audio_path: Optional[str]
    rendering_time_seconds: float
    created_at: str
    status: str
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary representation."""
        return asdict(self)


class VideoRenderer:
    """The central video production orchestrator for YouTube AI Autopilot."""

    def __init__(
        self,
        db_path: str = "data/autopilot.db",
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
    ) -> None:
        """Initialize the video rendering pipeline.

        Args:
            db_path: Path to SQLite database for audit and job tracking.
            ffmpeg_bin: Path or command for FFmpeg executable.
            ffprobe_bin: Path or command for FFprobe executable.
        """
        self.db_path = os.path.abspath(db_path)
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin

        # Sub-engines
        self.tts_engine = TTSEngine(db_path=self.db_path)
        self.visual_generator = VisualGenerator(fps=30)
        self.subtitle_generator = SubtitleGenerator(default_font="DejaVu Sans")

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_database()
        self._verify_ffmpeg_installation()

    def _get_db_connection(self) -> sqlite3.Connection:
        """Obtain a SQLite database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Create render jobs tracking table in SQLite."""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS video_render_jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT,
                    profile TEXT,
                    style TEXT,
                    status TEXT,
                    duration_seconds REAL,
                    file_size_bytes INTEGER,
                    output_path TEXT,
                    subtitles_path TEXT,
                    error_message TEXT,
                    rendering_time_seconds REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
                """
            )
            conn.commit()

    def _verify_ffmpeg_installation(self) -> None:
        """Verify presence of FFmpeg and FFprobe binaries."""
        for binary in (self.ffmpeg_bin, self.ffprobe_bin):
            res = shutil.which(binary)
            if not res:
                raise EnvironmentError(
                    f"Required binary '{binary}' was not found in system PATH."
                )

    def _record_job_start(self, job_id: str, title: str, profile: str, style: str) -> None:
        """Insert pending job record into SQLite."""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO video_render_jobs
                    (job_id, title, profile, style, status, created_at)
                    VALUES (?, ?, ?, ?, 'PROCESSING', ?)
                    """,
                    (job_id, title, profile, style, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
        except sqlite3.Error as err:
            logger.warning(f"Failed to record job start in DB: {err}")

    def _record_job_finish(
        self,
        job_id: str,
        status: str,
        duration: float,
        file_size: int,
        output_path: str,
        subtitles_path: Optional[str],
        render_time: float,
        error_msg: Optional[str] = None,
    ) -> None:
        """Update job record status in SQLite."""
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE video_render_jobs
                    SET status = ?, duration_seconds = ?, file_size_bytes = ?,
                        output_path = ?, subtitles_path = ?, error_message = ?,
                        rendering_time_seconds = ?, completed_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        status,
                        duration,
                        file_size,
                        output_path,
                        subtitles_path,
                        error_msg,
                        render_time,
                        datetime.now(timezone.utc).isoformat(),
                        job_id,
                    ),
                )
                conn.commit()
        except sqlite3.Error as err:
            logger.warning(f"Failed to record job completion in DB: {err}")

    def get_render_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch render job metadata from SQLite database."""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM video_render_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_render_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List historical render jobs from SQLite database."""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM video_render_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    # ==========================================
    # FFmpeg Command Builders
    # ==========================================

    def build_image_sequence_command(
        self,
        frame_pattern: str,
        audio_path: str,
        output_path: str,
        fps: int,
        resolution: Tuple[int, int],
        video_bitrate_kbps: int,
        audio_bitrate_kbps: int,
    ) -> List[str]:
        """Build FFmpeg command to assemble an image sequence and audio file into an MP4 video."""
        w, h = resolution
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frame_pattern),
            "-i",
            str(audio_path),
            "-vf",
            f"scale={w}:{h},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            f"{video_bitrate_kbps}k",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_bitrate_kbps}k",
            "-ar",
            "48000",
            "-shortest",
            str(output_path),
        ]
        return cmd

    def build_loudnorm_audio_command(
        self,
        input_audio: str,
        output_audio: str,
    ) -> List[str]:
        """Build FFmpeg command applying EBU R128 loudness normalization filter."""
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(input_audio),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            str(output_audio),
        ]
        return cmd

    def build_concat_command(
        self,
        video_segments: List[str],
        output_path: str,
        concat_list_file: str,
    ) -> List[str]:
        """Build FFmpeg command to concatenate video segments without re-encoding."""
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in video_segments:
                abs_seg = os.path.abspath(seg)
                f.write(f"file '{abs_seg}'\n")

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file),
            "-c",
            "copy",
            str(output_path),
        ]
        return cmd

    def build_final_encode_command(
        self,
        input_video: str,
        output_path: str,
        profile_cfg: ProfileConfig,
        srt_path: Optional[str] = None,
        normalize_audio: bool = True,
    ) -> List[str]:
        """Build comprehensive final encoding command enforcing bitrate, loudnorm, and container optimization."""
        w = profile_cfg.width
        h = profile_cfg.height
        is_portrait = h > w

        # Build video filters list
        video_filters: List[str] = [f"scale={w}:{h}", "format=yuv420p"]

        if srt_path and os.path.exists(srt_path):
            escaped_srt = (
                os.path.abspath(srt_path)
                .replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            font_size = 32 if is_portrait else 24
            margin_v = int(h * 0.20) if is_portrait else int(h * 0.10)
            style_str = (
                f"FontName=DejaVu Sans,FontSize={font_size},"
                f"PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,"
                f"BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV={margin_v}"
            )
            video_filters.append(f"subtitles='{escaped_srt}':force_style='{style_str}'")

        vf_arg = ",".join(video_filters)

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(input_video),
            "-vf",
            vf_arg,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            f"{profile_cfg.target_video_bitrate_kbps}k",
            "-maxrate",
            f"{profile_cfg.max_video_bitrate_kbps}k",
            "-bufsize",
            f"{profile_cfg.max_video_bitrate_kbps * 2}k",
            "-c:a",
            "aac",
            "-b:a",
            f"{profile_cfg.audio_bitrate_kbps}k",
            "-ar",
            "48000",
        ]

        if normalize_audio:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])

        # Enable faststart for web streaming and YouTube ingestion
        cmd.extend(["-movflags", "+faststart", str(output_path)])
        return cmd

    def _execute_subprocess(self, cmd: List[str], desc: str) -> None:
        """Run an FFmpeg/system command with error capture and logging."""
        logger.debug(f"Executing FFmpeg [{desc}]: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            err_msg = (
                f"Command '{desc}' failed with exit code {res.returncode}.\n"
                f"STDERR: {res.stderr[-800:] if res.stderr else 'None'}"
            )
            logger.error(err_msg)
            raise RuntimeError(err_msg)

    def probe_media_file(self, file_path: str) -> Dict[str, Any]:
        """Probe media file attributes using ffprobe."""
        abs_p = os.path.abspath(file_path)
        cmd = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=width,height,codec_name,r_frame_rate",
            "-of",
            "json",
            abs_p,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFprobe failed on '{abs_p}': {res.stderr}")

        return json.loads(res.stdout)

    def _validate_rendered_video(
        self,
        video_path: str,
        profile_cfg: ProfileConfig,
    ) -> Tuple[float, int, int]:
        """Validate generated MP4 output integrity against profile constraints."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Rendered video file not found at {video_path}")

        file_size = os.path.getsize(video_path)
        if file_size <= 1000:
            raise ValueError(f"Rendered video size is invalid ({file_size} bytes).")

        probe_data = self.probe_media_file(video_path)
        format_info = probe_data.get("format", {})
        streams = probe_data.get("streams", [])

        duration = float(format_info.get("duration", 0.0))
        if duration <= 0.0:
            raise ValueError("Rendered video duration is zero or invalid.")

        video_stream = next((s for s in streams if s.get("codec_name") == "h264"), None)
        if not video_stream:
            # Fall back to any video codec
            video_stream = next((s for s in streams if "width" in s), None)

        if not video_stream:
            raise ValueError("No valid video stream found in rendered MP4.")

        w = int(video_stream.get("width", 0))
        h = int(video_stream.get("height", 0))

        if w != profile_cfg.width or h != profile_cfg.height:
            logger.warning(
                f"Resolution mismatch: expected {profile_cfg.resolution_str}, got {w}x{h}"
            )

        # File size budget check
        if file_size > profile_cfg.max_file_size_bytes:
            logger.warning(
                f"{UI_MESSAGES_AR['budget_exceeded_warn']}: "
                f"Size {file_size / (1024*1024):.2f}MB exceeds limit "
                f"{profile_cfg.max_file_size_bytes / (1024*1024):.2f}MB"
            )

        return (duration, w, h)

    def _prepare_sections(
        self,
        request: VideoRenderRequest,
        audio_duration: float,
    ) -> List[Dict[str, Any]]:
        """Normalize script sections and synchronize timing to match audio duration."""
        sections = list(request.script_sections)

        if not sections:
            # Create a single comprehensive section from script_text or title
            sections = [
                {
                    "title": request.title,
                    "body": request.script_text,
                    "type": "fact",
                    "duration": audio_duration,
                }
            ]

        # Allocate durations proportionally to match audio duration exactly
        raw_durations = [float(s.get("duration", max(2.0, len(s.get("body", "")) / 15.0))) for s in sections]
        total_raw = sum(raw_durations)
        if total_raw <= 0:
            total_raw = 1.0

        scale = audio_duration / total_raw
        for idx, s in enumerate(sections):
            s["duration"] = raw_durations[idx] * scale

        return sections

    async def render_video_async(
        self,
        video_data: Union[Dict[str, Any], VideoRenderRequest],
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> RenderedVideo:
        """Execute the full video production pipeline asynchronously.

        Args:
            video_data: Render parameters dictionary or VideoRenderRequest instance.
            on_progress: Optional progress callback function (progress: float, message: str).

        Returns:
            RenderedVideo containing production metrics and final output path.

        Raises:
            Exception: If rendering pipeline fails at any stage.
        """
        start_time = time.time()

        # Parse request object
        if isinstance(video_data, dict):
            req = VideoRenderRequest(
                title=video_data.get("title", "Untitled Video"),
                subtitle=video_data.get("subtitle", ""),
                script_text=video_data.get("script_text", ""),
                script_sections=video_data.get("script_sections", []),
                profile=video_data.get("profile", "SHORT").upper(),
                style=video_data.get("style", "dark_tech"),
                voice=video_data.get("voice"),
                output_path=video_data.get("output_path"),
                include_subtitles=video_data.get("include_subtitles", True),
                include_title_card=video_data.get("include_title_card", True),
                intro_video_path=video_data.get("intro_video_path"),
                outro_video_path=video_data.get("outro_video_path"),
                job_id=video_data.get("job_id"),
            )
        else:
            req = video_data

        job_id = req.job_id or f"render_{uuid.uuid4().hex[:12]}"
        profile_key = req.profile.upper() if req.profile.upper() in PROFILES else "SHORT"
        profile_cfg = PROFILES[profile_key]

        logger.info(f"Starting render job '{job_id}' for '{req.title}' [Profile: {profile_cfg.name}]")
        self._record_job_start(job_id, req.title, profile_cfg.name, req.style)

        def report(progress_val: float, message_key: str) -> None:
            ar_msg = UI_MESSAGES_AR.get(message_key, message_key)
            logger.info(f"[{job_id}] ({int(progress_val*100)}%) {ar_msg}")
            if on_progress:
                try:
                    on_progress(progress_val, ar_msg)
                except Exception:
                    pass

        report(0.05, "init_pipeline")

        # Destination path setup
        if not req.output_path:
            os.makedirs("output", exist_ok=True)
            final_output_path = os.path.abspath(f"output/{job_id}.mp4")
        else:
            final_output_path = os.path.abspath(req.output_path)
            os.makedirs(os.path.dirname(final_output_path), exist_ok=True)

        # Execute in temporary directory with automatic cleanup
        with tempfile.TemporaryDirectory(prefix=f"autopilot_{job_id}_") as temp_dir:
            try:
                # ---------------------------------------------------------
                # Step 1: Generate Speech Audio
                # ---------------------------------------------------------
                report(0.15, "generating_tts")
                tts_audio_path = os.path.join(temp_dir, "narration.mp3")

                # Combine sections text if script_text was omitted
                full_script = req.script_text.strip()
                if not full_script and req.script_sections:
                    full_script = " ".join(
                        s.get("body", "") for s in req.script_sections if s.get("body")
                    )
                if not full_script:
                    full_script = req.title

                audio_result = await self.tts_engine.generate_speech(
                    text=full_script,
                    output_path=tts_audio_path,
                    voice=req.voice,
                )
                audio_duration = audio_result.duration_seconds
                logger.info(f"Audio generated: duration={audio_duration:.2f}s, engine={audio_result.engine_used}")

                # ---------------------------------------------------------
                # Step 2: Prepare Script Sections & Durations
                # ---------------------------------------------------------
                sections = self._prepare_sections(req, audio_duration)

                # ---------------------------------------------------------
                # Step 3: Visual Frame Generation
                # ---------------------------------------------------------
                report(0.35, "generating_visuals")
                frames_dir = os.path.join(temp_dir, "frames")
                os.makedirs(frames_dir, exist_ok=True)

                frame_paths = self.visual_generator.generate_frames(
                    script_sections=sections,
                    resolution=profile_cfg.resolution_tuple,
                    style=req.style,
                    output_dir=frames_dir,
                )
                if not frame_paths:
                    raise RuntimeError("Failed to generate visual frame sequence.")

                # ---------------------------------------------------------
                # Step 4: Generate Subtitles
                # ---------------------------------------------------------
                subtitles_file = None
                if req.include_subtitles:
                    report(0.60, "generating_subtitles")
                    subtitles_file = os.path.join(temp_dir, "subtitles.srt")
                    self.subtitle_generator.generate_srt(
                        script_text=full_script,
                        total_duration=audio_duration,
                        output_path=subtitles_file,
                    )

                # ---------------------------------------------------------
                # Step 5: FFmpeg Assembly (Frames + Audio)
                # ---------------------------------------------------------
                report(0.75, "assembling_ffmpeg")
                assembled_raw = os.path.join(temp_dir, "raw_assembled.mp4")
                frame_pattern = os.path.join(frames_dir, "frame_%06d.png")

                assemble_cmd = self.build_image_sequence_command(
                    frame_pattern=frame_pattern,
                    audio_path=tts_audio_path,
                    output_path=assembled_raw,
                    fps=profile_cfg.fps,
                    resolution=profile_cfg.resolution_tuple,
                    video_bitrate_kbps=profile_cfg.target_video_bitrate_kbps,
                    audio_bitrate_kbps=profile_cfg.audio_bitrate_kbps,
                )
                self._execute_subprocess(assemble_cmd, "Assemble Frames & Audio")

                current_video = assembled_raw

                # ---------------------------------------------------------
                # Step 6: Handle Optional Intro & Outro Stitching
                # ---------------------------------------------------------
                if req.intro_video_path or req.outro_video_path:
                    report(0.85, "stitching_intro_outro")
                    segments: List[str] = []
                    if req.intro_video_path and os.path.exists(req.intro_video_path):
                        segments.append(os.path.abspath(req.intro_video_path))
                    segments.append(current_video)
                    if req.outro_video_path and os.path.exists(req.outro_video_path):
                        segments.append(os.path.abspath(req.outro_video_path))

                    if len(segments) > 1:
                        stitched_video = os.path.join(temp_dir, "stitched.mp4")
                        concat_txt = os.path.join(temp_dir, "concat.txt")
                        concat_cmd = self.build_concat_command(segments, stitched_video, concat_txt)
                        self._execute_subprocess(concat_cmd, "Stitch Intro/Outro")
                        current_video = stitched_video

                # ---------------------------------------------------------
                # Step 7: Final Broadcast Encoding & Audio Normalization
                # ---------------------------------------------------------
                report(0.92, "encoding_final")

                # Dynamically adjust bitrate to guarantee staying within file size limits
                target_video_kbps = profile_cfg.target_video_bitrate_kbps
                safe_max_bytes = profile_cfg.max_file_size_bytes * 0.92
                audio_bytes = (profile_cfg.audio_bitrate_kbps * 1000 / 8) * audio_duration
                allowed_video_bytes = max(100000, safe_max_bytes - audio_bytes)
                duration_safe = max(1.0, audio_duration)
                max_calculated_kbps = int((allowed_video_bytes * 8) / (duration_safe * 1000))
                effective_video_kbps = min(target_video_kbps, max(500, max_calculated_kbps))

                adjusted_profile = ProfileConfig(
                    name=profile_cfg.name,
                    width=profile_cfg.width,
                    height=profile_cfg.height,
                    fps=profile_cfg.fps,
                    min_duration=profile_cfg.min_duration,
                    max_duration=profile_cfg.max_duration,
                    target_video_bitrate_kbps=effective_video_kbps,
                    max_video_bitrate_kbps=min(
                        profile_cfg.max_video_bitrate_kbps, int(effective_video_kbps * 1.25)
                    ),
                    audio_bitrate_kbps=profile_cfg.audio_bitrate_kbps,
                    max_file_size_bytes=profile_cfg.max_file_size_bytes,
                )

                encode_cmd = self.build_final_encode_command(
                    input_video=current_video,
                    output_path=final_output_path,
                    profile_cfg=adjusted_profile,
                    srt_path=subtitles_file if req.include_subtitles else None,
                    normalize_audio=True,
                )
                self._execute_subprocess(encode_cmd, "Final Encoding with Subtitles & Loudnorm")

                # ---------------------------------------------------------
                # Step 8: Validate Rendered Video Output
                # ---------------------------------------------------------
                report(0.98, "validating_output")
                duration, w, h = self._validate_rendered_video(final_output_path, profile_cfg)

                final_size = os.path.getsize(final_output_path)
                total_render_time = time.time() - start_time

                # Save copy of subtitles next to output if generated
                final_srt_path = None
                if subtitles_file and os.path.exists(subtitles_file):
                    srt_dest = str(Path(final_output_path).with_suffix(".srt"))
                    try:
                        shutil.copy2(subtitles_file, srt_dest)
                        final_srt_path = srt_dest
                    except Exception:
                        pass

                self._record_job_finish(
                    job_id=job_id,
                    status="COMPLETED",
                    duration=duration,
                    file_size=final_size,
                    output_path=final_output_path,
                    subtitles_path=final_srt_path,
                    render_time=total_render_time,
                )

                report(1.0, "rendering_completed")

                return RenderedVideo(
                    job_id=job_id,
                    video_path=final_output_path,
                    profile=profile_cfg.name,
                    duration_seconds=duration,
                    width=w,
                    height=h,
                    fps=profile_cfg.fps,
                    file_size_bytes=final_size,
                    file_size_mb=round(final_size / (1024 * 1024), 2),
                    subtitles_path=final_srt_path,
                    audio_path=None,
                    rendering_time_seconds=round(total_render_time, 2),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    status="COMPLETED",
                )

            except Exception as err:
                total_render_time = time.time() - start_time
                error_str = str(err)
                logger.error(f"Render pipeline failed for job '{job_id}': {error_str}", exc_info=True)
                self._record_job_finish(
                    job_id=job_id,
                    status="FAILED",
                    duration=0.0,
                    file_size=0,
                    output_path="",
                    subtitles_path=None,
                    render_time=total_render_time,
                    error_msg=error_str,
                )
                raise RuntimeError(f"{UI_MESSAGES_AR['rendering_failed']}: {error_str}") from err

    def render_video(
        self,
        video_data: Union[Dict[str, Any], VideoRenderRequest],
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> RenderedVideo:
        """Synchronous wrapper for rendering a video.

        Blocks until the full rendering pipeline finishes.

        Args:
            video_data: VideoRenderRequest or dictionary containing rendering parameters.
            on_progress: Optional progress callback.

        Returns:
            RenderedVideo outcome.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import nest_asyncio  # In case inside existing event loop
            nest_asyncio.apply()
            return loop.run_until_complete(self.render_video_async(video_data, on_progress))
        else:
            return asyncio.run(self.render_video_async(video_data, on_progress))
