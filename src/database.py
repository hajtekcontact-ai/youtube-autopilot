"""Database module for YouTube AI Autopilot.

Defines SQLAlchemy ORM models, connection engines, session factories,
initialization logic with SQLite WAL/Foreign Key pragmas, and schema migration support.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    scoped_session,
    sessionmaker,
)

from src.config import current_config

logger = logging.getLogger(__name__)


def generate_uuid() -> str:
    """Generate a 36-character UUID string for primary keys.

    Returns:
        str: Hexadecimal UUID4 string.
    """
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""

    def to_dict(self) -> Dict[str, Any]:
        """Generic serialization to dictionary.

        Returns:
            Dict[str, Any]: Column values as dictionary.
        """
        result: Dict[str, Any] = {}
        for column in self.__table__.columns:
            val = getattr(self, column.name)
            if isinstance(val, datetime):
                result[column.name] = val.isoformat()
            else:
                result[column.name] = val
        return result


class Video(Base):
    """Represents a video in the production or publication pipeline."""

    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(100), unique=True, nullable=True, default=generate_uuid, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    script = Column(Text, nullable=False, default="")
    status = Column(String(50), nullable=False, default="draft", index=True)
    # Status options: draft, script_ready, audio_ready, rendering, ready, scheduled, publishing, published, failed
    video_type = Column(String(20), nullable=False, default="short", index=True)  # 'short' or 'long'
    file_path = Column(String(500), nullable=True)
    thumbnail_path = Column(String(500), nullable=True)
    youtube_id = Column(String(100), nullable=True, index=True)
    scheduled_time = Column(DateTime, nullable=True, index=True)
    published_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    duration_seconds = Column(Float, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    topic = Column(String(255), nullable=True, index=True)
    niche = Column(String(100), nullable=True, index=True)
    tags = Column(Text, nullable=False, default="[]")  # Serialized JSON list
    metadata_json = Column("metadata_json", Text, nullable=False, default="{}")  # Serialized JSON dict

    # Relationships
    analytics = relationship("AnalyticsSnapshot", back_populates="video", cascade="all, delete-orphan")
    jobs = relationship("ProductionJob", back_populates="video", cascade="all, delete-orphan")
    schedule_slots = relationship("ScheduleSlot", back_populates="video")

    @property
    def tags_list(self) -> List[str]:
        """Deserialize tags from JSON string.

        Returns:
            List[str]: List of tags.
        """
        try:
            return json.loads(self.tags) if self.tags else []
        except (ValueError, TypeError):
            return []

    @tags_list.setter
    def tags_list(self, values: List[str]) -> None:
        """Serialize tags to JSON string.

        Args:
            values: List of string tags.
        """
        self.tags = json.dumps(values or [])

    @property
    def extra_metadata(self) -> Dict[str, Any]:
        """Deserialize metadata dictionary from JSON string.

        Returns:
            Dict[str, Any]: Extra metadata properties.
        """
        try:
            return json.loads(self.metadata_json) if self.metadata_json else {}
        except (ValueError, TypeError):
            return {}

    @extra_metadata.setter
    def extra_metadata(self, values: Dict[str, Any]) -> None:
        """Serialize metadata dictionary to JSON string.

        Args:
            values: Dictionary of metadata.
        """
        self.metadata_json = json.dumps(values or {})

    def to_dict(self) -> Dict[str, Any]:
        """Extended dictionary serializer including parsed tags and metadata."""
        d = super().to_dict()
        d["tags"] = self.tags_list
        d["metadata"] = self.extra_metadata
        return d


class ContentIdea(Base):
    """Represents a generated or trending content idea for future videos."""

    __tablename__ = "content_ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(255), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    niche = Column(String(100), nullable=False, index=True)
    source = Column(String(100), nullable=False, default="ai_generated", index=True)
    used = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    score = Column(Float, nullable=False, default=0.0, index=True)
    hook = Column(Text, nullable=False, default="")
    metadata_json = Column("metadata_json", Text, nullable=False, default="{}")

    @property
    def extra_metadata(self) -> Dict[str, Any]:
        """Get parsed JSON metadata."""
        try:
            return json.loads(self.metadata_json) if self.metadata_json else {}
        except (ValueError, TypeError):
            return {}

    @extra_metadata.setter
    def extra_metadata(self, values: Dict[str, Any]) -> None:
        """Set metadata as JSON string."""
        self.metadata_json = json.dumps(values or {})

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["metadata"] = self.extra_metadata
        return d


class AnalyticsSnapshot(Base):
    """Historical analytics snapshot for a video over a specific reporting window."""

    __tablename__ = "analytics_snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=True, index=True)
    views = Column(Integer, nullable=False, default=0)
    impressions = Column(Integer, nullable=False, default=0)
    ctr = Column(Float, nullable=False, default=0.0)
    watch_time_minutes = Column(Float, nullable=False, default=0.0)
    avg_view_duration = Column(Float, nullable=False, default=0.0)
    subs_gained = Column(Integer, nullable=False, default=0)
    traffic_sources = Column(Text, nullable=False, default="{}")
    snapshot_date = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    snapshot_type = Column(String(20), nullable=False, default="48h", index=True)  # 48h, 7d, 28d, lifetime

    # Relationships
    video = relationship("Video", back_populates="analytics")

    @property
    def traffic_sources_dict(self) -> Dict[str, Any]:
        """Get parsed traffic sources breakdown."""
        try:
            return json.loads(self.traffic_sources) if self.traffic_sources else {}
        except (ValueError, TypeError):
            return {}

    @traffic_sources_dict.setter
    def traffic_sources_dict(self, values: Dict[str, Any]) -> None:
        """Store traffic sources as JSON string."""
        self.traffic_sources = json.dumps(values or {})

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["traffic_sources"] = self.traffic_sources_dict
        return d


class ProductionJob(Base):
    """Tracks asynchronous video generation pipeline jobs and steps."""

    __tablename__ = "production_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    # Status: pending, in_progress, completed, failed, cancelled
    stage = Column(String(50), nullable=False, default="script", index=True)
    # Stage: script, audio, visuals, render, thumbnail, upload, review
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    logs = Column(Text, nullable=False, default="[]")  # JSON list of log entries

    # Relationships
    video = relationship("Video", back_populates="jobs")

    @property
    def logs_list(self) -> List[Dict[str, Any]]:
        """Get parsed list of job execution logs."""
        try:
            return json.loads(self.logs) if self.logs else []
        except (ValueError, TypeError):
            return []

    def append_log(self, entry: str, stage: Optional[str] = None) -> None:
        """Append a timestamped log entry to the job execution record.

        Args:
            entry: Log text description.
            stage: Pipeline stage name.
        """
        current_logs = self.logs_list
        current_logs.append({
            "timestamp": datetime.utcnow().isoformat(),
            "stage": stage or self.stage,
            "message": entry,
        })
        self.logs = json.dumps(current_logs)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["logs"] = self.logs_list
        return d


class ScheduleSlot(Base):
    """Calendar publishing slot for daily automated uploads."""

    __tablename__ = "schedule_slots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    slot_time = Column(String(10), nullable=False, index=True)  # e.g., '09:00', '12:00'
    day_of_week = Column(Integer, nullable=False, index=True)  # 0=Monday, 6=Sunday
    video_type = Column(String(20), nullable=False, default="short", index=True)  # 'short' or 'long'
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="open", index=True)
    # Status: open, assigned, published, missed, paused

    # Relationships
    video = relationship("Video", back_populates="schedule_slots")


class SystemLog(Base):
    """Persistent audit and operational logging table."""

    __tablename__ = "system_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    level = Column(String(20), nullable=False, default="INFO", index=True)
    module = Column(String(100), nullable=False, default="root", index=True)
    message = Column(Text, nullable=False)
    details = Column(Text, nullable=False, default="{}")  # JSON details
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    @property
    def details_dict(self) -> Dict[str, Any]:
        """Get parsed JSON details."""
        try:
            return json.loads(self.details) if self.details else {}
        except (ValueError, TypeError):
            return {}

    @details_dict.setter
    def details_dict(self, values: Dict[str, Any]) -> None:
        """Store details dictionary as JSON string."""
        self.details = json.dumps(values or {})

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["details"] = self.details_dict
        return d


class ExperimentResult(Base):
    """A/B testing experiments (e.g., titles, thumbnails, publish schedules)."""

    __tablename__ = "experiment_results"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    experiment_type = Column(String(100), nullable=False, index=True)  # title, thumbnail, timing
    variant_a = Column(Text, nullable=False)  # Raw text or JSON
    variant_b = Column(Text, nullable=False)  # Raw text or JSON
    metric = Column(String(50), nullable=False, default="ctr")  # ctr, views, retention
    result = Column(String(50), nullable=False, default="running")  # variant_a, variant_b, inconclusive
    confidence = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class APIUsage(Base):
    """Tracks daily external API quota consumption (YouTube, Gemini, Cloud)."""

    __tablename__ = "api_usage"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    api_name = Column(String(50), nullable=False, index=True)  # youtube, gemini, tts
    endpoint = Column(String(255), nullable=False, default="")
    units_used = Column(Integer, nullable=False, default=1)
    daily_total = Column(Integer, nullable=False, default=0)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_api_name_date", "api_name", "date"),
    )


class SecureToken(Base):
    """Stores encrypted OAuth refresh tokens, credentials, and API secrets."""

    __tablename__ = "secure_tokens"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    service = Column(String(100), unique=True, nullable=False, index=True)
    # e.g., 'youtube_oauth', 'gemini_key', 'github_token'
    encrypted_token = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# Global Engine and SessionMaker instances
_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker[Session]] = None
_ScopedSession: Optional[scoped_session[Session]] = None


def setup_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Enforce WAL journal mode and foreign key constraints on SQLite connections."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=10000;")  # 10 second timeout for lock contention
    except Exception as exc:
        logger.warning("Could not set SQLite pragmas: %s", exc)
    finally:
        cursor.close()


def get_engine(database_url: Optional[str] = None, echo: bool = False) -> Engine:
    """Create or return the existing SQLAlchemy engine.

    Args:
        database_url: Database connection string.
        echo: Whether to log generated SQL statements.

    Returns:
        Engine: Configured SQLAlchemy engine instance.
    """
    global _engine
    if _engine is not None and database_url is None:
        return _engine

    url = database_url or current_config.DATABASE_URL

    # Ensure parent directory exists for file-based SQLite databases
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        db_path_str = url.replace("sqlite:///", "")
        db_path = Path(db_path_str).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connect_args: Dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        echo=echo or current_config.DB_ECHO,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    if url.startswith("sqlite"):
        event.listen(engine, "connect", setup_sqlite_pragmas)

    if database_url is None:
        _engine = engine

    return engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker[Session]:
    """Retrieve or create the SQLAlchemy session factory.

    Args:
        engine: Optional Engine instance.

    Returns:
        sessionmaker[Session]: Factory producing database sessions.
    """
    global _SessionFactory, _ScopedSession
    if _SessionFactory is None or engine is not None:
        target_engine = engine or get_engine()
        _SessionFactory = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=target_engine)
        _ScopedSession = scoped_session(_SessionFactory)
    return _SessionFactory


def get_scoped_session() -> scoped_session[Session]:
    """Get thread-local scoped session instance.

    Returns:
        scoped_session[Session]: Thread-safe scoped session.
    """
    global _ScopedSession
    if _ScopedSession is None:
        get_session_factory()
    assert _ScopedSession is not None
    return _ScopedSession


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager providing a transactional SQLAlchemy session.

    Yields:
        Session: Open database session.

    Ensures:
        Session is committed if no exceptions occur, or rolled back on failure,
        and cleanly closed.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Database transaction rolled back due to error: %s", exc)
        raise
    finally:
        session.close()


def seed_default_schedule_slots(session: Session) -> int:
    """Seed the default 5 publishing slots per day for all 7 days of the week.

    Daily Slots NY Time:
    - 09:00 (Short)
    - 12:00 (Short)
    - 15:00 (Short)
    - 18:00 (Long Video)
    - 21:00 (Short)

    Args:
        session: Active database session.

    Returns:
        int: Number of new slots created.
    """
    existing_count = session.query(ScheduleSlot).count()
    if existing_count > 0:
        return 0

    slots_spec = [
        ("09:00", "short"),
        ("12:00", "short"),
        ("15:00", "short"),
        ("18:00", "long"),
        ("21:00", "short"),
    ]

    new_slots: List[ScheduleSlot] = []
    for day_of_week in range(7):  # 0=Monday through 6=Sunday
        for slot_time, video_type in slots_spec:
            slot = ScheduleSlot(
                slot_time=slot_time,
                day_of_week=day_of_week,
                video_type=video_type,
                status="open",
            )
            new_slots.append(slot)

    session.add_all(new_slots)
    session.commit()
    logger.info("Seeded %d default weekly schedule slots.", len(new_slots))
    return len(new_slots)


def run_migrations(engine: Engine) -> None:
    """Check database schema integrity and apply safe structural migrations.

    Inspects existing tables and ensures all expected columns exist.

    Args:
        engine: Database engine to inspect and migrate.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # Create missing tables
    Base.metadata.create_all(bind=engine)

    # Lightweight column migration for existing tables if needed in SQLite
    with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if table_name in existing_tables:
                existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
                for col in table.columns:
                    if col.name not in existing_cols:
                        # SQLite ADD COLUMN supports adding nullable or default columns
                        col_type = col.type.compile(engine.dialect)
                        sql = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                        logger.info("Migrating schema: adding column %s to %s", col.name, table_name)
                        conn.execute(text(sql))


def init_db(database_url: Optional[str] = None, echo: bool = False) -> Engine:
    """Initialize the database: configure engine, create tables, migrate, and seed defaults.

    Args:
        database_url: Optional custom database connection URL.
        echo: Whether to enable SQL output.

    Returns:
        Engine: Initialized SQLAlchemy engine.
    """
    engine = get_engine(database_url=database_url, echo=echo)
    run_migrations(engine)

    # Seed default schedule slots
    factory = get_session_factory(engine)
    with factory() as session:
        try:
            seed_default_schedule_slots(session)
        except Exception as exc:
            session.rollback()
            logger.warning("Default schedule slots seeding skipped: %s", exc)

    logger.info("Database initialized successfully at: %s", engine.url)
    return engine
