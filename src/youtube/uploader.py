"""
YouTube Video Uploader and Quota Management.

Production-ready resumable video uploader using YouTube Data API v3:
  - Resumable chunked upload with progress tracking
  - Strict privacy defaults (PRIVATE by default, explicit public authorization required)
  - Scheduled publishing support (publishAt with UTC validation)
  - Duplicate upload prevention via cryptographic file hash (SHA-256) and title checks
  - Resilient retry engine with exponential backoff (max 5 retries)
  - Daily API quota tracking and limits (1600 units per upload, 10,000 units daily default)
  - Thumbnail assignment and metadata updating
  - Processing status checks
  - Comprehensive Arabic and English error handling for the dashboard
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import random
import socket
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.youtube.auth import YouTubeAuth
from src.youtube.metadata_generator import VideoMetadata

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Arabic Error Messages for Dashboard UI
# ---------------------------------------------------------------------------

UPLOADER_MESSAGES_AR: Dict[str, str] = {
    "upload_success": "تم رفع الفيديو إلى YouTube بنجاح.",
    "upload_scheduled": "تم رفع الفيديو وجدولة نشره بنجاح.",
    "quota_exceeded": "تم تجاوز الحصة اليومية المتاحة لـ YouTube API (10,000 نقطة). يرجى الانتظار حتى إعادة التعيين.",
    "quota_insufficient": "رصيد الحصة اليومية غير كافٍ لإتمام الرفع (العملية تتطلب 1600 نقطة).",
    "auth_failure": "فشلت المصادقة مع YouTube. بيانات الاعتماد منتهية أو غير صالحة.",
    "duplicate_detected": "تم إلغاء العملية: الفيديو مكرر ومسجل مسبقاً في قاعدة البيانات.",
    "file_not_found": "ملف الفيديو المحدد غير موجود على القرص.",
    "file_invalid": "ملف الفيديو تالف أو فارغ (حجمه 0 بايت).",
    "file_too_large": "حجم ملف الفيديو يتجاوز الحد الأقصى المسموح به في YouTube (256 جيجابايت).",
    "metadata_invalid": "بيانات الفيديو الوصفية غير مطابقة لشروط YouTube (العنوان أو الوصف).",
    "network_error": "حدث انقطاع متكرر في الاتصال بالشبكة أثناء رفع الفيديو.",
    "upload_limit_exceeded": "تم الوصول إلى الحد اليومي لرفع الفيديوهات المسموح به لهذه القناة من YouTube.",
    "project_audit_required": "يتطلب تطبيق Google Cloud مراجعة أمنية (Project Audit) لرفع المزيد من الفيديوهات.",
    "public_not_authorized": (
        "تم تحويل خصوصية الفيديو تلقائياً إلى خاص (Private) لحماية القناة "
        "لعدم وجود تفويض صريح بالنشر العام."
    ),
    "thumbnail_success": "تم تعيين الصورة المصغرة للفيديو بنجاح.",
    "thumbnail_failed": "فشل تعيين الصورة المصغرة للفيديو.",
    "metadata_update_success": "تم تحديث البيانات الوصفية للفيديو بنجاح.",
    "metadata_update_failed": "فشل تحديث البيانات الوصفية للفيديو.",
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class UploadResult:
    """Represents the complete result of a video upload operation."""
    success: bool
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    status: str = "failed"  # 'uploaded', 'scheduled', 'duplicate', 'quota_exceeded', 'failed'
    title: str = ""
    privacy_status: str = "private"
    published_at: Optional[str] = None
    scheduled_time: Optional[str] = None
    quota_used: int = 0
    file_hash: Optional[str] = None
    error_message: Optional[str] = None
    error_message_ar: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert upload result to dictionary representation."""
        return asdict(self)


@dataclass
class QuotaStatus:
    """Represents daily YouTube Data API v3 quota usage and availability."""
    daily_limit: int = 10000
    used_today: int = 0
    remaining: int = 10000
    can_upload: bool = True
    reset_time: str = ""
    message: str = ""
    message_ar: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert quota status to dictionary representation."""
        return asdict(self)


# ---------------------------------------------------------------------------
# YouTube Uploader Class
# ---------------------------------------------------------------------------

class YouTubeUploader:
    """
    Automated, resilient YouTube video uploader.

    Enforces safe defaults:
      - Default privacy is PRIVATE.
      - Never publishes as PUBLIC without explicit caller authorization (allow_public=True).
      - Prevents duplicate uploads via SHA-256 content verification.
      - Monitors and tracks API quota usage against a daily ceiling.
      - Uses resumable chunked upload with retry and exponential backoff.
    """

    # Quota Costs per YouTube Data API v3
    QUOTA_COST_VIDEO_INSERT = 1600
    QUOTA_COST_THUMBNAIL_SET = 50
    QUOTA_COST_VIDEO_UPDATE = 50
    QUOTA_COST_READ = 1

    DEFAULT_DAILY_QUOTA_LIMIT = 10000
    MAX_FILE_SIZE_BYTES = 256 * 1024 * 1024 * 1024  # 256 GB YouTube maximum
    UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024             # 4 MB chunk size for resumable upload
    MAX_RETRIES = 5                                  # Maximum exponential backoff retries

    RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

    def __init__(
        self,
        auth: Optional[YouTubeAuth] = None,
        db_path: Optional[Union[str, Path]] = None,
        daily_quota_limit: int = DEFAULT_DAILY_QUOTA_LIMIT,
    ) -> None:
        """
        Initialize YouTubeUploader.

        Args:
            auth: Optional YouTubeAuth instance. If None, instantiates a default YouTubeAuth.
            db_path: Optional path to SQLite database for quota and duplicate tracking.
            daily_quota_limit: Maximum API quota units allowed per calendar day (UTC).
        """
        self.auth = auth or YouTubeAuth(db_path=db_path)
        self.db_path = Path(db_path).resolve() if db_path else self.auth.db_path
        self.daily_quota_limit = daily_quota_limit

        self._init_uploader_db()

    # -----------------------------------------------------------------------
    # Database Initialization & Helpers
    # -----------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Create and return a configured SQLite connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_uploader_db(self) -> None:
        """Initialize tables for quota usage and uploaded video duplicate tracking."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Quota Tracking Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS youtube_quota_usage (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date_utc TEXT NOT NULL,
                        units_used INTEGER NOT NULL,
                        action TEXT NOT NULL,
                        video_id TEXT,
                        timestamp TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_quota_date ON youtube_quota_usage(date_utc)"
                )

                # Upload History & Duplicate Prevention Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS uploaded_videos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        video_id TEXT UNIQUE NOT NULL,
                        title TEXT NOT NULL,
                        file_hash TEXT NOT NULL,
                        file_path TEXT,
                        file_size_bytes INTEGER,
                        privacy_status TEXT NOT NULL,
                        video_type TEXT NOT NULL,
                        category_id TEXT NOT NULL,
                        scheduled_time TEXT,
                        upload_time TEXT NOT NULL,
                        quota_used INTEGER NOT NULL
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_uploaded_hash ON uploaded_videos(file_hash)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_uploaded_title ON uploaded_videos(title)"
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to initialize uploader database tables: %s", e)

    # -----------------------------------------------------------------------
    # API Quota Monitoring
    # -----------------------------------------------------------------------

    def _record_quota_consumption(self, units: int, action: str, video_id: Optional[str] = None) -> None:
        """Log quota units consumed into the database."""
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            date_utc = now_utc.strftime("%Y-%m-%d")
            timestamp = now_utc.isoformat()

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO youtube_quota_usage (date_utc, units_used, action, video_id, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (date_utc, units, action, video_id, timestamp),
                )
                conn.commit()
            logger.info("Recorded %d quota units for action '%s' (video_id: %s)", units, action, video_id)
        except Exception as e:
            logger.error("Failed to log quota consumption to SQLite: %s", e)

    def check_upload_quota(self) -> QuotaStatus:
        """
        Check current YouTube API quota consumption for today (UTC).

        Returns:
            QuotaStatus instance indicating remaining units and upload viability.
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        date_utc = now_utc.strftime("%Y-%m-%d")

        # Midnight UTC reset time
        tomorrow_utc = (now_utc + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        reset_time_str = tomorrow_utc.isoformat()

        used_today = 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COALESCE(SUM(units_used), 0) AS total_used FROM youtube_quota_usage WHERE date_utc = ?",
                    (date_utc,),
                )
                row = cursor.fetchone()
                if row:
                    used_today = int(row["total_used"])
        except Exception as e:
            logger.error("Error reading quota usage from database: %s", e)

        remaining = max(0, self.daily_quota_limit - used_today)
        can_upload = remaining >= self.QUOTA_COST_VIDEO_INSERT

        if not can_upload:
            msg = (
                f"Insufficient quota remaining ({remaining}/{self.daily_quota_limit} units). "
                f"Needs {self.QUOTA_COST_VIDEO_INSERT}."
            )
            msg_ar = UPLOADER_MESSAGES_AR["quota_insufficient"]
        else:
            msg = f"Quota OK: {used_today} used, {remaining} remaining out of {self.daily_quota_limit}."
            msg_ar = f"الحصة كافية: تم استهلاك {used_today} وحدة، والمتبقي {remaining} وحدة."

        return QuotaStatus(
            daily_limit=self.daily_quota_limit,
            used_today=used_today,
            remaining=remaining,
            can_upload=can_upload,
            reset_time=reset_time_str,
            message=msg,
            message_ar=msg_ar,
        )

    # -----------------------------------------------------------------------
    # Duplicate Upload Prevention
    # -----------------------------------------------------------------------

    @staticmethod
    def calculate_file_hash(file_path: Union[str, Path]) -> str:
        """Compute SHA-256 checksum of a file using streaming 64KB chunks."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def check_duplicate(self, file_hash: str, title: str) -> Optional[Dict[str, Any]]:
        """
        Check if a video with the same SHA-256 hash or exact title has already been uploaded.

        Returns:
            Dictionary with existing video information if duplicate found, None otherwise.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Check file hash first
                cursor.execute(
                    """
                    SELECT video_id, title, file_hash, privacy_status, upload_time
                    FROM uploaded_videos
                    WHERE file_hash = ?
                    LIMIT 1
                    """,
                    (file_hash,),
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)

                # Check exact title within the last 14 days
                two_weeks_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=14)).isoformat()
                cursor.execute(
                    """
                    SELECT video_id, title, file_hash, privacy_status, upload_time
                    FROM uploaded_videos
                    WHERE title = ? AND upload_time >= ?
                    LIMIT 1
                    """,
                    (title.strip(), two_weeks_ago),
                )
                row_title = cursor.fetchone()
                if row_title:
                    return dict(row_title)

            return None
        except Exception as e:
            logger.error("Error checking duplicate in database: %s", e)
            return None

    def _record_uploaded_video(
        self,
        video_id: str,
        title: str,
        file_hash: str,
        file_path: str,
        file_size: int,
        privacy_status: str,
        video_type: str,
        category_id: str,
        scheduled_time: Optional[str],
        quota_used: int,
    ) -> None:
        """Record a successful video upload into the uploaded_videos table."""
        try:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO uploaded_videos (
                        video_id, title, file_hash, file_path, file_size_bytes,
                        privacy_status, video_type, category_id, scheduled_time,
                        upload_time, quota_used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        title=excluded.title,
                        privacy_status=excluded.privacy_status,
                        scheduled_time=excluded.scheduled_time
                    """,
                    (
                        video_id,
                        title,
                        file_hash,
                        file_path,
                        file_size,
                        privacy_status,
                        video_type,
                        category_id,
                        scheduled_time,
                        now_iso,
                        quota_used,
                    ),
                )
                conn.commit()
            logger.info("Recorded video upload record in database for video_id: %s", video_id)
        except Exception as e:
            logger.error("Failed to record uploaded video to database: %s", e)

    # -----------------------------------------------------------------------
    # Main Video Upload Flow
    # -----------------------------------------------------------------------

    def upload_video(
        self,
        video_path: Union[str, Path],
        metadata: Union[VideoMetadata, Dict[str, Any]],
        thumbnail_path: Optional[Union[str, Path]] = None,
        allow_public: bool = False,
        force_duplicate: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> UploadResult:
        """
        Upload a video to YouTube using resumable media upload.

        Args:
            video_path: Path to the local video file.
            metadata: VideoMetadata object or dictionary containing video metadata.
            thumbnail_path: Optional path to custom thumbnail to set after upload.
            allow_public: Security guard. If False and privacy_status is 'public',
                          automatically downgrades to 'private' to prevent accidental leaks.
            force_duplicate: If True, bypasses duplicate check.
            progress_callback: Optional callback func(uploaded_bytes, total_bytes).

        Returns:
            UploadResult dataclass with execution outcome.
        """
        path = Path(video_path).resolve()

        # 1. Validate Video File
        if not path.exists():
            return UploadResult(
                success=False,
                status="failed",
                error_message=f"Video file not found at: {path}",
                error_message_ar=UPLOADER_MESSAGES_AR["file_not_found"],
            )

        file_size = path.stat().st_size
        if file_size <= 0:
            return UploadResult(
                success=False,
                status="failed",
                error_message="Video file is empty (0 bytes).",
                error_message_ar=UPLOADER_MESSAGES_AR["file_invalid"],
            )

        if file_size > self.MAX_FILE_SIZE_BYTES:
            return UploadResult(
                success=False,
                status="failed",
                error_message=f"Video file exceeds 256GB limit ({file_size} bytes).",
                error_message_ar=UPLOADER_MESSAGES_AR["file_too_large"],
            )

        # 2. Standardize & Validate Metadata
        if isinstance(metadata, dict):
            # Parse dict into VideoMetadata
            video_meta = VideoMetadata(
                title=metadata.get("title", "Untitled Video"),
                description=metadata.get("description", ""),
                tags=metadata.get("tags", []),
                category_id=str(metadata.get("category_id") or metadata.get("categoryId") or "28"),
                privacy_status=metadata.get("privacy_status") or metadata.get("privacyStatus") or "private",
                made_for_kids=metadata.get("made_for_kids", False),
                scheduled_time=metadata.get("scheduled_time") or metadata.get("publishAt"),
                video_type=metadata.get("video_type", "regular"),
            )
        else:
            video_meta = metadata

        # Security: Enforce Private Default & Check allow_public
        if video_meta.privacy_status.lower() == "public" and not allow_public:
            logger.warning(
                "Public upload requested but allow_public=False. "
                "Enforcing privacy_status='private' for safety."
            )
            video_meta.privacy_status = "private"

        # Validate metadata against YouTube rules
        is_valid, validation_errors = video_meta.validate()
        if not is_valid:
            return UploadResult(
                success=False,
                status="failed",
                error_message=f"Metadata validation error: {'; '.join(validation_errors)}",
                error_message_ar=UPLOADER_MESSAGES_AR["metadata_invalid"],
            )

        # 3. Check Duplicate Prevention
        file_hash = self.calculate_file_hash(path)
        if not force_duplicate:
            dup_info = self.check_duplicate(file_hash, video_meta.title)
            if dup_info:
                logger.warning(
                    "Duplicate video detected! Matching video_id: %s (Title: %s)",
                    dup_info["video_id"],
                    dup_info["title"],
                )
                return UploadResult(
                    success=False,
                    video_id=dup_info["video_id"],
                    status="duplicate",
                    title=video_meta.title,
                    file_hash=file_hash,
                    error_message=f"Duplicate video already uploaded as video_id: {dup_info['video_id']}",
                    error_message_ar=UPLOADER_MESSAGES_AR["duplicate_detected"],
                    details=dup_info,
                )

        # 4. Check API Quota Availability
        quota_status = self.check_upload_quota()
        if not quota_status.can_upload:
            return UploadResult(
                success=False,
                status="quota_exceeded",
                error_message=quota_status.message,
                error_message_ar=quota_status.message_ar,
            )

        # 5. Check Authentication
        creds = self.auth.get_credentials()
        if not creds:
            return UploadResult(
                success=False,
                status="failed",
                error_message="Not authenticated with YouTube API.",
                error_message_ar=UPLOADER_MESSAGES_AR["auth_failure"],
            )

        # 6. Execute Resumable Upload with Exponential Backoff
        try:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            body = video_meta.to_api_body()

            media = MediaFileUpload(
                str(path),
                mimetype="video/*",
                chunksize=self.UPLOAD_CHUNK_SIZE,
                resumable=True,
            )

            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = self._execute_resumable_upload(request, progress_callback)
            video_id = response.get("id")

            if not video_id:
                return UploadResult(
                    success=False,
                    status="failed",
                    error_message="Upload completed but no video ID returned from API.",
                    error_message_ar="اكتمل الرفع ولكن لم يُرجع YouTube رقم تعريف للفيديو.",
                    details=response,
                )

            # Record Quota Used
            self._record_quota_consumption(self.QUOTA_COST_VIDEO_INSERT, "video_upload", video_id)

            # Record in Upload History DB
            self._record_uploaded_video(
                video_id=video_id,
                title=video_meta.title,
                file_hash=file_hash,
                file_path=str(path),
                file_size=file_size,
                privacy_status=video_meta.privacy_status,
                video_type=video_meta.video_type,
                category_id=video_meta.category_id,
                scheduled_time=video_meta.scheduled_time,
                quota_used=self.QUOTA_COST_VIDEO_INSERT,
            )

            # 7. Optionally Set Thumbnail
            if thumbnail_path:
                try:
                    self.set_thumbnail(video_id, thumbnail_path)
                except Exception as th_err:
                    logger.warning("Failed to automatically set thumbnail for %s: %s", video_id, th_err)

            status_str = "scheduled" if video_meta.scheduled_time else "uploaded"
            video_url = f"https://youtu.be/{video_id}"

            return UploadResult(
                success=True,
                video_id=video_id,
                video_url=video_url,
                status=status_str,
                title=video_meta.title,
                privacy_status=video_meta.privacy_status,
                published_at=response.get("snippet", {}).get("publishedAt"),
                scheduled_time=video_meta.scheduled_time,
                quota_used=self.QUOTA_COST_VIDEO_INSERT,
                file_hash=file_hash,
                details=response,
            )

        except HttpError as he:
            return self._handle_http_error(he, video_meta.title)
        except Exception as e:
            logger.error("Unexpected error during video upload: %s", e)
            return UploadResult(
                success=False,
                status="failed",
                title=video_meta.title,
                error_message=str(e),
                error_message_ar=UPLOADER_MESSAGES_AR["network_error"],
            )

    # -----------------------------------------------------------------------
    # Resumable Upload Execution Loop
    # -----------------------------------------------------------------------

    def _execute_resumable_upload(
        self,
        request: Any,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the chunk-by-chunk resumable upload loop with exponential backoff on retryable errors.
        """
        response = None
        retries = 0

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress_pct = int(status.progress() * 100)
                    logger.info(
                        "Upload progress: %d%% (%d / %d bytes)",
                        progress_pct,
                        status.resumable_progress,
                        status.total_size,
                    )
                    if progress_callback:
                        progress_callback(status.resumable_progress, status.total_size)
                # Successful chunk reset retries counter
                retries = 0

            except HttpError as err:
                if err.resp.status in self.RETRIABLE_STATUS_CODES:
                    retries += 1
                    if retries > self.MAX_RETRIES:
                        logger.error("Maximum retries (%d) reached for HTTP %d", self.MAX_RETRIES, err.resp.status)
                        raise
                    sleep_time = min(60.0, (2 ** retries) + random.uniform(0.5, 1.5))
                    logger.warning("Retryable HTTP %d error. Backing off for %.2fs...", err.resp.status, sleep_time)
                    time.sleep(sleep_time)
                else:
                    # Non-retryable error
                    raise

            except (socket.error, httplib2.HttpLib2Error, ConnectionResetError) as net_err:
                retries += 1
                if retries > self.MAX_RETRIES:
                    logger.error("Maximum retries (%d) reached for network socket error: %s", self.MAX_RETRIES, net_err)
                    raise
                sleep_time = min(60.0, (2 ** retries) + random.uniform(0.5, 1.5))
                logger.warning("Network connection error (%s). Backing off for %.2fs...", net_err, sleep_time)
                time.sleep(sleep_time)

        return response

    # -----------------------------------------------------------------------
    # Thumbnail Management
    # -----------------------------------------------------------------------

    def set_thumbnail(self, video_id: str, thumbnail_path: Union[str, Path]) -> bool:
        """
        Set a custom thumbnail for an existing YouTube video.

        Args:
            video_id: The 11-character YouTube video ID.
            thumbnail_path: Path to the thumbnail image file (JPG or PNG, max 2MB).

        Returns:
            True if thumbnail was set successfully, False otherwise.
        """
        path = Path(thumbnail_path).resolve()
        if not path.exists():
            logger.error("Thumbnail file does not exist: %s", path)
            return False

        if path.stat().st_size > 2 * 1024 * 1024:
            logger.error("Thumbnail file exceeds 2MB limit: %d bytes", path.stat().st_size)
            return False

        creds = self.auth.get_credentials()
        if not creds:
            logger.error("Cannot set thumbnail: Not authenticated.")
            return False

        try:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            media = MediaFileUpload(str(path), mimetype="image/jpeg", resumable=False)
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=media,
            ).execute()

            self._record_quota_consumption(self.QUOTA_COST_THUMBNAIL_SET, "thumbnail_upload", video_id)
            logger.info("Custom thumbnail set successfully for video_id: %s", video_id)
            return True
        except HttpError as he:
            logger.error("Failed to set thumbnail for %s (HTTP %d): %s", video_id, he.resp.status, he)
            return False
        except Exception as e:
            logger.error("Unexpected error setting thumbnail for %s: %s", video_id, e)
            return False

    # -----------------------------------------------------------------------
    # Metadata Updating
    # -----------------------------------------------------------------------

    def update_video_metadata(
        self,
        video_id: str,
        metadata: Union[VideoMetadata, Dict[str, Any]],
    ) -> bool:
        """
        Update the snippet and status metadata of an already uploaded video.

        Args:
            video_id: The YouTube video ID.
            metadata: VideoMetadata instance or dictionary with updated fields.

        Returns:
            True if metadata updated successfully, False otherwise.
        """
        creds = self.auth.get_credentials()
        if not creds:
            logger.error("Cannot update metadata: Not authenticated.")
            return False

        try:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

            if isinstance(metadata, VideoMetadata):
                body = metadata.to_api_body()
            else:
                body = {
                    "snippet": {
                        "title": metadata.get("title"),
                        "description": metadata.get("description"),
                        "tags": metadata.get("tags", []),
                        "categoryId": str(metadata.get("category_id") or metadata.get("categoryId") or "28"),
                    },
                    "status": {
                        "privacyStatus": metadata.get("privacy_status") or metadata.get("privacyStatus") or "private",
                    },
                }
            body["id"] = video_id

            youtube.videos().update(
                part="snippet,status",
                body=body,
            ).execute()

            self._record_quota_consumption(self.QUOTA_COST_VIDEO_UPDATE, "metadata_update", video_id)
            logger.info("Successfully updated metadata for video_id: %s", video_id)
            return True
        except HttpError as he:
            logger.error("Failed to update metadata for %s: %s", video_id, he)
            return False
        except Exception as e:
            logger.error("Unexpected error updating metadata for %s: %s", video_id, e)
            return False

    # -----------------------------------------------------------------------
    # Video Status & Processing Check
    # -----------------------------------------------------------------------

    def get_video_status(self, video_id: str) -> Dict[str, Any]:
        """
        Retrieve upload and processing status for a video from YouTube Data API v3.

        Returns:
            Dictionary with processing status, upload status, privacy status, and failure reason.
        """
        creds = self.auth.get_credentials()
        if not creds:
            return {"success": False, "error": "Not authenticated"}

        try:
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            response = youtube.videos().list(
                part="snippet,status,processingDetails,contentDetails",
                id=video_id,
            ).execute()

            items = response.get("items", [])
            if not items:
                return {"success": False, "error": f"Video not found: {video_id}"}

            item = items[0]
            status_obj = item.get("status", {})
            proc_obj = item.get("processingDetails", {})
            snippet = item.get("snippet", {})

            return {
                "success": True,
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "upload_status": status_obj.get("uploadStatus", ""),  # 'uploaded', 'processed', 'failed', 'rejected'
                "privacy_status": status_obj.get("privacyStatus", ""),
                "processing_status": proc_obj.get("processingStatus", ""),  # 'processing', 'succeeded', 'failed'
                "rejection_reason": status_obj.get("rejectionReason", None),
                "failure_reason": status_obj.get("failureReason", None),
                "published_at": snippet.get("publishedAt", ""),
            }
        except Exception as e:
            logger.error("Failed to get status for video %s: %s", video_id, e)
            return {"success": False, "error": str(e)}

    # -----------------------------------------------------------------------
    # Error Diagnostics
    # -----------------------------------------------------------------------

    def _handle_http_error(self, he: HttpError, title: str) -> UploadResult:
        """Parse YouTube API HttpError and map to user-friendly Arabic diagnostics."""
        status_code = he.resp.status
        reason = ""
        try:
            error_details = json.loads(he.content.decode("utf-8"))
            errors_list = error_details.get("error", {}).get("errors", [])
            if errors_list:
                reason = errors_list[0].get("reason", "")
        except Exception:
            reason = str(he)

        logger.error("YouTube API HTTP %d error (reason: %s): %s", status_code, reason, he)

        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            return UploadResult(
                success=False,
                status="quota_exceeded",
                title=title,
                error_message=f"YouTube API quota exceeded (HTTP {status_code}).",
                error_message_ar=UPLOADER_MESSAGES_AR["quota_exceeded"],
            )

        if reason == "uploadLimitExceeded":
            return UploadResult(
                success=False,
                status="failed",
                title=title,
                error_message="Daily upload limit exceeded for this YouTube channel.",
                error_message_ar=UPLOADER_MESSAGES_AR["upload_limit_exceeded"],
            )

        if "audit" in str(he).lower():
            return UploadResult(
                success=False,
                status="failed",
                title=title,
                error_message="Project audit required by Google Cloud.",
                error_message_ar=UPLOADER_MESSAGES_AR["project_audit_required"],
            )

        if status_code in (401, 403) and reason in ("invalid_grant", "unauthorized"):
            return UploadResult(
                success=False,
                status="failed",
                title=title,
                error_message=f"Authentication failure with YouTube API (HTTP {status_code}).",
                error_message_ar=UPLOADER_MESSAGES_AR["auth_failure"],
            )

        return UploadResult(
            success=False,
            status="failed",
            title=title,
            error_message=f"YouTube API error (HTTP {status_code}): {reason}",
            error_message_ar=UPLOADER_MESSAGES_AR["network_error"],
            details={"status_code": status_code, "reason": reason},
        )
