"""YouTube Analytics Collector Module.

Collects, processes, and persists video and channel performance metrics
from the YouTube Analytics API and YouTube Data API v3 with automatic fallback,
rate limiting, and multi-window evaluation (48h, 7d, 28d).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Required OAuth Scopes
ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_DB_PATH = os.getenv("DATABASE_URL", "data/autopilot.db").replace("sqlite:///", "")


@dataclass
class AnalyticsSnapshot:
    """Represents a point-in-time performance snapshot for a YouTube video.

    Attributes:
        video_id: Internal video identifier.
        youtube_video_id: YouTube alphanumeric video ID.
        window: Evaluation window ('48h', '7d', '28d', or 'current').
        snapshot_date: Date string (YYYY-MM-DD) for which metrics are recorded.
        views: Total view count.
        estimated_minutes_watched: Aggregate watch time in minutes.
        average_view_duration: Average view duration in seconds.
        subscribers_gained: Net subscribers gained from this video.
        subscribers_lost: Net subscribers lost from this video.
        likes: Total like count.
        dislikes: Total dislike count.
        shares: Total share count.
        comments: Total comment count.
        impressions: Thumbnail impressions shown to viewers.
        impression_ctr: Click-through rate percentage (0.0 - 100.0).
        annotation_ctr: Interactive element/card click-through rate.
        raw_data: Complete dictionary of metrics returned from upstream API.
        source: Source of metrics ('youtube_analytics' or 'youtube_data_api_fallback').
        created_at: ISO timestamp when the record was persisted.
        id: Primary key in the database if persisted.
    """

    video_id: str
    youtube_video_id: str
    window: str
    snapshot_date: str
    views: int = 0
    estimated_minutes_watched: float = 0.0
    average_view_duration: float = 0.0
    subscribers_gained: int = 0
    subscribers_lost: int = 0
    likes: int = 0
    dislikes: int = 0
    shares: int = 0
    comments: int = 0
    impressions: int = 0
    impression_ctr: float = 0.0
    annotation_ctr: float = 0.0
    raw_data: Dict[str, Any] = field(default_factory=dict)
    source: str = "youtube_analytics"
    created_at: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert snapshot dataclass to serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalyticsSnapshot:
        """Instantiate snapshot from an SQLite row."""
        raw_data_raw = row["raw_data"]
        raw_data = json.loads(raw_data_raw) if raw_data_raw else {}
        return cls(
            id=row["id"],
            video_id=row["video_id"],
            youtube_video_id=row["youtube_video_id"],
            window=row["window"],
            snapshot_date=row["snapshot_date"],
            views=row["views"],
            estimated_minutes_watched=row["estimated_minutes_watched"],
            average_view_duration=row["average_view_duration"],
            subscribers_gained=row["subscribers_gained"],
            subscribers_lost=row["subscribers_lost"],
            likes=row["likes"],
            dislikes=row["dislikes"],
            shares=row["shares"],
            comments=row["comments"],
            impressions=row["impressions"],
            impression_ctr=row["impression_ctr"],
            annotation_ctr=row["annotation_ctr"],
            raw_data=raw_data,
            source=row["source"],
            created_at=row["created_at"],
        )


class QuotaManager:
    """Tracks and regulates daily YouTube API quota consumption."""

    def __init__(self, db_path: str, max_daily_quota: int = 10000) -> None:
        """Initialize QuotaManager.

        Args:
            db_path: Path to SQLite database file.
            max_daily_quota: Maximum allowable units per 24h window (default: 10,000).
        """
        self.db_path = db_path
        self.max_daily_quota = max_daily_quota
        self._init_table()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_quota_tracker (
                    date_str TEXT NOT NULL,
                    api_name TEXT NOT NULL,
                    units_used INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT NOT NULL,
                    PRIMARY KEY (date_str, api_name)
                );
                """
            )
            conn.commit()

    def check_available(self, api_name: str, requested_units: int = 1) -> bool:
        """Check if requested units can be safely spent without exceeding limits."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT units_used FROM api_quota_tracker WHERE date_str = ? AND api_name = ?",
                (today, api_name),
            )
            row = cur.fetchone()
            used = row["units_used"] if row else 0
            return (used + requested_units) <= self.max_daily_quota

    def record_usage(self, api_name: str, units: int = 1) -> None:
        """Record consumption of API quota units."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO api_quota_tracker (date_str, api_name, units_used, last_used_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date_str, api_name) DO UPDATE SET
                    units_used = units_used + excluded.units_used,
                    last_used_at = excluded.last_used_at;
                """,
                (today, api_name, units, now_iso),
            )
            conn.commit()

    def get_remaining_quota(self, api_name: str = "youtube_data") -> int:
        """Return the estimated quota remaining for today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT units_used FROM api_quota_tracker WHERE date_str = ? AND api_name = ?",
                (today, api_name),
            )
            row = cur.fetchone()
            used = row["units_used"] if row else 0
            return max(0, self.max_daily_quota - used)


class AnalyticsCollector:
    """Collects YouTube video and channel performance data.

    Manages OAuth credentials, communicates with YouTube Analytics API v2
    and YouTube Data API v3, handles rate limiting and quotas, and stores
    structured performance snapshots.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        credentials: Optional[Credentials] = None,
        credentials_path: Optional[str] = None,
        max_daily_quota: int = 10000,
        requests_per_minute: int = 60,
    ) -> None:
        """Initialize the AnalyticsCollector.

        Args:
            db_path: Path to SQLite database file.
            credentials: Pre-authorized google.oauth2.credentials.Credentials instance.
            credentials_path: Path to token.json or service account credentials.
            max_daily_quota: Daily quota threshold for YouTube Data API.
            requests_per_minute: Throttle limit for API calls.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self._custom_credentials = credentials
        self.credentials_path = credentials_path or os.getenv("YOUTUBE_TOKEN_PATH", "token.json")
        self.quota_manager = QuotaManager(self.db_path, max_daily_quota=max_daily_quota)
        self.requests_per_minute = max(1, requests_per_minute)
        self._min_interval = 60.0 / self.requests_per_minute
        self._last_request_time = 0.0

        self._analytics_service: Optional[Resource] = None
        self._data_service: Optional[Resource] = None

        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtain a thread-safe connection with dictionary row access."""
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Create necessary database tables if they do not exist."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    youtube_video_id TEXT NOT NULL,
                    window TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    estimated_minutes_watched REAL NOT NULL DEFAULT 0.0,
                    average_view_duration REAL NOT NULL DEFAULT 0.0,
                    subscribers_gained INTEGER NOT NULL DEFAULT 0,
                    subscribers_lost INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    dislikes INTEGER NOT NULL DEFAULT 0,
                    shares INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    impressions INTEGER NOT NULL DEFAULT 0,
                    impression_ctr REAL NOT NULL DEFAULT 0.0,
                    annotation_ctr REAL NOT NULL DEFAULT 0.0,
                    raw_data TEXT,
                    source TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Ensure schema migration if table pre-existed from another model
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(analytics_snapshots);")
            existing_cols = {row[1] for row in cursor.fetchall()}
            needed_cols = [
                ("youtube_video_id", "TEXT NOT NULL DEFAULT ''"),
                ("window", "TEXT NOT NULL DEFAULT '48h'"),
                ("estimated_minutes_watched", "REAL NOT NULL DEFAULT 0.0"),
                ("subscribers_lost", "INTEGER NOT NULL DEFAULT 0"),
                ("likes", "INTEGER NOT NULL DEFAULT 0"),
                ("dislikes", "INTEGER NOT NULL DEFAULT 0"),
                ("shares", "INTEGER NOT NULL DEFAULT 0"),
                ("comments", "INTEGER NOT NULL DEFAULT 0"),
                ("impression_ctr", "REAL NOT NULL DEFAULT 0.0"),
                ("annotation_ctr", "REAL NOT NULL DEFAULT 0.0"),
                ("raw_data", "TEXT"),
                ("source", "TEXT NOT NULL DEFAULT 'api'"),
            ]
            for col_name, col_type in needed_cols:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE analytics_snapshots ADD COLUMN {col_name} {col_type};")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_collection_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    window TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    execution_time_ms REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_video_id ON analytics_snapshots(video_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_yt_id ON analytics_snapshots(youtube_video_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_window ON analytics_snapshots(window);")
            conn.commit()

    def _rate_limit(self) -> None:
        """Enforce inter-request spacing to respect rate limits."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _get_credentials(self) -> Optional[Credentials]:
        """Resolve OAuth credentials from instance, file, or environment."""
        if self._custom_credentials and self._custom_credentials.valid:
            return self._custom_credentials

        if self._custom_credentials and self._custom_credentials.expired and self._custom_credentials.refresh_token:
            try:
                from google.auth.transport.requests import Request
                self._custom_credentials.refresh(Request())
                return self._custom_credentials
            except GoogleAuthError as exc:
                logger.warning("Failed refreshing provided credentials: %s", exc)

        # Attempt loading from credentials_path
        if os.path.exists(self.credentials_path):
            try:
                with open(self.credentials_path, "r", encoding="utf-8") as f:
                    token_info = json.load(f)
                creds = Credentials.from_authorized_user_info(token_info, scopes=ANALYTICS_SCOPES)
                if creds.expired and creds.refresh_token:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                self._custom_credentials = creds
                return creds
            except Exception as exc:
                logger.warning("Failed to load credentials from %s: %s", self.credentials_path, exc)

        # Attempt environment variable fallback
        client_id = os.getenv("YOUTUBE_CLIENT_ID")
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
        refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
        if client_id and client_secret and refresh_token:
            try:
                creds = Credentials(
                    token=None,
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=ANALYTICS_SCOPES,
                )
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                self._custom_credentials = creds
                return creds
            except Exception as exc:
                logger.error("Failed creating credentials from environment: %s", exc)

        return None

    def _get_analytics_client(self) -> Optional[Resource]:
        """Instantiate and cache YouTube Analytics API v2 client."""
        if self._analytics_service is not None:
            return self._analytics_service

        creds = self._get_credentials()
        if not creds:
            logger.warning("No OAuth credentials available for YouTube Analytics API.")
            return None

        try:
            self._analytics_service = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
            return self._analytics_service
        except Exception as exc:
            logger.error("Failed to build YouTube Analytics client: %s", exc)
            return None

    def _get_data_client(self) -> Optional[Resource]:
        """Instantiate and cache YouTube Data API v3 client."""
        if self._data_service is not None:
            return self._data_service

        creds = self._get_credentials()
        try:
            if creds:
                self._data_service = build("youtube", "v3", credentials=creds, cache_discovery=False)
            else:
                # Fallback to API Key if available
                api_key = os.getenv("YOUTUBE_API_KEY")
                if api_key:
                    self._data_service = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
                else:
                    logger.warning("Neither OAuth credentials nor YOUTUBE_API_KEY available for Data API.")
                    return None
            return self._data_service
        except Exception as exc:
            logger.error("Failed to build YouTube Data client: %s", exc)
            return None

    def _log_attempt(
        self,
        target_type: str,
        target_id: str,
        window: Optional[str],
        source: str,
        status: str,
        error_message: Optional[str],
        duration_ms: float,
    ) -> None:
        """Record an analytics collection attempt in the database log."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO analytics_collection_logs
                    (target_type, target_id, window, source, status, error_message, execution_time_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (target_type, target_id, window, source, status, error_message, duration_ms),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("Failed logging collection attempt: %s", exc)

    def _fetch_video_publish_date(self, youtube_video_id: str) -> Optional[datetime]:
        """Fetch video publish timestamp from YouTube Data API."""
        client = self._get_data_client()
        if not client or not self.quota_manager.check_available("youtube_data", 1):
            return None

        try:
            self._rate_limit()
            response = client.videos().list(part="snippet", id=youtube_video_id).execute()
            self.quota_manager.record_usage("youtube_data", 1)
            items = response.get("items", [])
            if items:
                pub_str = items[0]["snippet"]["publishedAt"]
                # Parse ISO timestamp like 2026-10-15T09:00:00Z
                return datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        except Exception as exc:
            logger.warning("Could not fetch publish date for %s: %s", youtube_video_id, exc)
        return None

    def _query_analytics_api_for_video(
        self, youtube_video_id: str, start_date: str, end_date: str
    ) -> Optional[Dict[str, Any]]:
        """Query YouTube Analytics API reports endpoint for a specific video."""
        service = self._get_analytics_client()
        if not service:
            return None

        # Standard core metrics supported by YouTube Analytics API
        metrics_candidates = [
            "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,dislikes,shares,comments,annotationClickThroughRate",
            "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,dislikes,shares,comments",
            "views,estimatedMinutesWatched,averageViewDuration,likes,comments",
        ]

        for metrics_str in metrics_candidates:
            try:
                self._rate_limit()
                request = service.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=metrics_str,
                    dimensions="video",
                    filters=f"video=={youtube_video_id}",
                )
                response = request.execute()
                rows = response.get("rows", [])
                headers = [h["name"] for h in response.get("columnHeaders", [])]

                if rows and len(rows) > 0:
                    row_dict = dict(zip(headers, rows[0]))
                    return row_dict
                else:
                    # Video exists but no rows recorded in this time range (e.g. brand new)
                    return {"views": 0, "estimatedMinutesWatched": 0, "averageViewDuration": 0}
            except HttpError as exc:
                if exc.resp.status in (400, 403):
                    # Could be invalid metric combination or insufficient scope, retry next metric set
                    logger.debug("Analytics API query failed with %s for metrics %s", exc.resp.status, metrics_str)
                    continue
                else:
                    logger.warning("YouTube Analytics API error: %s", exc)
                    break
            except Exception as exc:
                logger.warning("Unexpected error querying YouTube Analytics API: %s", exc)
                break

        return None

    def _query_data_api_fallback(self, youtube_video_id: str) -> Optional[Dict[str, Any]]:
        """Fallback to YouTube Data API v3 videos.list to retrieve basic statistics."""
        client = self._get_data_client()
        if not client:
            return None

        if not self.quota_manager.check_available("youtube_data", 1):
            logger.warning("YouTube Data API quota exceeded. Cannot execute fallback.")
            return None

        try:
            self._rate_limit()
            response = client.videos().list(part="statistics,snippet,contentDetails", id=youtube_video_id).execute()
            self.quota_manager.record_usage("youtube_data", 1)
            items = response.get("items", [])
            if not items:
                logger.warning("Video %s not found in YouTube Data API.", youtube_video_id)
                return None

            item = items[0]
            stats = item.get("statistics", {})

            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "dislikes": int(stats.get("dislikeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "shares": 0,
                "subscribersGained": 0,
                "subscribersLost": 0,
                "estimatedMinutesWatched": 0.0,
                "averageViewDuration": 0.0,
                "impressions": 0,
                "impressionClickThroughRate": 0.0,
                "annotationClickThroughRate": 0.0,
                "raw_statistics": stats,
            }
        except Exception as exc:
            logger.error("Error executing YouTube Data API fallback for %s: %s", youtube_video_id, exc)
            return None

    def _persist_snapshot(self, snapshot: AnalyticsSnapshot) -> AnalyticsSnapshot:
        """Save snapshot to SQLite database and populate assigned primary key."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    video_id, youtube_video_id, window, snapshot_date,
                    views, estimated_minutes_watched, average_view_duration,
                    subscribers_gained, subscribers_lost, likes, dislikes,
                    shares, comments, impressions, impression_ctr,
                    annotation_ctr, raw_data, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.video_id,
                    snapshot.youtube_video_id,
                    snapshot.window,
                    snapshot.snapshot_date,
                    snapshot.views,
                    snapshot.estimated_minutes_watched,
                    snapshot.average_view_duration,
                    snapshot.subscribers_gained,
                    snapshot.subscribers_lost,
                    snapshot.likes,
                    snapshot.dislikes,
                    snapshot.shares,
                    snapshot.comments,
                    snapshot.impressions,
                    snapshot.impression_ctr,
                    snapshot.annotation_ctr,
                    json.dumps(snapshot.raw_data),
                    snapshot.source,
                ),
            )
            snapshot.id = cur.lastrowid
            conn.commit()
        return snapshot

    def collect_video_analytics(
        self,
        video_id: str,
        youtube_video_id: str,
        publish_date: Optional[datetime] = None,
    ) -> AnalyticsSnapshot:
        """Collect analytics data for a specific video across 48h, 7d, or 28d windows.

        Tries the YouTube Analytics API first. If unavailable, falls back gracefully
        to YouTube Data API v3 statistics. Persists snapshots in `analytics_snapshots`.

        Args:
            video_id: Internal video identifier in autopilot system.
            youtube_video_id: YouTube's alphanumeric ID.
            publish_date: Publishing timestamp. If omitted, resolved via Data API or now.

        Returns:
            AnalyticsSnapshot containing the collected metrics.
        """
        start_time = time.time()
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")

        if publish_date is None:
            publish_date = self._fetch_video_publish_date(youtube_video_id) or (now - timedelta(days=1))

        # Ensure timezone awareness
        if publish_date.tzinfo is None:
            publish_date = publish_date.replace(tzinfo=timezone.utc)

        age = now - publish_date
        windows_to_evaluate: List[Tuple[str, str, str]] = []

        # 48h window
        start_48h = publish_date.strftime("%Y-%m-%d")
        end_48h = min(now, publish_date + timedelta(hours=48)).strftime("%Y-%m-%d")
        windows_to_evaluate.append(("48h", start_48h, end_48h))

        # 7d window (if age >= 2 days)
        if age >= timedelta(days=2):
            end_7d = min(now, publish_date + timedelta(days=7)).strftime("%Y-%m-%d")
            windows_to_evaluate.append(("7d", start_48h, end_7d))

        # 28d window (if age >= 7 days)
        if age >= timedelta(days=7):
            end_28d = min(now, publish_date + timedelta(days=28)).strftime("%Y-%m-%d")
            windows_to_evaluate.append(("28d", start_48h, end_28d))

        # Current lifetime window
        windows_to_evaluate.append(("current", start_48h, today_str))

        latest_snapshot: Optional[AnalyticsSnapshot] = None

        for window_name, w_start, w_end in windows_to_evaluate:
            # Ensure valid date order for YouTube Analytics API
            if w_start > w_end:
                w_start = w_end

            analytics_data = self._query_analytics_api_for_video(youtube_video_id, w_start, w_end)
            source = "youtube_analytics"
            raw_metrics = analytics_data

            if analytics_data is None:
                # Fallback to Data API
                source = "youtube_data_api_fallback"
                raw_metrics = self._query_data_api_fallback(youtube_video_id) or {}

            # Construct snapshot
            views = int(raw_metrics.get("views", 0))
            minutes_watched = float(raw_metrics.get("estimatedMinutesWatched", 0.0))
            avg_duration = float(raw_metrics.get("averageViewDuration", 0.0))
            subs_gained = int(raw_metrics.get("subscribersGained", 0))
            subs_lost = int(raw_metrics.get("subscribersLost", 0))
            likes = int(raw_metrics.get("likes", 0))
            dislikes = int(raw_metrics.get("dislikes", 0))
            shares = int(raw_metrics.get("shares", 0))
            comments = int(raw_metrics.get("comments", 0))
            impressions = int(raw_metrics.get("impressions", 0))
            imp_ctr = float(raw_metrics.get("impressionClickThroughRate", 0.0))
            ann_ctr = float(raw_metrics.get("annotationClickThroughRate", 0.0))

            snapshot = AnalyticsSnapshot(
                video_id=video_id,
                youtube_video_id=youtube_video_id,
                window=window_name,
                snapshot_date=today_str,
                views=views,
                estimated_minutes_watched=minutes_watched,
                average_view_duration=avg_duration,
                subscribers_gained=subs_gained,
                subscribers_lost=subs_lost,
                likes=likes,
                dislikes=dislikes,
                shares=shares,
                comments=comments,
                impressions=impressions,
                impression_ctr=imp_ctr,
                annotation_ctr=ann_ctr,
                raw_data=raw_metrics,
                source=source,
            )

            saved = self._persist_snapshot(snapshot)
            if latest_snapshot is None or window_name in ("current", "28d", "7d", "48h"):
                latest_snapshot = saved

        duration_ms = (time.time() - start_time) * 1000.0
        status = "success" if latest_snapshot and latest_snapshot.source == "youtube_analytics" else "warning"
        self._log_attempt(
            target_type="video",
            target_id=youtube_video_id,
            window=latest_snapshot.window if latest_snapshot else "unknown",
            source=latest_snapshot.source if latest_snapshot else "none",
            status=status,
            error_message=None if status == "success" else "Used Data API fallback or zero metrics",
            duration_ms=duration_ms,
        )

        if latest_snapshot is None:
            # Create a zero fallback snapshot if all APIs completely failed
            fallback_snapshot = AnalyticsSnapshot(
                video_id=video_id,
                youtube_video_id=youtube_video_id,
                window="current",
                snapshot_date=today_str,
                source="error_fallback",
            )
            return self._persist_snapshot(fallback_snapshot)

        return latest_snapshot

    def collect_channel_analytics(self, days: int = 28) -> Dict[str, Any]:
        """Collect channel-level metrics aggregated over the requested period.

        Args:
            days: Lookback window in days (default: 28).

        Returns:
            Dictionary containing channel totals, averages, daily breakdowns, and source.
        """
        start_time = time.time()
        now = datetime.now(timezone.utc)
        end_date = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")

        service = self._get_analytics_client()
        if service:
            try:
                self._rate_limit()
                # Aggregate report
                agg_request = service.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,dislikes,shares,comments",
                )
                agg_response = agg_request.execute()
                headers = [h["name"] for h in agg_response.get("columnHeaders", [])]
                rows = agg_response.get("rows", [])
                agg_data = dict(zip(headers, rows[0])) if rows else {}

                # Day-by-day time series
                self._rate_limit()
                time_request = service.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost",
                    dimensions="day",
                    sort="day",
                )
                time_response = time_request.execute()
                time_headers = [h["name"] for h in time_response.get("columnHeaders", [])]
                time_rows = [dict(zip(time_headers, r)) for r in time_response.get("rows", [])]

                duration_ms = (time.time() - start_time) * 1000.0
                self._log_attempt(
                    target_type="channel",
                    target_id="MINE",
                    window=f"{days}d",
                    source="youtube_analytics",
                    status="success",
                    error_message=None,
                    duration_ms=duration_ms,
                )

                return {
                    "source": "youtube_analytics",
                    "period_days": days,
                    "start_date": start_date,
                    "end_date": end_date,
                    "totals": {
                        "views": int(agg_data.get("views", 0)),
                        "estimatedMinutesWatched": float(agg_data.get("estimatedMinutesWatched", 0.0)),
                        "averageViewDuration": float(agg_data.get("averageViewDuration", 0.0)),
                        "subscribersGained": int(agg_data.get("subscribersGained", 0)),
                        "subscribersLost": int(agg_data.get("subscribersLost", 0)),
                        "netSubscribers": int(agg_data.get("subscribersGained", 0)) - int(agg_data.get("subscribersLost", 0)),
                        "likes": int(agg_data.get("likes", 0)),
                        "dislikes": int(agg_data.get("dislikes", 0)),
                        "shares": int(agg_data.get("shares", 0)),
                        "comments": int(agg_data.get("comments", 0)),
                    },
                    "daily_timeline": time_rows,
                }
            except Exception as exc:
                logger.warning("YouTube Analytics channel query failed, attempting Data API fallback: %s", exc)

        # Fallback to YouTube Data API channels().list
        data_client = self._get_data_client()
        totals = {
            "views": 0,
            "subscribers": 0,
            "videoCount": 0,
            "estimatedMinutesWatched": 0.0,
            "averageViewDuration": 0.0,
        }
        if data_client and self.quota_manager.check_available("youtube_data", 1):
            try:
                self._rate_limit()
                ch_resp = data_client.channels().list(part="statistics", mine=True).execute()
                self.quota_manager.record_usage("youtube_data", 1)
                items = ch_resp.get("items", [])
                if items:
                    st = items[0].get("statistics", {})
                    totals["views"] = int(st.get("viewCount", 0))
                    totals["subscribers"] = int(st.get("subscriberCount", 0))
                    totals["videoCount"] = int(st.get("videoCount", 0))
            except Exception as exc:
                logger.error("Failed fetching channel statistics fallback: %s", exc)

        duration_ms = (time.time() - start_time) * 1000.0
        self._log_attempt(
            target_type="channel",
            target_id="MINE",
            window=f"{days}d",
            source="youtube_data_api_fallback",
            status="warning",
            error_message="Fallback to Data API",
            duration_ms=duration_ms,
        )

        return {
            "source": "youtube_data_api_fallback",
            "period_days": days,
            "start_date": start_date,
            "end_date": end_date,
            "totals": totals,
            "daily_timeline": [],
        }

    def get_performance_summary(self, video_id: str) -> Dict[str, Any]:
        """Generate a multi-window performance summary for a specific video.

        Computes growth progression across 48h, 7d, and 28d windows, calculates
        engagement rates, and produces bilingual performance ratings.

        Args:
            video_id: Internal video identifier.

        Returns:
            Dictionary with snapshots breakdown, growth ratios, and Arabic/English ratings.
        """
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM analytics_snapshots
                WHERE video_id = ?
                ORDER BY created_at ASC;
                """,
                (video_id,),
            )
            rows = cur.fetchall()

        if not rows:
            return {
                "video_id": video_id,
                "status": "not_found",
                "message_en": "No analytics snapshots found for this video.",
                "message_ar": "لا توجد لقطات تحليلية مسجلة لهذا الفيديو بعد.",
            }

        snapshots_by_window: Dict[str, AnalyticsSnapshot] = {}
        for r in rows:
            snap = AnalyticsSnapshot.from_row(r)
            snapshots_by_window[snap.window] = snap

        # Latest available snapshot
        latest = AnalyticsSnapshot.from_row(rows[-1])

        # Engagement and retention metrics
        views = max(1, latest.views)
        engagement_count = latest.likes + latest.comments + latest.shares
        engagement_rate = round((engagement_count / views) * 100.0, 2)
        like_ratio = round((latest.likes / views) * 100.0, 2)

        # Growth comparisons
        w_48h = snapshots_by_window.get("48h")
        w_7d = snapshots_by_window.get("7d")
        w_28d = snapshots_by_window.get("28d")

        growth_48h_to_7d = None
        if w_48h and w_7d and w_48h.views > 0:
            growth_48h_to_7d = round(((w_7d.views - w_48h.views) / w_48h.views) * 100.0, 2)

        growth_7d_to_28d = None
        if w_7d and w_28d and w_7d.views > 0:
            growth_7d_to_28d = round(((w_28d.views - w_7d.views) / w_7d.views) * 100.0, 2)

        # Performance Tier Determination
        if engagement_rate > 8.0 and latest.average_view_duration > 40.0:
            rating_en = "Exceptional"
            rating_ar = "أداء استثنائي"
        elif engagement_rate > 4.0:
            rating_en = "Strong"
            rating_ar = "أداء قوي ومستقر"
        elif engagement_rate > 1.5:
            rating_en = "Average"
            rating_ar = "أداء متوسط"
        else:
            rating_en = "Underperforming"
            rating_ar = "يحتاج إلى تحسين في التفاعل"

        return {
            "video_id": video_id,
            "youtube_video_id": latest.youtube_video_id,
            "status": "success",
            "rating_en": rating_en,
            "rating_ar": rating_ar,
            "latest_metrics": {
                "views": latest.views,
                "watch_time_minutes": latest.estimated_minutes_watched,
                "average_view_duration_seconds": latest.average_view_duration,
                "likes": latest.likes,
                "comments": latest.comments,
                "shares": latest.shares,
                "subscribers_gained": latest.subscribers_gained,
                "impression_ctr": latest.impression_ctr,
                "engagement_rate_pct": engagement_rate,
                "like_ratio_pct": like_ratio,
                "source": latest.source,
                "snapshot_date": latest.snapshot_date,
            },
            "windows": {
                "48h": w_48h.to_dict() if w_48h else None,
                "7d": w_7d.to_dict() if w_7d else None,
                "28d": w_28d.to_dict() if w_28d else None,
            },
            "growth": {
                "growth_48h_to_7d_pct": growth_48h_to_7d,
                "growth_7d_to_28d_pct": growth_7d_to_28d,
            },
            "summary_ar": f"إجمالي المشاهدات: {latest.views:,} | نسبة التفاعل: {engagement_rate}% | التقييم: {rating_ar}",
        }
