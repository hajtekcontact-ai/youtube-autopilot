"""Cloud Autonomous Production Scheduler.

Manages durable, idempotent daily production schedules across 5 defined slots
in America/New_York timezone with full Daylight Saving Time (DST) support,
UTC cloud execution conversion, 2-hour pre-generation scheduling, concurrency locking,
and automated missed-job recovery.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.getenv("DATABASE_URL", "data/autopilot.db").replace("sqlite:///", "")
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# 5 Production slots configuration: (hour, minute, content_type)
SLOT_CONFIGURATIONS: List[Tuple[int, int, str]] = [
    (9, 0, "short"),   # 09:00 AM Short
    (12, 0, "short"),  # 12:00 PM Short
    (15, 0, "short"),  # 03:00 PM Short
    (18, 0, "long"),   # 06:00 PM Long video
    (21, 0, "short"),  # 09:00 PM Short
]

SCHEDULE_AHEAD_HOURS = 2.0  # Production begins 2 hours before scheduled publishing
DEFAULT_LOCK_TIMEOUT_SECONDS = 1800  # 30-minute concurrency lease


@dataclass
class ScheduleSlot:
    """Represents a scheduled production and publication window.

    Attributes:
        slot_id: Deterministic unique identifier (e.g., 'slot_2026-10-15_0900_short').
        date_str: Target date in local timezone (YYYY-MM-DD).
        publish_time_local: Planned publication datetime in local timezone (America/New_York).
        publish_time_utc: Planned publication datetime converted to UTC.
        production_time_utc: Generation start datetime (2 hours before publish time).
        content_type: Content format ('short' or 'long').
        status: Current state ('pending', 'producing', 'completed', 'missed', 'failed', 'recovered').
        video_id: Associated internal video identifier upon creation/completion.
        lock_owner: Process/worker token holding the execution lease.
        locked_at: Timestamp when the execution lock was granted.
        completed_at: Timestamp when production/publishing finished.
        retry_count: Number of recovery/retry attempts executed.
        error_message: Diagnostic error text if production failed.
        created_at: Creation timestamp.
        updated_at: Last state transition timestamp.
    """

    slot_id: str
    date_str: str
    publish_time_local: datetime
    publish_time_utc: datetime
    production_time_utc: datetime
    content_type: str
    status: str = "pending"
    video_id: Optional[str] = None
    lock_owner: Optional[str] = None
    locked_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert schedule slot to dictionary format."""
        d = asdict(self)
        d["publish_time_local"] = self.publish_time_local.isoformat()
        d["publish_time_utc"] = self.publish_time_utc.isoformat()
        d["production_time_utc"] = self.production_time_utc.isoformat()
        d["locked_at"] = self.locked_at.isoformat() if self.locked_at else None
        d["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ScheduleSlot:
        """Construct ScheduleSlot instance from database row."""
        pub_local = datetime.fromisoformat(row["publish_time_local"])
        pub_utc = datetime.fromisoformat(row["publish_time_utc"])
        prod_utc = datetime.fromisoformat(row["production_time_utc"])

        locked_at = datetime.fromisoformat(row["locked_at"]) if row["locked_at"] else None
        completed_at = datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
        created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
        updated_at = datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None

        return cls(
            slot_id=row["slot_id"],
            date_str=row["date_str"],
            publish_time_local=pub_local,
            publish_time_utc=pub_utc,
            production_time_utc=prod_utc,
            content_type=row["content_type"],
            status=row["status"],
            video_id=row["video_id"],
            lock_owner=row["lock_owner"],
            locked_at=locked_at,
            completed_at=completed_at,
            retry_count=row["retry_count"],
            error_message=row["error_message"],
            created_at=created_at,
            updated_at=updated_at,
        )

    def is_lock_expired(self, timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS) -> bool:
        """Check whether the active lock has exceeded its expiration threshold."""
        if not self.locked_at:
            return False
        now_utc = datetime.now(timezone.utc)
        locked_time = self.locked_at if self.locked_at.tzinfo else self.locked_at.replace(tzinfo=timezone.utc)
        return (now_utc - locked_time).total_seconds() > timeout_seconds


class CloudScheduler:
    """Production scheduler managing daily video slots and cloud execution life-cycle.

    Features:
    - 5 daily slots in America/New_York timezone (09:00, 12:00, 15:00, 18:00, 21:00).
    - Robust DST handling via pytz.
    - 2-hour schedule-ahead window for video generation prior to publish time.
    - Concurrency control with distributed lease locks.
    - Idempotent execution ensuring duplicate invocations do not produce duplicate videos.
    - Automatic detection and recovery of missed production slots.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        timezone_name: str = DEFAULT_TIMEZONE,
        schedule_ahead_hours: float = SCHEDULE_AHEAD_HOURS,
        lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize CloudScheduler.

        Args:
            db_path: Path to SQLite database.
            timezone_name: IANA timezone identifier (default: 'America/New_York').
            schedule_ahead_hours: Hours ahead of publish time when production should begin.
            lock_timeout_seconds: Duration before an unreleased lock is considered stale.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.timezone_name = timezone_name
        self.tz = pytz.timezone(self.timezone_name)
        self.schedule_ahead_hours = schedule_ahead_hours
        self.lock_timeout_seconds = lock_timeout_seconds

        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtain thread-safe connection to SQLite database."""
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Create the schedule_slots table and associated indexes."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_slots (
                    slot_id TEXT PRIMARY KEY,
                    date_str TEXT NOT NULL,
                    publish_time_local TEXT NOT NULL,
                    publish_time_utc TEXT NOT NULL,
                    production_time_utc TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    video_id TEXT,
                    lock_owner TEXT,
                    locked_at TEXT,
                    completed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_date ON schedule_slots(date_str);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_status ON schedule_slots(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_prod_time ON schedule_slots(production_time_utc);")
            conn.commit()

    def generate_slots_for_date(self, target_date: date) -> List[ScheduleSlot]:
        """Generate and persist the 5 configured slots for a given local date.

        Performs DST-aware conversion to UTC and computes production lead time.
        Idempotent: will not overwrite existing completed or in-progress slots.

        Args:
            target_date: Target calendar date in local timezone.

        Returns:
            List of ScheduleSlot objects for that date.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        slots: List[ScheduleSlot] = []

        with self._get_connection() as conn:
            for hour, minute, content_type in SLOT_CONFIGURATIONS:
                slot_id = f"slot_{date_str}_{hour:02d}{minute:02d}_{content_type}"

                # Local time with pytz DST handling
                naive_local = datetime(target_date.year, target_date.month, target_date.day, hour, minute, 0)
                localized_dt = self.tz.localize(naive_local)
                utc_pub_dt = localized_dt.astimezone(pytz.UTC)
                utc_prod_dt = utc_pub_dt - timedelta(hours=self.schedule_ahead_hours)

                # Insert if not already existing
                conn.execute(
                    """
                    INSERT OR IGNORE INTO schedule_slots (
                        slot_id, date_str, publish_time_local, publish_time_utc,
                        production_time_utc, content_type, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending');
                    """,
                    (
                        slot_id,
                        date_str,
                        localized_dt.isoformat(),
                        utc_pub_dt.isoformat(),
                        utc_prod_dt.isoformat(),
                        content_type,
                    ),
                )

            conn.commit()

            # Retrieve persisted slots
            cur = conn.execute(
                "SELECT * FROM schedule_slots WHERE date_str = ? ORDER BY publish_time_utc ASC;",
                (date_str,),
            )
            for row in cur.fetchall():
                slots.append(ScheduleSlot.from_row(row))

        return slots

    def get_today_schedule(self, target_date: Optional[date] = None) -> List[ScheduleSlot]:
        """Get the full 5-slot production schedule for today (or specified date).

        Calculates today relative to the configured local timezone (America/New_York).

        Args:
            target_date: Optional specific calendar date. Defaults to current local date.

        Returns:
            List of 5 ScheduleSlot items.
        """
        if target_date is None:
            now_local = datetime.now(self.tz)
            target_date = now_local.date()

        return self.generate_slots_for_date(target_date)

    def acquire_slot_lock(
        self, slot_id: str, lock_owner: Optional[str] = None, timeout_seconds: Optional[int] = None
    ) -> bool:
        """Attempt to acquire an exclusive lock on a slot for video production.

        Enforces concurrency control: prevents multiple workers or cloud jobs
        from producing content for the same slot simultaneously.

        Args:
            slot_id: Target slot identifier.
            lock_owner: Unique identifier for the requesting worker or task.
            timeout_seconds: Expiration timeout in seconds.

        Returns:
            True if lock was successfully acquired, False otherwise.
        """
        owner = lock_owner or f"worker_{uuid.uuid4().hex[:8]}"
        timeout = timeout_seconds or self.lock_timeout_seconds
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        stale_threshold_iso = (now_utc - timedelta(seconds=timeout)).isoformat()

        with self._get_connection() as conn:
            # Acquire lock if:
            # 1. slot is pending or recovered
            # 2. OR slot is currently 'producing' but its lock has expired (stale)
            cur = conn.execute(
                """
                UPDATE schedule_slots
                SET status = 'producing',
                    lock_owner = ?,
                    locked_at = ?,
                    updated_at = ?
                WHERE slot_id = ?
                  AND status != 'completed'
                  AND (
                      lock_owner IS NULL
                      OR status IN ('pending', 'recovered')
                      OR locked_at < ?
                  );
                """,
                (owner, now_iso, now_iso, slot_id, stale_threshold_iso),
            )
            conn.commit()
            acquired = cur.rowcount > 0

        if acquired:
            logger.info("Acquired lock on slot %s for owner %s", slot_id, owner)
        else:
            logger.debug("Failed to acquire lock on slot %s (already held or completed)", slot_id)

        return acquired

    def release_slot_lock(self, slot_id: str, lock_owner: str, target_status: str = "pending") -> bool:
        """Release execution lock held by a specific owner."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE schedule_slots
                SET status = ?,
                    lock_owner = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE slot_id = ? AND lock_owner = ?;
                """,
                (target_status, now_iso, slot_id, lock_owner),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_slot_complete(self, slot_id: str, video_id: str) -> None:
        """Mark a slot as completed with its associated video identifier.

        Idempotent: safe to invoke multiple times without data corruption.

        Args:
            slot_id: Target slot identifier.
            video_id: Internal video identifier created for this slot.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE schedule_slots
                SET status = 'completed',
                    video_id = ?,
                    completed_at = ?,
                    lock_owner = NULL,
                    locked_at = NULL,
                    error_message = NULL,
                    updated_at = ?
                WHERE slot_id = ?;
                """,
                (video_id, now_iso, now_iso, slot_id),
            )
            conn.commit()

        logger.info("Marked slot %s complete with video %s", slot_id, video_id)

    def mark_slot_failed(self, slot_id: str, error_message: str) -> None:
        """Mark a slot as failed and increment retry count."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE schedule_slots
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    error_message = ?,
                    lock_owner = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE slot_id = ?;
                """,
                (error_message, now_iso, slot_id),
            )
            conn.commit()

        logger.warning("Marked slot %s as failed: %s", slot_id, error_message)

    def get_slot(self, slot_id: str) -> Optional[ScheduleSlot]:
        """Retrieve a specific slot by its identifier."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM schedule_slots WHERE slot_id = ?;", (slot_id,))
            row = cur.fetchone()
            return ScheduleSlot.from_row(row) if row else None

    def get_next_slot(self, now_utc: Optional[datetime] = None) -> Optional[ScheduleSlot]:
        """Find the next slot ready for execution.

        Evaluates the 2-hour schedule-ahead window: a slot is eligible for production
        as soon as current UTC time reaches its production_time_utc. Prioritizes
        recovered/missed slots, then next pending slot in chronological order.

        Args:
            now_utc: Current UTC datetime. Defaults to system datetime.now(timezone.utc).

        Returns:
            The next due ScheduleSlot, or None if no slots are due.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        # Make sure today's and tomorrow's slots are generated
        local_now = now_utc.astimezone(self.tz)
        self.generate_slots_for_date(local_now.date())
        self.generate_slots_for_date(local_now.date() + timedelta(days=1))

        # Check for any stale locks and reset them to pending
        self.reset_stale_locks()

        now_iso = now_utc.isoformat()

        with self._get_connection() as conn:
            # 1. First priority: recovered or missed slots that need immediate production
            cur = conn.execute(
                """
                SELECT * FROM schedule_slots
                WHERE status IN ('recovered')
                  AND (lock_owner IS NULL)
                ORDER BY publish_time_utc ASC LIMIT 1;
                """
            )
            row = cur.fetchone()
            if row:
                return ScheduleSlot.from_row(row)

            # 2. Second priority: pending slots whose production window is open (now >= production_time_utc)
            # and whose publish time is in the future
            cur = conn.execute(
                """
                SELECT * FROM schedule_slots
                WHERE status = 'pending'
                  AND production_time_utc <= ?
                  AND publish_time_utc >= ?
                  AND (lock_owner IS NULL)
                ORDER BY production_time_utc ASC LIMIT 1;
                """,
                (now_iso, now_iso),
            )
            row = cur.fetchone()
            if row:
                return ScheduleSlot.from_row(row)

            # 3. Third priority: closest upcoming slot scheduled ahead
            cur = conn.execute(
                """
                SELECT * FROM schedule_slots
                WHERE status = 'pending'
                  AND production_time_utc > ?
                  AND (lock_owner IS NULL)
                ORDER BY production_time_utc ASC LIMIT 1;
                """,
                (now_iso,),
            )
            row = cur.fetchone()
            if row:
                return ScheduleSlot.from_row(row)

        return None

    def get_missed_slots(self, now_utc: Optional[datetime] = None) -> List[ScheduleSlot]:
        """Identify slots that passed their scheduled publish or production time without completion.

        Args:
            now_utc: Reference UTC datetime. Defaults to now.

        Returns:
            List of missed ScheduleSlot instances.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        # Ensure today's slots are generated
        local_now = now_utc.astimezone(self.tz)
        self.generate_slots_for_date(local_now.date())
        now_iso = now_utc.isoformat()

        missed: List[ScheduleSlot] = []
        with self._get_connection() as conn:
            # A slot is missed if:
            # 1. status is 'pending' or 'failed' AND publish_time_utc < now
            # 2. OR status is 'producing' but lock has been stale for over 1 hour past publish time
            cur = conn.execute(
                """
                SELECT * FROM schedule_slots
                WHERE status IN ('pending', 'failed', 'missed')
                  AND publish_time_utc < ?
                ORDER BY publish_time_utc ASC;
                """,
                (now_iso,),
            )
            for row in cur.fetchall():
                missed.append(ScheduleSlot.from_row(row))

        return missed

    def recover_missed_slots(self, now_utc: Optional[datetime] = None) -> List[ScheduleSlot]:
        """Detect and queue missed production slots for recovery.

        If a slot was skipped (e.g. system downtime, runner failure), this method
        transitions its status to 'recovered', resets locks, and prepares it
        to be executed immediately on the next runner cycle.

        Args:
            now_utc: Reference UTC datetime.

        Returns:
            List of recovered ScheduleSlot objects ready for production.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        missed_slots = self.get_missed_slots(now_utc)
        recovered: List[ScheduleSlot] = []
        now_iso = now_utc.isoformat()

        with self._get_connection() as conn:
            for slot in missed_slots:
                # Mark as recovered, ready for immediate execution
                conn.execute(
                    """
                    UPDATE schedule_slots
                    SET status = 'recovered',
                        lock_owner = NULL,
                        locked_at = NULL,
                        retry_count = retry_count + 1,
                        updated_at = ?
                    WHERE slot_id = ?;
                    """,
                    (now_iso, slot.slot_id),
                )
                slot.status = "recovered"
                slot.retry_count += 1
                recovered.append(slot)
            conn.commit()

        if recovered:
            logger.warning("Recovered %d missed production slots for execution.", len(recovered))

        return recovered

    def reset_stale_locks(self, timeout_seconds: Optional[int] = None) -> int:
        """Release locks held by crashed or timed-out workers.

        Args:
            timeout_seconds: Lock duration threshold.

        Returns:
            Count of stale locks successfully cleared.
        """
        timeout = timeout_seconds or self.lock_timeout_seconds
        now_utc = datetime.now(timezone.utc)
        stale_threshold_iso = (now_utc - timedelta(seconds=timeout)).isoformat()
        now_iso = now_utc.isoformat()

        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE schedule_slots
                SET status = 'pending',
                    lock_owner = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE status = 'producing'
                  AND locked_at < ?;
                """,
                (now_iso, stale_threshold_iso),
            )
            conn.commit()
            cleared_count = cur.rowcount

        if cleared_count > 0:
            logger.info("Reset %d stale slot production locks.", cleared_count)

        return cleared_count
