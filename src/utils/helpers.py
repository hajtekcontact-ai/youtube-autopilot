"""Utility helper functions for YouTube AI Autopilot.

Provides datetime timezone conversions (America/New_York), schedule slot calculations,
safe file sanitization, formatters, video validation, decorators, and Arabic UI helpers.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Type, Union

import pytz

logger = logging.getLogger(__name__)

NY_TIMEZONE_STR = "America/New_York"
NY_TZ = pytz.timezone(NY_TIMEZONE_STR)

# Arabic-Indic digit translation table (0 -> ٠, etc.)
ARABIC_DIGIT_MAP = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def get_ny_time() -> datetime:
    """Get the current datetime localized to America/New_York timezone.

    Returns:
        datetime: Timezone-aware datetime in America/New_York.
    """
    return datetime.now(NY_TZ)


def utc_to_ny(dt: datetime) -> datetime:
    """Convert a UTC datetime to America/New_York timezone.

    If dt is naive (no tzinfo), it is assumed to be in UTC.

    Args:
        dt: Datetime object to convert.

    Returns:
        datetime: Datetime localized to America/New_York.
    """
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(NY_TZ)


def ny_to_utc(dt: datetime) -> datetime:
    """Convert an America/New_York datetime to UTC.

    If dt is naive (no tzinfo), it is assumed to be in America/New_York.

    Args:
        dt: Datetime object to convert.

    Returns:
        datetime: Datetime localized to UTC.
    """
    if dt.tzinfo is None:
        dt = NY_TZ.localize(dt)
    return dt.astimezone(pytz.utc)


def get_schedule_slots() -> List[Dict[str, Any]]:
    """Return the 5 fixed daily publishing time slots (9, 12, 15, 18, 21 NY time).

    Returns:
        List[Dict[str, Any]]: List of slot specifications with hour, formatted time,
                              default video type, and descriptive English/Arabic label.
    """
    return [
        {
            "slot_index": 0,
            "hour": 9,
            "slot_time": "09:00",
            "video_type": "short",
            "name": "Morning Short",
            "label_ar": "فيديو قصير الصباح (09:00 بتوقيت نيويورك)",
        },
        {
            "slot_index": 1,
            "hour": 12,
            "slot_time": "12:00",
            "video_type": "short",
            "name": "Noon Short",
            "label_ar": "فيديو قصير الظهيرة (12:00 بتوقيت نيويورك)",
        },
        {
            "slot_index": 2,
            "hour": 15,
            "slot_time": "15:00",
            "video_type": "short",
            "name": "Afternoon Short",
            "label_ar": "فيديو قصير بعد الظهر (15:00 بتوقيت نيويورك)",
        },
        {
            "slot_index": 3,
            "hour": 18,
            "slot_time": "18:00",
            "video_type": "long",
            "name": "Prime Long Video",
            "label_ar": "فيديو طويل أساسي (18:00 بتوقيت نيويورك)",
        },
        {
            "slot_index": 4,
            "hour": 21,
            "slot_time": "21:00",
            "video_type": "short",
            "name": "Evening Short",
            "label_ar": "فيديو قصير المساء (21:00 بتوقيت نيويورك)",
        },
    ]


def generate_unique_id(prefix: str = "") -> str:
    """Generate a unique UUID4-based identifier.

    Args:
        prefix: Optional prefix for the ID (e.g. 'vid', 'job', 'idea').

    Returns:
        str: Formatted unique ID string.
    """
    uid = str(uuid.uuid4())
    if prefix:
        clean_prefix = re.sub(r"[^a-zA-Z0-9_-]", "", prefix).strip("_")
        return f"{clean_prefix}_{uid}"
    return uid


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Sanitize a filename for safe cross-platform filesystem storage.

    Strips path traversal elements, removes forbidden characters, preserves
    Arabic characters, replaces whitespace with underscores, and retains extensions.

    Args:
        name: Proposed filename.
        max_length: Maximum allowed length for the base name.

    Returns:
        str: Clean, safe filename.
    """
    if not name:
        return "unnamed_file"

    # Separate extension if present
    path_obj = Path(name)
    stem = path_obj.stem
    ext = path_obj.suffix.lower()

    # Normalize unicode (keeps Arabic characters intact while composing accents)
    stem = unicodedata.normalize("NFKC", stem)

    # Disallow path traversal sequences
    stem = stem.replace("..", "").replace("/", "").replace("\\", "")

    # Remove filesystem dangerous characters
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem)

    # Replace consecutive spaces or dashes/underscores with a single underscore
    stem = re.sub(r"[\s\-]+", "_", stem).strip("._ ")

    if not stem:
        stem = f"file_{uuid.uuid4().hex[:8]}"

    # Enforce maximum length
    if len(stem) > max_length:
        stem = stem[:max_length].rstrip("._ ")

    # Sanitize extension
    ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)

    return f"{stem}{ext}"


def format_duration(seconds: Optional[Union[float, int]]) -> str:
    """Format duration in seconds to a human-readable HH:MM:SS or MM:SS format.

    Args:
        seconds: Duration in seconds.

    Returns:
        str: Formatted duration string (e.g., '00:45', '12:30', '01:15:20').
    """
    if seconds is None or seconds < 0:
        return "00:00"

    total_sec = int(round(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_file_size(bytes_size: Optional[Union[int, float]]) -> str:
    """Format a byte size into human-readable units (B, KB, MB, GB, TB).

    Args:
        bytes_size: File size in bytes.

    Returns:
        str: Human-readable file size string (e.g. '12.5 MB').
    """
    if bytes_size is None or bytes_size < 0:
        return "0 B"

    size = float(bytes_size)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    unit_idx = 0

    while size >= 1024.0 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[unit_idx]}"


def truncate_text(text: str, max_len: int, ellipsis: str = "...") -> str:
    """Intelligently truncate text at word boundaries without breaking words.

    Args:
        text: Input string.
        max_len: Maximum allowed length including the ellipsis.
        ellipsis: Trailing indicator string.

    Returns:
        str: Truncated text.
    """
    if not text:
        return ""

    text = text.strip()
    if len(text) <= max_len:
        return text

    target_len = max_len - len(ellipsis)
    if target_len <= 0:
        return text[:max_len]

    # Look for last space within target length
    cutoff = text[:target_len]
    last_space = cutoff.rfind(" ")
    if last_space > target_len // 2:
        return cutoff[:last_space].rstrip(",.;:!? ") + ellipsis
    return cutoff.rstrip(",.;:!? ") + ellipsis


def is_valid_video_file(path: Union[str, Path]) -> bool:
    """Verify whether a file path exists, is non-empty, and has a valid MP4/video header.

    Checks:
    - Path existence and regular file status
    - Minimum size threshold (> 1024 bytes)
    - Presence of MP4/QuickTime 'ftyp' or 'moov' atom in header bytes

    Args:
        path: Path to the video file.

    Returns:
        bool: True if file appears to be a valid video container, False otherwise.
    """
    file_path = Path(path)
    try:
        if not file_path.is_file():
            return False

        file_size = file_path.stat().st_size
        if file_size < 1024:
            return False

        # Read the first 128 bytes to check container headers
        with open(file_path, "rb") as f:
            header = f.read(128)

        # Common MP4 / MOV signatures: 'ftyp', 'moov', 'mdat'
        signatures = [b"ftyp", b"moov", b"mdat", b"\x1a\x45\xdf\xa3"]  # also includes MKV/WebM header
        return any(sig in header for sig in signatures)
    except (OSError, IOError, PermissionError) as exc:
        logger.warning("Error inspecting video file %s: %s", path, exc)
        return False


def retry_with_backoff(
    func: Optional[Callable[..., Any]] = None,
    *,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Sequence[Type[BaseException]] = (Exception,),
) -> Callable[..., Any]:
    """Decorator to retry a function (synchronous or async) with exponential backoff.

    Args:
        func: Target function or coroutine.
        max_retries: Maximum number of retry attempts after first failure.
        initial_delay: Initial sleep duration in seconds before first retry.
        backoff_factor: Multiplier for consecutive delay increments.
        exceptions: Sequence of exception types to catch and retry.

    Returns:
        Callable: Wrapped function with retry logic.
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                delay = initial_delay
                last_exc: Optional[BaseException] = None
                for attempt in range(1, max_retries + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except tuple(exceptions) as exc:
                        last_exc = exc
                        if attempt == max_retries:
                            logger.error(
                                "Async function %s failed after %d attempts: %s",
                                fn.__name__, attempt, exc
                            )
                            raise
                        logger.warning(
                            "Attempt %d/%d for async %s failed (%s). Retrying in %.2fs...",
                            attempt, max_retries, fn.__name__, exc, delay
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor
                if last_exc:
                    raise last_exc
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exc: Optional[BaseException] = None
            for attempt in range(1, max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except tuple(exceptions) as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        logger.error(
                            "Function %s failed after %d attempts: %s",
                            fn.__name__, attempt, exc
                        )
                        raise
                    logger.warning(
                        "Attempt %d/%d for %s failed (%s). Retrying in %.2fs...",
                        attempt, max_retries, fn.__name__, exc, delay
                    )
                    time.sleep(delay)
                    delay *= backoff_factor
            if last_exc:
                raise last_exc
        return sync_wrapper

    if func is not None:
        return decorator(func)
    return decorator


def hash_content(text: str) -> str:
    """Generate SHA-256 hex digest for text content deduplication.

    Args:
        text: String content to hash.

    Returns:
        str: 64-character lowercase hexadecimal hash.
    """
    normalized = (text or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def safe_json_loads(text: Optional[Union[str, bytes]], default: Any = None) -> Any:
    """Safely parse a JSON string or bytes without raising exceptions.

    Args:
        text: JSON string, bytes, or None.
        default: Fallback value returned on parsing error or empty string.

    Returns:
        Any: Decoded Python object or the provided default.
    """
    if text is None:
        return default
    if isinstance(text, (str, bytes)) and len(text.strip()) == 0:
        return default

    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return default


def ensure_dir(path: Union[str, Path]) -> Path:
    """Ensure directory exists, creating parent directories as necessary.

    Args:
        path: Directory path.

    Returns:
        Path: Resolved Path object of the created or verified directory.
    """
    p = Path(path).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def arabic_number(n: Union[int, float, str]) -> str:
    """Convert Western Arabic numerals (0-9) to Eastern Arabic-Indic numerals (٠-٩).

    Args:
        n: Number or string containing digits.

    Returns:
        str: String with digits converted to Eastern Arabic numerals.
    """
    if n is None:
        return ""
    str_val = str(n)
    return str_val.translate(ARABIC_DIGIT_MAP)
