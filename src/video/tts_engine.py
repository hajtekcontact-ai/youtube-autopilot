"""Text-to-Speech (TTS) Engine for YouTube AI Autopilot.

Uses edge-tts (Microsoft Edge TTS) as the primary high-quality speech engine
with automatic fallback to Google Translate TTS (gTTS) when network or service
issues occur. Provides audio splitting for long narrations, loudness normalization,
voice caching, and SQLite audit logging.
"""

import asyncio
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import edge_tts
try:
    from gtts import gTTS
except ImportError:
    gTTS = None
from pydub import AudioSegment, effects

# Default American English Voices
VOICE_MALE: str = "en-US-GuyNeural"
VOICE_FEMALE: str = "en-US-JennyNeural"
DEFAULT_VOICE: str = VOICE_MALE

# Fallback gTTS Configuration
FALLBACK_GTTS_LANG: str = "en"
FALLBACK_GTTS_TLD: str = "com"

# Target Audio Normalization Level (YouTube standard speech ~ -16 to -14 LUFS/dBFS)
TARGET_DBFS: float = -16.0

# UI Status Strings in Arabic
UI_MESSAGES_AR: Dict[str, str] = {
    "engine_edge_tts": "محرك Microsoft Edge TTS (عالي الجودة)",
    "engine_gtts": "محرك Google TTS (البديل التلقائي)",
    "generating_audio": "جاري توليد التعليق الصوتي بالذكاء الاصطناعي...",
    "generating_segment": "جاري معالجة المقطع الصوتي {index} من {total}...",
    "edge_tts_failed": "تعذر الاتصال بمحرك Edge-TTS، جاري التبديل التلقائي إلى gTTS...",
    "normalization": "جاري تسوية مستوى الصوت وضبط المعايير القياسية...",
    "success": "تم إنشاء الملف الصوتي بنجاح بجودة عالية",
    "error_empty_text": "نص الاسكريبت فارغ، لا يمكن توليد الصوت",
    "error_generation_failed": "فشل توليد الصوت عبر جميع المحركات المتاحة",
}


@dataclass
class AudioResult:
    """Represents the outcome of a text-to-speech generation operation."""

    audio_path: str
    duration_seconds: float
    sample_rate: int
    channels: int
    engine_used: str
    voice: str
    file_size_bytes: int
    segments_count: int
    text_length: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert the result dataclass to a dictionary representation."""
        return asdict(self)


class TTSEngine:
    """Production-grade Text-to-Speech synthesizer with failover and normalization."""

    def __init__(
        self,
        db_path: str = "data/autopilot.db",
        default_voice: str = DEFAULT_VOICE,
        target_dbfs: float = TARGET_DBFS,
        max_segment_chars: int = 400,
    ) -> None:
        """Initialize the TTS engine with database persistence and voice defaults.

        Args:
            db_path: Path to the SQLite database for caching voices and logging.
            default_voice: Primary Edge TTS voice identifier.
            target_dbfs: Target loudness in decibels relative to full scale.
            max_segment_chars: Maximum character limit for script chunking.
        """
        self.db_path = os.path.abspath(db_path)
        self.default_voice = default_voice
        self.target_dbfs = target_dbfs
        self.max_segment_chars = max_segment_chars
        self._memory_voice_cache: Optional[List[Dict[str, Any]]] = None

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_database()

    def _get_db_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Ensure database tables for voice caching and generation audits exist."""
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_voices_cache (
                    short_name TEXT PRIMARY KEY,
                    friendly_name TEXT,
                    gender TEXT,
                    locale TEXT,
                    suggested_codec TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_generation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text_snippet TEXT,
                    text_length INTEGER,
                    voice TEXT,
                    engine_used TEXT,
                    duration_seconds REAL,
                    file_size_bytes INTEGER,
                    output_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def _split_text_into_segments(self, text: str) -> List[str]:
        """Split a long script text into cohesive segments for stable generation.

        Splits text by sentence boundaries and groups smaller sentences
        together without exceeding the configured character threshold.

        Args:
            text: Raw input script text.

        Returns:
            List of non-empty text chunks.
        """
        clean_text = re.sub(r"\s+", " ", text).strip()
        if not clean_text:
            return []

        # Split on sentence ending punctuation while retaining readability
        raw_sentences = re.split(r"(?<=[.!?])\s+", clean_text)
        segments: List[str] = []
        current_chunk: List[str] = []
        current_len = 0

        for sentence in raw_sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # If a single sentence is exceptionally long, split on commas or clauses
            if len(sentence) > self.max_segment_chars:
                clauses = re.split(r"(?<=[,;:])\s+", sentence)
                for clause in clauses:
                    clause = clause.strip()
                    if not clause:
                        continue
                    if current_len + len(clause) + 1 <= self.max_segment_chars:
                        current_chunk.append(clause)
                        current_len += len(clause) + 1
                    else:
                        if current_chunk:
                            segments.append(" ".join(current_chunk))
                        current_chunk = [clause]
                        current_len = len(clause)
                continue

            if current_len + len(sentence) + 1 <= self.max_segment_chars:
                current_chunk.append(sentence)
                current_len += len(sentence) + 1
            else:
                if current_chunk:
                    segments.append(" ".join(current_chunk))
                current_chunk = [sentence]
                current_len = len(sentence)

        if current_chunk:
            segments.append(" ".join(current_chunk))

        return segments if segments else [clean_text]

    async def list_available_voices(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Retrieve and cache the catalog of available Edge-TTS voices.

        Uses in-memory cache first, falls back to SQLite cache, and queries
        the remote Edge-TTS service if unpopulated or explicitly refreshed.

        Args:
            force_refresh: When True, bypasses cache and queries remote API.

        Returns:
            List of voice metadata dictionaries.
        """
        if not force_refresh and self._memory_voice_cache is not None:
            return self._memory_voice_cache

        if not force_refresh:
            try:
                with self._get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM tts_voices_cache ORDER BY locale, short_name")
                    rows = cursor.fetchall()
                    if rows:
                        cached = [dict(row) for row in rows]
                        self._memory_voice_cache = cached
                        return cached
            except sqlite3.Error:
                pass

        try:
            remote_voices = await edge_tts.list_voices()
            parsed_voices: List[Dict[str, Any]] = []

            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM tts_voices_cache")
                now_str = datetime.now(timezone.utc).isoformat()

                for voice in remote_voices:
                    item = {
                        "short_name": voice.get("ShortName", ""),
                        "friendly_name": voice.get("FriendlyName", ""),
                        "gender": voice.get("Gender", ""),
                        "locale": voice.get("Locale", ""),
                        "suggested_codec": voice.get("SuggestedCodec", "audio-24khz-48kbitrate-mono-mp3"),
                    }
                    parsed_voices.append(item)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO tts_voices_cache
                        (short_name, friendly_name, gender, locale, suggested_codec, cached_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["short_name"],
                            item["friendly_name"],
                            item["gender"],
                            item["locale"],
                            item["suggested_codec"],
                            now_str,
                        ),
                    )
                conn.commit()

            self._memory_voice_cache = parsed_voices
            return parsed_voices
        except Exception:
            # Return built-in standard defaults if network fails
            fallback_list = [
                {
                    "short_name": VOICE_MALE,
                    "friendly_name": "Microsoft Guy Online (Natural) - English (United States)",
                    "gender": "Male",
                    "locale": "en-US",
                    "suggested_codec": "audio-24khz-48kbitrate-mono-mp3",
                },
                {
                    "short_name": VOICE_FEMALE,
                    "friendly_name": "Microsoft Jenny Online (Natural) - English (United States)",
                    "gender": "Female",
                    "locale": "en-US",
                    "suggested_codec": "audio-24khz-48kbitrate-mono-mp3",
                },
            ]
            self._memory_voice_cache = fallback_list
            return fallback_list

    async def _synthesize_edge_segment(
        self, text_segment: str, voice: str, destination_path: str, retries: int = 2
    ) -> bool:
        """Synthesize a single text chunk using Edge TTS with retry logic."""
        for attempt in range(1, retries + 2):
            try:
                communicator = edge_tts.Communicate(text=text_segment, voice=voice)
                await asyncio.wait_for(communicator.save(destination_path), timeout=35.0)
                if os.path.exists(destination_path) and os.path.getsize(destination_path) > 0:
                    return True
            except Exception:
                if attempt <= retries:
                    await asyncio.sleep(1.0 * attempt)
        return False

    def _synthesize_gtts_segment(self, text_segment: str, destination_path: str) -> bool:
        """Synthesize a single text chunk using Google TTS (synchronous fallback)."""
        try:
            tts = gTTS(text=text_segment, lang=FALLBACK_GTTS_LANG, tld=FALLBACK_GTTS_TLD, slow=False)
            tts.save(destination_path)
            return os.path.exists(destination_path) and os.path.getsize(destination_path) > 0
        except Exception:
            return False

    def _normalize_loudness(self, audio: AudioSegment) -> AudioSegment:
        """Apply peak normalization and target dBFS adjustment."""
        if len(audio) == 0:
            return audio

        # Peak normalization to prevent clipping
        normalized = effects.normalize(audio, headroom=0.1)

        # Gain correction towards target dBFS
        current_dbfs = normalized.dBFS
        if current_dbfs != float("-inf") and current_dbfs < 0:
            target_gain = self.target_dbfs - current_dbfs
            # Limit maximum boost to avoid distortion
            clamped_gain = max(-12.0, min(12.0, target_gain))
            normalized = normalized.apply_gain(clamped_gain)

        return normalized

    def _log_generation(
        self,
        text: str,
        voice: str,
        engine_used: str,
        duration: float,
        size_bytes: int,
        output_path: str,
    ) -> None:
        """Persist generation audit details to SQLite."""
        try:
            snippet = (text[:120] + "...") if len(text) > 120 else text
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tts_generation_logs
                    (text_snippet, text_length, voice, engine_used, duration_seconds, file_size_bytes, output_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (snippet, len(text), voice, engine_used, duration, size_bytes, output_path),
                )
                conn.commit()
        except sqlite3.Error:
            pass

    async def generate_speech(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
    ) -> AudioResult:
        """Generate a speech audio file (MP3 or WAV) from script text.

        Tries Microsoft Edge TTS first; if network or service errors arise,
        automatically falls back to Google TTS. The resulting audio is
        concatenated with natural pauses and normalized for volume consistency.

        Args:
            text: Script narration text to synthesize.
            output_path: Destination path for the audio file (.mp3 or .wav).
            voice: Voice identifier. Defaults to en-US-GuyNeural.

        Returns:
            AudioResult containing metadata about duration, engine, and size.

        Raises:
            ValueError: If the provided text is empty.
            RuntimeError: If synthesis fails across both primary and fallback engines.
        """
        clean_text = text.strip() if text else ""
        if not clean_text:
            raise ValueError(UI_MESSAGES_AR["error_empty_text"])

        selected_voice = voice or self.default_voice
        abs_output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

        segments = self._split_text_into_segments(clean_text)
        engine_used = "edge-tts"
        combined_audio = AudioSegment.empty()
        inter_segment_pause = AudioSegment.silent(duration=220)  # 220ms natural pause

        with tempfile.TemporaryDirectory(prefix="tts_chunks_") as temp_dir:
            # Stage 1: Try Edge TTS
            edge_success = True
            segment_audio_list: List[AudioSegment] = []

            for idx, segment in enumerate(segments, start=1):
                chunk_file = os.path.join(temp_dir, f"edge_chunk_{idx:04d}.mp3")
                ok = await self._synthesize_edge_segment(segment, selected_voice, chunk_file)
                if not ok:
                    edge_success = False
                    break
                try:
                    seg_audio = AudioSegment.from_file(chunk_file)
                    segment_audio_list.append(seg_audio)
                except Exception:
                    edge_success = False
                    break

            if edge_success and segment_audio_list:
                for idx, seg_audio in enumerate(segment_audio_list):
                    if idx > 0:
                        combined_audio += inter_segment_pause
                    combined_audio += seg_audio
            else:
                # Stage 2: Fallback to gTTS
                engine_used = "gTTS"
                selected_voice = f"gTTS-{FALLBACK_GTTS_LANG}"
                combined_audio = AudioSegment.empty()
                loop = asyncio.get_running_loop()

                for idx, segment in enumerate(segments, start=1):
                    chunk_file = os.path.join(temp_dir, f"gtts_chunk_{idx:04d}.mp3")
                    ok = await loop.run_in_executor(
                        None, self._synthesize_gtts_segment, segment, chunk_file
                    )
                    if not ok:
                        raise RuntimeError(UI_MESSAGES_AR["error_generation_failed"])
                    try:
                        seg_audio = AudioSegment.from_file(chunk_file)
                        if idx > 1:
                            combined_audio += inter_segment_pause
                        combined_audio += seg_audio
                    except Exception as err:
                        raise RuntimeError(
                            f"{UI_MESSAGES_AR['error_generation_failed']}: {err}"
                        ) from err

            # Stage 3: Normalize Loudness
            normalized_audio = self._normalize_loudness(combined_audio)

            # Stage 4: Export to requested format (.wav or .mp3)
            out_ext = Path(abs_output_path).suffix.lower()
            if out_ext == ".wav":
                normalized_audio.export(
                    abs_output_path,
                    format="wav",
                )
            else:
                normalized_audio.export(
                    abs_output_path,
                    format="mp3",
                    bitrate="192k",
                )

        duration_seconds = len(normalized_audio) / 1000.0
        file_size = os.path.getsize(abs_output_path) if os.path.exists(abs_output_path) else 0

        self._log_generation(
            text=clean_text,
            voice=selected_voice,
            engine_used=engine_used,
            duration=duration_seconds,
            size_bytes=file_size,
            output_path=abs_output_path,
        )

        return AudioResult(
            audio_path=abs_output_path,
            duration_seconds=duration_seconds,
            sample_rate=normalized_audio.frame_rate,
            channels=normalized_audio.channels,
            engine_used=engine_used,
            voice=selected_voice,
            file_size_bytes=file_size,
            segments_count=len(segments),
            text_length=len(clean_text),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
