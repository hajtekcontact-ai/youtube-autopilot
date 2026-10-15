"""
SQLite Database Layer for YouTube AI Autopilot Dashboard.

Provides robust database connection management, schema initialization,
seed data generation, and secure data access methods.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from werkzeug.security import generate_password_hash


# Base directory resolution
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "autopilot.db"


def get_db_path() -> Path:
    """Resolve SQLite database path from environment or default location."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        raw_path = db_url.replace("sqlite:///", "")
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path
    return DEFAULT_DB_PATH


def get_db_connection() -> sqlite3.Connection:
    """Create and return a new SQLite connection with foreign keys and dict row factory."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    """Create all required tables and populate seed data if database is empty."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS system_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        is_paused INTEGER NOT NULL DEFAULT 0,
        is_emergency_stopped INTEGER NOT NULL DEFAULT 0,
        last_heartbeat TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        niche TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'ar',
        video_type TEXT NOT NULL DEFAULT 'short',
        status TEXT NOT NULL DEFAULT 'draft',
        views INTEGER NOT NULL DEFAULT 0,
        likes INTEGER NOT NULL DEFAULT 0,
        comments INTEGER NOT NULL DEFAULT 0,
        retention_rate REAL NOT NULL DEFAULT 0.0,
        revenue_usd REAL NOT NULL DEFAULT 0.0,
        thumbnail_url TEXT,
        scheduled_time TEXT,
        published_at TEXT,
        created_at TEXT NOT NULL,
        error_message TEXT
    );

    CREATE TABLE IF NOT EXISTS publishing_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER,
        slot_time TEXT NOT NULL,
        day_of_week TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        content_type TEXT NOT NULL DEFAULT 'short',
        topic TEXT NOT NULL,
        FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS content_ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        topic TEXT NOT NULL,
        niche TEXT NOT NULL,
        hook TEXT NOT NULL,
        script_outline TEXT,
        status TEXT NOT NULL DEFAULT 'idea',
        ai_score REAL NOT NULL DEFAULT 8.5,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pipeline_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT UNIQUE NOT NULL,
        stage TEXT NOT NULL,
        title TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued',
        started_at TEXT NOT NULL,
        completed_at TEXT,
        error_details TEXT
    );

    CREATE TABLE IF NOT EXISTS analytics_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        views INTEGER NOT NULL DEFAULT 0,
        subscribers_gained INTEGER NOT NULL DEFAULT 0,
        watch_time_hours REAL NOT NULL DEFAULT 0.0,
        impressions INTEGER NOT NULL DEFAULT 0,
        ctr_percent REAL NOT NULL DEFAULT 0.0,
        revenue_usd REAL NOT NULL DEFAULT 0.0
    );

    CREATE TABLE IF NOT EXISTS revenue_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        ad_revenue REAL NOT NULL DEFAULT 0.0,
        shorts_feed_revenue REAL NOT NULL DEFAULT 0.0,
        memberships_revenue REAL NOT NULL DEFAULT 0.0,
        api_costs REAL NOT NULL DEFAULT 0.0,
        net_profit REAL NOT NULL DEFAULT 0.0
    );

    CREATE TABLE IF NOT EXISTS experiments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        hypothesis TEXT NOT NULL,
        variant_a TEXT NOT NULL,
        variant_b TEXT NOT NULL,
        metric TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        winner TEXT,
        confidence REAL DEFAULT 0.0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS system_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'error',
        message TEXT NOT NULL,
        details TEXT,
        timestamp TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        retry_count INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS cloud_resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_name TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        usage_percent REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'healthy',
        cost_monthly_usd REAL NOT NULL DEFAULT 0.0,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS api_quotas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_name TEXT NOT NULL,
        used_units INTEGER NOT NULL DEFAULT 0,
        total_limit INTEGER NOT NULL DEFAULT 10000,
        unit_name TEXT NOT NULL,
        reset_time TEXT NOT NULL,
        cost_usd REAL NOT NULL DEFAULT 0.0
    );

    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS security_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        ip_address TEXT NOT NULL,
        user_agent TEXT,
        details TEXT,
        timestamp TEXT NOT NULL
    );
    """)

    conn.commit()

    # Populate seed data if empty
    cursor.execute("SELECT COUNT(*) AS count FROM users")
    if cursor.fetchone()["count"] == 0:
        _seed_initial_data(conn)

    conn.close()


def _seed_initial_data(conn: sqlite3.Connection) -> None:
    """Populate initial seed data for local development and live dashboard rendering."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()

    # Default admin user
    default_admin = os.environ.get("ADMIN_USERNAME", "admin")
    admin_pw_hash = os.environ.get("ADMIN_PASSWORD_HASH")
    if not admin_pw_hash:
        admin_pw_hash = generate_password_hash("admin123")

    cursor.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (default_admin, admin_pw_hash, "admin", now_str),
    )

    # Initial system state
    cursor.execute(
        "INSERT INTO system_state (is_paused, is_emergency_stopped, last_heartbeat) VALUES (0, 0, ?)",
        (now_str,),
    )

    # Seed Videos
    videos_seed = [
        (
            "YT-AI-001",
            "كيف يُغير الذكاء الاصطناعي التوليدي عالم البرمجة في 2026؟",
            "نظرة شاملة وسريعة على أحدث نماذج الذكاء الاصطناعي وكيف يعتمد عليها كبار المطورين.",
            "science_technology_education",
            "ar",
            "short",
            "published",
            24580,
            1840,
            142,
            78.4,
            38.50,
            "/static/images/thumb_ai_coding.jpg",
            (now - timedelta(days=1)).strftime("%Y-%m-%d 14:00:00"),
            (now - timedelta(days=1)).strftime("%Y-%m-%d 14:00:00"),
            (now - timedelta(days=2)).strftime("%Y-%m-%d 10:00:00"),
            None,
        ),
        (
            "YT-AI-002",
            "أسرار خوارزمية يوتيوب لعام 2026: كيف تتصدر صفحة البداية؟",
            "شرح مبسط لقواعد خوارزمية الفيديوهات القصيرة ونسبة الاحتفاظ بالجمهور.",
            "science_technology_education",
            "ar",
            "short",
            "published",
            18920,
            1420,
            96,
            82.1,
            29.10,
            "/static/images/thumb_algorithm.jpg",
            (now - timedelta(hours=6)).strftime("%Y-%m-%d 18:00:00"),
            (now - timedelta(hours=6)).strftime("%Y-%m-%d 18:00:00"),
            (now - timedelta(days=1)).strftime("%Y-%m-%d 11:00:00"),
            None,
        ),
        (
            "YT-AI-003",
            "شرح بنية المحولات Transformers في 60 ثانية للمبتدئين",
            "تبسيط بنية Attention Is All You Need بأسلوب بصري تفاعلي.",
            "science_technology_education",
            "ar",
            "short",
            "scheduled",
            0,
            0,
            0,
            0.0,
            0.0,
            "/static/images/thumb_transformers.jpg",
            (now + timedelta(hours=3)).strftime("%Y-%m-%d %H:00:00"),
            None,
            now_str,
            None,
        ),
        (
            "YT-AI-004",
            "٥ تقنيات حاسوبية ستختفي قريباً وتستبدلها الروبوتات",
            "حقائق ومعلومات تقنية سريعة حول الحوسبة السحابية وأتمتة المهام اليومية.",
            "science_technology_education",
            "ar",
            "short",
            "uploaded",
            0,
            0,
            0,
            0.0,
            0.0,
            "/static/images/thumb_robotics.jpg",
            (now + timedelta(hours=7)).strftime("%Y-%m-%d %H:00:00"),
            None,
            now_str,
            None,
        ),
        (
            "YT-AI-005",
            "الدليل الكامل لبناء خط أنابيب فيديو آلي بدون تدخل بشري",
            "فيديو تفصيلي يستعرض معمارية الأنظمة السحابية لإنشاء المحتوى وتوليد الصوتيات.",
            "science_technology_education",
            "ar",
            "long",
            "produced",
            0,
            0,
            0,
            0.0,
            0.0,
            "/static/images/thumb_pipeline.jpg",
            (now + timedelta(days=1)).strftime("%Y-%m-%d 20:00:00"),
            None,
            now_str,
            None,
        ),
        (
            "YT-AI-006",
            "أفضل لغات البرمجة للذكاء الاصطناعي في السوق العربي",
            "مقارنة عملية وسريعة بين بايثون ولغات الأنظمة في معالجة البيانات الضخمة.",
            "science_technology_education",
            "ar",
            "short",
            "failed",
            0,
            0,
            0,
            0.0,
            0.0,
            "/static/images/thumb_python.jpg",
            (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:00:00"),
            None,
            now_str,
            "فشل في استخراج التسميات التوضيحية الصوتية عبر محرك TTS",
        ),
    ]

    cursor.executemany(
        """
        INSERT INTO videos (
            video_id, title, description, niche, language, video_type,
            status, views, likes, comments, retention_rate, revenue_usd,
            thumbnail_url, scheduled_time, published_at, created_at, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        videos_seed,
    )

    # Publishing schedule seed
    schedule_seed = [
        (1, "12:00", "الخميس", "completed", "short", "ذكاء اصطناعي وتطوير البرمجيات"),
        (2, "16:00", "الخميس", "completed", "short", "أسرار خوارزميات يوتيوب"),
        (3, "20:00", "الخميس", "pending", "short", "شرح بنية Transformers"),
        (4, "23:00", "الخميس", "pending", "short", "تقنيات حاسوبية قيد التلاشي"),
        (5, "18:00", "الجمعة", "pending", "long", "الدليل الكامل لخط إنتاج الفيديو"),
    ]
    cursor.executemany(
        """
        INSERT INTO publishing_schedule (
            video_id, slot_time, day_of_week, status, content_type, topic
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        schedule_seed,
    )

    # Content ideas seed
    ideas_seed = [
        (
            "ما هي الحوسبة الكمومية وهل تهدد التشفير الحديث؟",
            "الحوسبة الكمومية والأمن السيبراني",
            "science_technology_education",
            "هل يمكن لحاسوب كمومي كسر كلمات سر العالم في ثوانٍ؟",
            "مقدمة سريعة -> مفهوم الكيوبت -> خوارزمية شور -> الواقع اليوم -> خاتمة",
            "idea",
            9.2,
            now_str,
        ),
        (
            "مستقبل قواعد البيانات الموزعة في الأنظمة السحابية",
            "معمارية البرمجيات الحديثة",
            "science_technology_education",
            "لماذا لا تكفي قواعد البيانات التقليدية للملايين من المستخدمين؟",
            "التوسع الأفقي -> مبدأ CAP -> أمثلة حية -> نصيحة للمطورين",
            "approved",
            8.8,
            now_str,
        ),
        (
            "أقوى 3 ميزات سرية في لغة بايثون 3.14 لا يعرفها أغلب المبرمجين",
            "نصائح برمجية وحيل تقنية",
            "science_technology_education",
            "هذه الميزة في بايثون ستختصر نصف كودك البرمجي!",
            "الميزة الأولى -> الميزة الثانية -> الميزة الثالثة مع مثال حي",
            "scripted",
            9.5,
            now_str,
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO content_ideas (
            title, topic, niche, hook, script_outline, status, ai_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ideas_seed,
    )

    # Pipeline jobs seed
    jobs_seed = [
        (
            "JOB-9941",
            "visual_generation",
            "شرح بنية المحولات Transformers في 60 ثانية للمبتدئين",
            75,
            "running",
            (now - timedelta(minutes=8)).strftime("%Y-%m-%d %H:%M:%S"),
            None,
            None,
        ),
        (
            "JOB-9942",
            "video_assembly",
            "٥ تقنيات حاسوبية ستختفي قريباً وتستبدلها الروبوتات",
            90,
            "running",
            (now - timedelta(minutes=14)).strftime("%Y-%m-%d %H:%M:%S"),
            None,
            None,
        ),
        (
            "JOB-9940",
            "tts_audio",
            "أفضل لغات البرمجة للذكاء الاصطناعي في السوق العربي",
            40,
            "failed",
            (now - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
            (now - timedelta(minutes=44)).strftime("%Y-%m-%d %H:%M:%S"),
            "Timeout during synthesis in edge-tts engine",
        ),
        (
            "JOB-9939",
            "upload",
            "أسرار خوارزمية يوتيوب لعام 2026: كيف تتصدر صفحة البداية؟",
            100,
            "completed",
            (now - timedelta(hours=6, minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
            (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
            None,
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO pipeline_jobs (
            job_id, stage, title, progress, status, started_at, completed_at, error_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        jobs_seed,
    )

    # Analytics snapshots (last 7 days)
    for i in range(7, 0, -1):
        day_date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        cursor.execute(
            """
            INSERT OR IGNORE INTO analytics_snapshots (
                date, views, subscribers_gained, watch_time_hours, impressions, ctr_percent, revenue_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day_date,
                15000 + (i * 1800),
                85 + (i * 12),
                320.5 + (i * 24.2),
                180000 + (i * 12000),
                8.2 + (i * 0.3),
                28.50 + (i * 4.20),
            ),
        )

    # Revenue records (last 7 days)
    for i in range(7, 0, -1):
        day_date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        ad_rev = 18.50 + (i * 3.1)
        shorts_rev = 12.00 + (i * 1.5)
        member_rev = 5.00 + (i * 0.8)
        costs = 1.20 + (i * 0.15)
        net = (ad_rev + shorts_rev + member_rev) - costs
        cursor.execute(
            """
            INSERT OR IGNORE INTO revenue_records (
                date, ad_revenue, shorts_feed_revenue, memberships_revenue, api_costs, net_profit
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (day_date, round(ad_rev, 2), round(shorts_rev, 2), round(member_rev, 2), round(costs, 2), round(net, 2)),
        )

    # Experiments seed
    experiments_seed = [
        (
            "مقارنة الصورة المصغرة المشرقة مقابل الداكنة",
            "الصور ذات التباين العالي والخلفيات الداكنة تحقق نسبة نقر للظهور (CTR) أعلى بنسبة 15%",
            "خلفية ساطعة + نص أصفر غليظ",
            "خلفية داكنة + رسم ثلاثي الأبعاد وعنصر مفاجأة",
            "ctr",
            "running",
            None,
            74.5,
            (now - timedelta(days=3)).strftime("%Y-%m-%d"),
        ),
        (
            "سرعة النبرة الصوتية في أول 3 ثوانٍ",
            "البدء المباشر بالسؤال دون مقدمة يزيد من معدل الاحتفاظ بالجمهور",
            "مقدمة صوتية ترحيبية قصيرة (ثانيتان)",
            "بداية صادمة فورية بالسؤال والخطاف",
            "retention",
            "completed",
            "الخيار B (خطاف فوري)",
            94.8,
            (now - timedelta(days=10)).strftime("%Y-%m-%d"),
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO experiments (
            title, hypothesis, variant_a, variant_b, metric, status, winner, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        experiments_seed,
    )

    # System errors seed
    errors_seed = [
        (
            "tts_engine",
            "error",
            "خطأ في الاتصال بخدمة توليد الصوت الاصطناعي (Edge TTS)",
            "ConnectionTimeout: Failed to establish WebSocket connection with edge-tts endpoint after 3 retries.",
            (now - timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
            0,
            2,
        ),
        (
            "youtube_api",
            "warning",
            "اقتراب استهلاك حصة الرفع اليومية من الحد الموصى به",
            "YouTube API Quota Warning: 7,850 units consumed out of 10,000 threshold warning mark.",
            (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            0,
            0,
        ),
        (
            "video_renderer",
            "critical",
            "عدم كفاية الذاكرة المؤقتة أثناء تصيير فيديو ثلاثي الأبعاد عالي الدقة",
            "MemoryError: Buffer allocation exceeded 2048MB during frame compositing.",
            (now - timedelta(days=1)).strftime("%Y-%m-%d 15:30:00"),
            1,
            1,
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO system_errors (
            module, severity, message, details, timestamp, resolved, retry_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        errors_seed,
    )

    # Cloud resources seed
    resources_seed = [
        ("خادم المعالجة المركزية (CPU Node)", "cpu", 42.5, "healthy", 14.20, now_str),
        ("الذاكرة العشوائية السحابية (RAM 8GB)", "memory", 68.0, "healthy", 9.80, now_str),
        ("سعة التخزين الدائمة (NVMe SSD)", "disk", 31.2, "healthy", 4.50, now_str),
        ("نطاق الشبكة الصادر (Bandwidth Egress)", "network", 19.4, "healthy", 2.10, now_str),
    ]
    cursor.executemany(
        """
        INSERT INTO cloud_resources (
            service_name, resource_type, usage_percent, status, cost_monthly_usd, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        resources_seed,
    )

    # API quotas seed
    quotas_seed = [
        ("YouTube Data API v3", 3850, 10000, "وحدة طلب (Units)", "غداً الساعة 03:00 ص", 0.00),
        ("Google Gemini AI Studio", 1420, 15000, "ألف رمز (Tokens)", "تجديد شهري", 0.00),
        ("Edge-TTS Voice Engine", 48000, 100000, "حرف صوتي (Chars)", "غير محدود مجاني", 0.00),
        ("GitHub Actions Runners", 120, 2000, "دقيقة تشغيل (Minutes)", "تجديد في أول الشهر", 0.00),
    ]
    cursor.executemany(
        """
        INSERT INTO api_quotas (
            service_name, used_units, total_limit, unit_name, reset_time, cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        quotas_seed,
    )

    # System settings seed
    settings_seed = [
        ("channel_niche", "science_technology_education", "content"),
        ("content_language", "ar-SA", "content"),
        ("tts_voice", "ar-SA-HamedNeural", "content"),
        ("shorts_per_day", "4", "schedule"),
        ("long_videos_per_day", "1", "schedule"),
        ("publish_timezone", "Asia/Riyadh", "schedule"),
        ("auto_publish_enabled", "true", "schedule"),
        ("gemini_model", "gemini-1.5-flash", "ai"),
        ("notify_on_publish", "true", "notification"),
        ("notify_on_error", "true", "notification"),
        ("notification_email", "admin@autopilot.local", "notification"),
        ("cost_kill_switch", "false", "security"),
        ("max_daily_api_cost_usd", "5.00", "security"),
        ("auto_retry_failed_jobs", "true", "system"),
    ]
    cursor.executemany(
        """
        INSERT INTO system_settings (key, value, category, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        [(k, v, c, now_str) for k, v, c in settings_seed],
    )

    # Security audit log seed
    audit_seed = [
        ("login_success", "127.0.0.1", "Mozilla/5.0 (Admin Console)", "تسجيل دخول ناجح لحساب المسؤول", now_str),
        ("system_init", "127.0.0.1", "System Core", "تهيئة قاعدة البيانات الأولية للنظام التلقائي", now_str),
    ]
    cursor.executemany(
        """
        INSERT INTO security_audit_log (
            event_type, ip_address, user_agent, details, timestamp
        ) VALUES (?, ?, ?, ?, ?)
        """,
        audit_seed,
    )

    conn.commit()


# ==============================================================================
# Database Helper Query Functions
# ==============================================================================

def get_overview_stats() -> Dict[str, Any]:
    """Retrieve summarized statistics for dashboard overview page."""
    conn = get_db_connection()
    cursor = conn.cursor()

    total_videos = cursor.execute("SELECT COUNT(*) AS count FROM videos").fetchone()["count"]
    
    # Published today
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
    published_today = cursor.execute(
        "SELECT COUNT(*) AS count FROM videos WHERE status = 'published' AND published_at >= ?",
        (today_start,),
    ).fetchone()["count"]

    # Pending or producing videos
    pending_videos = cursor.execute(
        "SELECT COUNT(*) AS count FROM videos WHERE status IN ('draft', 'producing', 'produced', 'uploaded', 'scheduled')"
    ).fetchone()["count"]

    # System errors count (unresolved)
    active_errors = cursor.execute(
        "SELECT COUNT(*) AS count FROM system_errors WHERE resolved = 0"
    ).fetchone()["count"]

    # Production success rate
    completed_jobs = cursor.execute(
        "SELECT COUNT(*) AS count FROM pipeline_jobs WHERE status = 'completed'"
    ).fetchone()["count"]
    total_pipeline_jobs = cursor.execute("SELECT COUNT(*) AS count FROM pipeline_jobs").fetchone()["count"]
    success_rate = round((completed_jobs / total_pipeline_jobs * 100), 1) if total_pipeline_jobs > 0 else 100.0

    # API Quota usage percentage (YouTube Data API)
    yt_quota = cursor.execute(
        "SELECT used_units, total_limit FROM api_quotas WHERE service_name LIKE '%YouTube%'"
    ).fetchone()
    if yt_quota and yt_quota["total_limit"] > 0:
        quota_percent = round((yt_quota["used_units"] / yt_quota["total_limit"]) * 100, 1)
        quota_used = yt_quota["used_units"]
        quota_total = yt_quota["total_limit"]
    else:
        quota_percent = 38.5
        quota_used = 3850
        quota_total = 10000

    # Videos produced this week
    week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
    produced_this_week = cursor.execute(
        "SELECT COUNT(*) AS count FROM videos WHERE created_at >= ?",
        (week_start,),
    ).fetchone()["count"]

    conn.close()

    return {
        "total_videos": total_videos,
        "published_today": published_today,
        "pending_videos": pending_videos,
        "active_errors": active_errors,
        "success_rate": success_rate,
        "quota_percent": quota_percent,
        "quota_used": quota_used,
        "quota_total": quota_total,
        "produced_this_week": produced_this_week,
    }


def get_today_schedule() -> List[Dict[str, Any]]:
    """Retrieve schedule items for current day."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, v.title AS video_title, v.thumbnail_url 
        FROM publishing_schedule s
        LEFT JOIN videos v ON s.video_id = v.id
        ORDER BY s.slot_time ASC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_recent_activity(limit: int = 8) -> List[Dict[str, Any]]:
    """Retrieve recent system activity entries from audit log and jobs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM security_audit_log ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_7day_analytics() -> List[Dict[str, Any]]:
    """Retrieve last 7 days of performance and viewer engagement metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM analytics_snapshots ORDER BY date ASC LIMIT 7"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_videos(
    page: int = 1,
    per_page: int = 12,
    search: str = "",
    status_filter: str = "all",
) -> Tuple[List[Dict[str, Any]], int]:
    """Retrieve paginated and filtered videos list."""
    conn = get_db_connection()
    cursor = conn.cursor()

    conditions: List[str] = []
    params: List[Any] = []

    if search.strip():
        conditions.append("(title LIKE ? OR description LIKE ?)")
        search_pattern = f"%{search.strip()}%"
        params.extend([search_pattern, search_pattern])

    if status_filter and status_filter != "all":
        conditions.append("status = ?")
        params.append(status_filter)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    count_query = f"SELECT COUNT(*) AS count FROM videos {where_clause}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["count"]

    offset = (page - 1) * per_page
    data_query = f"""
        SELECT * FROM videos {where_clause}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    cursor.execute(data_query, params)
    rows = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return rows, total_count


def get_video_by_id(video_id: int) -> Optional[Dict[str, Any]]:
    """Fetch single video by its primary key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_publishing_calendar() -> List[Dict[str, Any]]:
    """Retrieve all publishing schedule entries with video details."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, v.title AS video_title, v.video_type, v.status AS video_status
        FROM publishing_schedule s
        LEFT JOIN videos v ON s.video_id = v.id
        ORDER BY s.slot_time ASC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_content_ideas() -> List[Dict[str, Any]]:
    """Retrieve all content ideas from AI generator pipeline."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM content_ideas ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_pipeline_jobs() -> List[Dict[str, Any]]:
    """Retrieve all active and recent video generation pipeline jobs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pipeline_jobs ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_analytics_summary() -> Dict[str, Any]:
    """Retrieve comprehensive analytics metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            SUM(views) AS total_views,
            SUM(subscribers_gained) AS total_subs,
            SUM(watch_time_hours) AS total_watch_time,
            AVG(ctr_percent) AS avg_ctr,
            SUM(revenue_usd) AS total_rev
        FROM analytics_snapshots
    """)
    totals = dict(cursor.fetchone() or {})

    cursor.execute("SELECT * FROM analytics_snapshots ORDER BY date DESC LIMIT 14")
    snapshots = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {
        "totals": totals,
        "snapshots": snapshots,
    }


def get_revenue_data() -> List[Dict[str, Any]]:
    """Retrieve financial and monetization records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM revenue_records ORDER BY date DESC LIMIT 30")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_experiments() -> List[Dict[str, Any]]:
    """Retrieve all A/B testing and algorithmic experiment records."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM experiments ORDER BY id DESC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_system_errors(
    severity: str = "all",
    module: str = "all",
    unresolved_only: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieve filtered system and background job errors."""
    conn = get_db_connection()
    cursor = conn.cursor()

    conditions: List[str] = []
    params: List[Any] = []

    if severity != "all":
        conditions.append("severity = ?")
        params.append(severity)

    if module != "all":
        conditions.append("module = ?")
        params.append(module)

    if unresolved_only:
        conditions.append("resolved = 0")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM system_errors {where_clause} ORDER BY timestamp DESC LIMIT 100"
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def retry_error(error_id: int) -> bool:
    """Increment retry counter and mark error job for re-execution."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE system_errors SET retry_count = retry_count + 1 WHERE id = ?",
        (error_id,),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def get_cloud_resources() -> List[Dict[str, Any]]:
    """Retrieve cloud resource utilization metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cloud_resources ORDER BY id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_api_quotas() -> List[Dict[str, Any]]:
    """Retrieve API quota allocations and usage status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM api_quotas ORDER BY id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_settings() -> Dict[str, str]:
    """Retrieve all configuration settings as key-value dictionary."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM system_settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def save_settings(settings: Dict[str, str]) -> None:
    """Update system settings with provided values."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    for key, value in settings.items():
        cursor.execute(
            """
            INSERT INTO system_settings (key, value, category, updated_at)
            VALUES (?, ?, 'general', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value), now_str),
        )
    conn.commit()
    conn.close()


def get_system_state() -> Dict[str, Any]:
    """Retrieve current operational state (paused, emergency stopped)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_paused, is_emergency_stopped, last_heartbeat FROM system_state ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "is_paused": bool(row["is_paused"]),
            "is_emergency_stopped": bool(row["is_emergency_stopped"]),
            "last_heartbeat": row["last_heartbeat"],
        }
    return {"is_paused": False, "is_emergency_stopped": False, "last_heartbeat": ""}


def set_system_state(is_paused: Optional[bool] = None, is_emergency_stopped: Optional[bool] = None) -> None:
    """Update system state flags."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()

    current = get_system_state()
    new_paused = current["is_paused"] if is_paused is None else is_paused
    new_stop = current["is_emergency_stopped"] if is_emergency_stopped is None else is_emergency_stopped

    cursor.execute(
        """
        UPDATE system_state 
        SET is_paused = ?, is_emergency_stopped = ?, last_heartbeat = ?
        WHERE id = (SELECT id FROM system_state ORDER BY id DESC LIMIT 1)
        """,
        (1 if new_paused else 0, 1 if new_stop else 0, now_str),
    )
    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO system_state (is_paused, is_emergency_stopped, last_heartbeat) VALUES (?, ?, ?)",
            (1 if new_paused else 0, 1 if new_stop else 0, now_str),
        )
    conn.commit()
    conn.close()


def add_audit_log(event_type: str, ip_address: str, user_agent: Optional[str], details: str) -> None:
    """Record an audit trail event for security tracking."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO security_audit_log (event_type, ip_address, user_agent, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, ip_address, user_agent or "Unknown", details, now_str),
    )
    conn.commit()
    conn.close()


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Retrieve user record by username."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_password(username: str, new_password_hash: str) -> bool:
    """Update password hash for the specified user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (new_password_hash, username),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated
