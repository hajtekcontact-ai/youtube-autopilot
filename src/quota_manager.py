"""API Quota and Cost Management Module for YouTube AI Autopilot.

Manages external API usage limits, daily quotas, rate limiting windows, cost tracking,
and integration with safety kill switches for Google APIs (YouTube Data API v3, Gemini AI)
and Edge TTS.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func
from src.config import current_config
from src.database import APIUsage, get_db

logger = logging.getLogger(__name__)


@dataclass
class UsageSummary:
    """Consolidated summary of API quota consumption for a single day."""

    api_name: str
    date: str
    total_units: int
    quota_limit: int
    remaining_units: int
    usage_percentage: float
    estimated_cost_usd: float
    is_exhausted: bool
    is_near_limit: bool
    endpoint_breakdown: Dict[str, int] = field(default_factory=dict)
    arabic_status: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary representation.

        Returns:
            Dict[str, Any]: Serialized usage summary.
        """
        return asdict(self)


class QuotaManager:
    """Thread-safe API quota and cost orchestrator.

    Tracks consumption across YouTube Data API v3, Google Gemini, and TTS,
    enforcing daily limits, RPM constraints, and cost kill switches.
    """

    # YouTube Data API operation unit costs (Standard YouTube API Quota)
    YOUTUBE_UNIT_COSTS: Dict[str, int] = {
        "uploads.insert": 1600,
        "videos.list": 1,
        "videos.update": 50,
        "thumbnails.set": 50,
        "channels.list": 1,
        "playlistItems.insert": 50,
        "playlistItems.list": 1,
        "search.list": 100,
        "commentThreads.list": 1,
        "commentThreads.insert": 50,
    }

    # Estimated costs per unit / token for financial simulation and tracking
    # Gemini 1.5 Flash: ~$0.075 / 1M input tokens, ~$0.30 / 1M output tokens (~$0.0001 per typical request)
    # YouTube Data API: Beyond free tier ~$0.0001 per unit (or standard enterprise rates)
    ESTIMATED_UNIT_COST_USD: Dict[str, float] = {
        "youtube": 0.00005,      # $0.50 per 10,000 units equivalent
        "gemini": 0.0002,        # Approximate cost per typical multi-turn generation
        "tts": 0.000015,         # Per 1,000 characters
    }

    def __init__(self) -> None:
        """Initialize quota manager with thread lock and in-memory rate trackers."""
        self._lock = threading.Lock()
        # Sliding timestamps for requests per minute (RPM)
        self._gemini_request_timestamps: deque[float] = deque()
        self._alert_threshold_pct: float = getattr(current_config, "COST_ALERT_THRESHOLD_PCT", 0.80)

    @staticmethod
    def get_current_utc_date() -> str:
        """Return the current UTC date string in YYYY-MM-DD format.

        Returns:
            str: Date string.
        """
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_api_limit(self, api: str) -> int:
        """Retrieve configured daily limit for a given API.

        Args:
            api: API identifier ('youtube', 'gemini', 'tts').

        Returns:
            int: Daily unit limit.
        """
        api_lower = api.lower()
        if api_lower == "youtube":
            return current_config.YOUTUBE_DAILY_QUOTA_LIMIT
        elif api_lower == "gemini":
            return current_config.GEMINI_DAILY_REQUEST_LIMIT
        elif api_lower == "tts":
            return current_config.TTS_DAILY_CHAR_LIMIT
        return 10000

    def can_upload(self) -> bool:
        """Determine whether YouTube quota and safety policies allow a new video upload.

        Upload requires 1,600 YouTube units, inactive emergency stop, and
        cost ceiling compliance.

        Returns:
            bool: True if video upload is permitted, False otherwise.
        """
        if current_config.EMERGENCY_STOP:
            logger.warning("Upload blocked: EMERGENCY_STOP is active.")
            return False

        if self.is_cost_kill_switch_active():
            logger.warning("Upload blocked: COST_KILL_SWITCH is active.")
            return False

        remaining = self.get_remaining_quota("youtube")
        upload_cost = self.YOUTUBE_UNIT_COSTS.get("uploads.insert", 1600)

        if remaining < upload_cost:
            logger.warning(
                "Upload blocked: Insufficient YouTube quota (%d remaining, %d needed).",
                remaining,
                upload_cost,
            )
            return False

        return True

    def can_call_gemini(self) -> bool:
        """Check if Gemini API calls are permitted under RPM, daily quota, and cost rules.

        Returns:
            bool: True if Gemini API call is permissible.
        """
        if current_config.EMERGENCY_STOP:
            logger.warning("Gemini call blocked: EMERGENCY_STOP is active.")
            return False

        if self.is_cost_kill_switch_active():
            logger.warning("Gemini call blocked: COST_KILL_SWITCH is active.")
            return False

        # Check RPM sliding window
        now = time.time()
        rpm_limit = current_config.GEMINI_RPM_LIMIT
        with self._lock:
            # Purge timestamps older than 60 seconds
            while self._gemini_request_timestamps and now - self._gemini_request_timestamps[0] > 60.0:
                self._gemini_request_timestamps.popleft()

            if len(self._gemini_request_timestamps) >= rpm_limit:
                logger.warning(
                    "Gemini call throttled: RPM limit reached (%d requests in past 60s).",
                    len(self._gemini_request_timestamps),
                )
                return False

        # Check daily requests limit
        remaining = self.get_remaining_quota("gemini")
        if remaining <= 0:
            logger.warning("Gemini call blocked: Daily request limit exhausted.")
            return False

        return True

    def is_cost_kill_switch_active(self) -> bool:
        """Evaluate if the cost kill switch is engaged by environment or budget overrun.

        Returns:
            bool: True if production should be halted due to cost ceilings.
        """
        if current_config.COST_KILL_SWITCH:
            return True

        max_daily_cost = current_config.MAX_DAILY_API_COST_USD
        if max_daily_cost > 0.0:
            estimated = self.estimate_daily_cost()
            if estimated >= max_daily_cost:
                logger.critical(
                    "Cost kill switch triggered: daily estimated cost $%.4f exceeds cap $%.2f",
                    estimated,
                    max_daily_cost,
                )
                return True

        return False

    def record_usage(
        self,
        api: str,
        endpoint: str,
        units: Optional[int] = None,
        cost_usd: Optional[float] = None,
    ) -> None:
        """Record an API operation consumption in the database and rate limiter.

        Args:
            api: API name ('youtube', 'gemini', 'tts').
            endpoint: Operation or endpoint identifier (e.g. 'uploads.insert').
            units: Explicit unit consumption. If None, looked up from defaults.
            cost_usd: Explicit dollar cost if known.
        """
        api_lower = api.lower()
        date_str = self.get_current_utc_date()

        # Determine units used
        if units is None:
            if api_lower == "youtube":
                units = self.YOUTUBE_UNIT_COSTS.get(endpoint, 1)
            else:
                units = 1

        # Register RPM for Gemini
        if api_lower == "gemini":
            with self._lock:
                self._gemini_request_timestamps.append(time.time())

        # Persist to database
        try:
            with get_db() as session:
                # Query existing daily total for this API today
                current_total = (
                    session.query(func.coalesce(func.sum(APIUsage.units_used), 0))
                    .filter(APIUsage.api_name == api_lower, APIUsage.date == date_str)
                    .scalar()
                ) or 0

                new_total = int(current_total) + units

                usage_record = APIUsage(
                    api_name=api_lower,
                    endpoint=endpoint,
                    units_used=units,
                    daily_total=new_total,
                    date=date_str,
                )
                session.add(usage_record)
                session.commit()

            # Check threshold and log warnings if needed
            limit = self._get_api_limit(api_lower)
            if limit > 0:
                pct = new_total / limit
                if pct >= self._alert_threshold_pct:
                    logger.warning(
                        "API Quota Alert: '%s' has consumed %d of %d daily units (%.1f%%).",
                        api_lower,
                        new_total,
                        limit,
                        pct * 100.0,
                    )
        except Exception as exc:
            logger.error("Failed to persist API usage record for %s:%s: %s", api_lower, endpoint, exc)

    def get_daily_usage(self, api: str, date_str: Optional[str] = None) -> UsageSummary:
        """Calculate aggregate consumption statistics for a specific API on a given date.

        Args:
            api: Target API identifier ('youtube', 'gemini', 'tts').
            date_str: Date in YYYY-MM-DD format (defaults to current UTC date).

        Returns:
            UsageSummary: Complete consumption summary.
        """
        api_lower = api.lower()
        target_date = date_str or self.get_current_utc_date()
        limit = self._get_api_limit(api_lower)

        total_units = 0
        endpoint_breakdown: Dict[str, int] = {}

        try:
            with get_db() as session:
                records = (
                    session.query(APIUsage.endpoint, func.sum(APIUsage.units_used))
                    .filter(APIUsage.api_name == api_lower, APIUsage.date == target_date)
                    .group_by(APIUsage.endpoint)
                    .all()
                )
                for ep, count in records:
                    units_count = int(count or 0)
                    endpoint_breakdown[ep] = units_count
                    total_units += units_count
        except Exception as exc:
            logger.error("Error retrieving daily usage for %s: %s", api_lower, exc)

        remaining = max(0, limit - total_units)
        pct = (total_units / limit * 100.0) if limit > 0 else 0.0
        is_exhausted = total_units >= limit
        is_near = (total_units / limit) >= self._alert_threshold_pct if limit > 0 else False

        cost_factor = self.ESTIMATED_UNIT_COST_USD.get(api_lower, 0.0001)
        estimated_cost = round(total_units * cost_factor, 4)

        if is_exhausted:
            arabic_status = f"تم استنفاد الحصة اليومية بالكامل ({total_units}/{limit} وحدة)"
        elif is_near:
            arabic_status = f"تحذير: الحصة تقترب من النفاد ({pct:.1f}% مستهلك)"
        else:
            arabic_status = f"الحصة طبيعية وفي الحدود الآمنة ({pct:.1f}% مستهلك)"

        return UsageSummary(
            api_name=api_lower,
            date=target_date,
            total_units=total_units,
            quota_limit=limit,
            remaining_units=remaining,
            usage_percentage=round(pct, 2),
            estimated_cost_usd=estimated_cost,
            is_exhausted=is_exhausted,
            is_near_limit=is_near,
            endpoint_breakdown=endpoint_breakdown,
            arabic_status=arabic_status,
        )

    def get_remaining_quota(self, api: str) -> int:
        """Get the number of available units remaining today for the given API.

        Args:
            api: Target API name.

        Returns:
            int: Remaining units.
        """
        summary = self.get_daily_usage(api)
        return summary.remaining_units

    def estimate_daily_cost(self, date_str: Optional[str] = None) -> float:
        """Estimate the total daily dollar expenditure across all tracked APIs.

        Args:
            date_str: Target date string (YYYY-MM-DD), defaults to UTC today.

        Returns:
            float: Estimated total cost in USD.
        """
        target_date = date_str or self.get_current_utc_date()
        total_cost = 0.0

        for api in ("youtube", "gemini", "tts"):
            summary = self.get_daily_usage(api, date_str=target_date)
            total_cost += summary.estimated_cost_usd

        return round(total_cost, 4)

    def get_all_summaries(self, date_str: Optional[str] = None) -> Dict[str, UsageSummary]:
        """Obtain usage summaries for all supported external services.

        Args:
            date_str: Target date.

        Returns:
            Dict[str, UsageSummary]: Map of API name to its usage summary.
        """
        target_date = date_str or self.get_current_utc_date()
        return {
            "youtube": self.get_daily_usage("youtube", target_date),
            "gemini": self.get_daily_usage("gemini", target_date),
            "tts": self.get_daily_usage("tts", target_date),
        }
