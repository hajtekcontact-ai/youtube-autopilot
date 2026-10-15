"""Main Command Line Interface for YouTube AI Autopilot.

Provides the primary CLI entry points:
- serve: Launch the web dashboard and real-time WebSocket server
- produce: Execute daily scheduled video production and publishing
- dry-run: Run complete production simulation without uploading to YouTube
- health: Inspect host, database, FFmpeg, and API readiness
- setup: Interactive/automated initial setup wizard with Arabic prompts
- test-render: Generate a sample test video and validate technical quality
- collect-analytics: Fetch and persist latest YouTube channel performance metrics
- init-db: Create and migrate database tables and seed publishing slots
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from src.config import current_config
from src.database import init_db
from src.utils.logging_config import setup_logging

# Initialize system logger
logger = setup_logging(
    log_level="DEBUG" if current_config.APP_DEBUG else "INFO",
    log_dir=current_config.LOGS_DIR,
)


def cmd_init_db(args: argparse.Namespace) -> int:
    """Initialize SQLite database tables, run migrations, and seed default schedule slots.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: Exit status code (0 for success).
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Database Initialization")
    print("  تهيئة قاعدة البيانات وإنشاء الجداول المجدولة")
    print("=" * 60 + "\n")

    try:
        current_config.ensure_directories()
        engine = init_db(database_url=current_config.DATABASE_URL)
        print(f"[✓] Database successfully initialized at: {engine.url}")
        print("[✓] تم إنشاء وتحديث جداول قاعدة البيانات وجدولة المواعيد الافتراضية بنجاح.")
        return 0
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc, exc_info=True)
        print(f"[✗] Database initialization failed: {exc}")
        print(f"[✗] فشلت تهيئة قاعدة البيانات: {exc}")
        return 1


def cmd_health(args: argparse.Namespace) -> int:
    """Execute complete operational readiness and health inspection.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 if all components are healthy/degraded, 1 if critical components failed.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — System Health Inspection")
    print("  فحص صحة وجاهزية النظام وموارده")
    print("=" * 60 + "\n")

    from src.cloud.health import HealthCheck

    try:
        hc = HealthCheck(db_path=str(current_config.DATA_DIR / "autopilot.db"))
        report = hc.run_health_check()

        overall_status = report.overall_status.upper()
        status_symbol = "[✓]" if overall_status == "HEALTHY" else ("[!] " if overall_status == "DEGRADED" else "[✗]")

        summary_text = getattr(report, "summary_ar", "") or getattr(report, "summary_en", "")
        print(f"Overall Status: {status_symbol} {overall_status}")
        print(f"الحالة العامة للنظام: {summary_text}\n")

        print("Component Breakdown:")
        print("-" * 50)
        comp_list = report.components.values() if isinstance(report.components, dict) else report.components
        for comp in comp_list:
            icon = "[✓]" if comp.status == "healthy" else ("[!] " if comp.status == "degraded" else "[✗]")
            print(f" {icon} {comp.component.upper():<16} : {comp.status:<10} | {comp.message_en}")
            print(f"   {'':<16}   العربية : {comp.message_ar}")

        if report.overall_status == "unhealthy":
            print("\n[!] Attention: Critical components failed verification.")
            return 1

        print("\n[✓] System is operational and ready for production tasks.")
        return 0
    except Exception as exc:
        logger.error("Health check execution failed: %s", exc, exc_info=True)
        print(f"[✗] Health inspection failed to execute: {exc}")
        return 1


def cmd_produce(args: argparse.Namespace) -> int:
    """Run scheduled daily video production pipeline.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 on completed run, 1 on critical abort.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Daily Video Production")
    print("  تشغيل دورة الإنتاج والنشر اليومي للفيديوهات")
    print("=" * 60 + "\n")

    from src.pipeline import ProductionPipeline

    try:
        pipeline = ProductionPipeline()
        report = pipeline.run_daily_production()

        print("\n" + "-" * 50)
        print("  Production Summary / ملخص دورة الإنتاج:")
        print(f"  Total Slots Today:     {report.total_slots}")
        print(f"  Successfully Produced: {report.successful}")
        print(f"  Failed Slots:          {report.failed}")
        print(f"  Execution Time:        {report.total_duration_sec:.1f}s")
        print(f"  API Quota Units Used:  {report.quota_used}")
        print(f"  Arabic Summary:        {report.arabic_summary}")
        print("-" * 50 + "\n")

        return 0 if report.failed == 0 else 1
    except Exception as exc:
        logger.error("Production pipeline encountered an unrecoverable error: %s", exc, exc_info=True)
        print(f"\n[✗] Critical production failure: {exc}")
        return 1


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Simulate complete production pipeline without uploading to YouTube.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 on success, 1 on failure.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Production Pipeline Dry Run")
    print("  تشغيل تجريبي ومحاكاة كاملة لمراحل الإنتاج (بدون رفع)")
    print("=" * 60 + "\n")

    from src.pipeline import ProductionPipeline

    try:
        pipeline = ProductionPipeline()
        report = pipeline.dry_run()

        print("\nValidation Stage Results:")
        print("-" * 50)
        for step, passed in report.steps_passed.items():
            icon = "[✓]" if passed else "[✗]"
            print(f" {icon} {step:<25}: {'PASSED' if passed else 'FAILED'}")

        print(f"\nArabic Summary: {report.arabic_summary}")

        if report.errors:
            print("\nErrors Detected:")
            for err in report.errors:
                print(f" - {err}")
            return 1

        if report.sample_video:
            print("\nSample Video Synthesized:")
            print(f" - Title:    {report.sample_video.get('title')}")
            print(f" - File:     {report.sample_video.get('file_path')}")
            print(f" - Duration: {report.sample_video.get('duration_seconds')}s")

        print("\n[✓] Dry run completed successfully! All stages verified.")
        return 0
    except Exception as exc:
        logger.error("Dry run encountered an error: %s", exc, exc_info=True)
        print(f"\n[✗] Dry run failed: {exc}")
        return 1


def cmd_test_render(args: argparse.Namespace) -> int:
    """Render a short test video and thumbnail to test FFmpeg and graphics stacks.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 on success, 1 on failure.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Test Video Rendering")
    print("  اختبار رندرة فيديو تجريبي والصورة المصغرة")
    print("=" * 60 + "\n")

    from src.quality import QualityChecker
    from src.video.renderer import VideoRenderRequest, VideoRenderer
    from src.video.thumbnail_generator import ThumbnailGenerator

    try:
        output_dir = current_config.OUTPUT_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        test_video_path = str((output_dir / "test_render.mp4").resolve())
        test_thumb_path = str((output_dir / "test_thumb.jpg").resolve())

        print("[1/3] Rendering sample test video...")
        renderer = VideoRenderer(db_path=str(current_config.DATA_DIR / "autopilot.db"))
        req = VideoRenderRequest(
            title="How Gravitational Waves Reshape Spacetime",
            script_text=(
                "Did you know that massive black hole collisions create ripples in the very fabric of spacetime? "
                "Discovered by LIGO in 2015, these gravitational waves travel across billions of light years. "
                "Modern laser interferometers can detect distortions smaller than the width of a single proton! "
                "Subscribe to unlock the deepest mysteries of modern physics."
            ),
            profile="SHORT",
            style="dark_tech",
            output_path=test_video_path,
            voice=current_config.TTS_VOICE,
        )
        rendered = renderer.render_video(req)
        v_path = getattr(rendered, "video_path", None) or getattr(rendered, "file_path", test_video_path)
        print(f"[✓] Video rendered: {v_path} ({rendered.duration_seconds:.1f}s, {rendered.file_size_bytes} bytes)")

        print("[2/3] Generating sample thumbnail...")
        thumb_gen = ThumbnailGenerator(output_dir=output_dir)
        thumb_path = thumb_gen.generate_thumbnail(
            title="Gravitational Waves",
            style="cyber_glow",
            video_type="shorts",
            category="science",
            output_path=test_thumb_path,
            badge_text="COSMIC EVENT",
        )
        print(f"[✓] Thumbnail generated: {thumb_path}")

        print("[3/3] Validating output quality...")
        qc = QualityChecker()
        q_rep = qc.validate_video(test_video_path, "short")
        if q_rep.is_valid:
            print("[✓] Quality validation passed 100%!")
            print(f"[✓] {q_rep.arabic_summary}")
            return 0
        else:
            print("[✗] Quality validation failed:")
            for err in q_rep.errors:
                print(f"  - {err}")
            return 1
    except Exception as exc:
        logger.error("Test render failed: %s", exc, exc_info=True)
        print(f"[✗] Test render encountered an error: {exc}")
        return 1


def cmd_collect_analytics(args: argparse.Namespace) -> int:
    """Collect latest YouTube performance metrics and update analytics snapshots.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 on success, 1 on failure.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Analytics Collection")
    print("  جمع وتحديث إحصائيات القناة والفيديوهات المنشورة")
    print("=" * 60 + "\n")

    from src.analytics.collector import AnalyticsCollector

    try:
        collector = AnalyticsCollector(db_path=str(current_config.DATA_DIR / "autopilot.db"))
        res = collector.collect_channel_analytics(days=28)
        print("[✓] Analytics collected successfully.")
        print(f"  Channel ID: {res.get('channel_id', 'N/A')}")
        print(f"  Videos Evaluated: {len(res.get('snapshots', []))}")
        print("[✓] تم تحديث بيانات المشاهدات، وقت المشاهدة، والمشتركين بنجاح.")
        return 0
    except Exception as exc:
        logger.error("Analytics collection failed: %s", exc, exc_info=True)
        print(f"[✗] Analytics collection error: {exc}")
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Start dashboard Flask application with WebSocket support.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: Exit status code.
    """
    host = args.host or "0.0.0.0"
    port = args.port or int(os.environ.get("PORT", 5000))

    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Dashboard Web Server")
    print("  تشغيل لوحة التحكم والمراقبة الحية")
    print(f"  URL: http://{host}:{port}")
    print("=" * 60 + "\n")

    from src.dashboard.app import app, socketio

    try:
        socketio.run(app, host=host, port=port, debug=current_config.APP_DEBUG)
        return 0
    except Exception as exc:
        logger.error("Dashboard server crashed: %s", exc, exc_info=True)
        print(f"[✗] Server failed to start: {exc}")
        return 1


def cmd_setup(args: argparse.Namespace) -> int:
    """Run interactive or non-interactive initial configuration wizard with Arabic guidance.

    Args:
        args: Parsed CLI arguments.

    Returns:
        int: 0 on setup success.
    """
    print("\n" + "=" * 60)
    print("  YouTube AI Autopilot — Initial Setup Wizard")
    print("  معالج التهيئة والإعداد الأولي للنظام")
    print("=" * 60 + "\n")

    print("مرحباً بك في معالج إعداد نظام YouTube AI Autopilot.")
    print("سيقوم هذا المعالج بتهيئة المجلدات وقاعدة البيانات والتحقق من الإعدادات الأساسية.\n")

    non_interactive = getattr(args, "non_interactive", False) or not sys.stdin.isatty()
    if non_interactive:
        print("[ℹ] جاري التشغيل في الوضع التلقائي غير التفاعلي (Non-interactive mode).")

    # Step 1: Directory initialization
    print("[1/4] جاري إنشاء المجلدات الأساسية للنظام...")
    current_config.ensure_directories()
    (current_config.BASE_DIR / "assets" / "thumbnails").mkdir(parents=True, exist_ok=True)
    print("[✓] تم إنشاء وتأكيد وجود جميع مسارات التخزين والبيانات.")

    # Step 2: Database Initialization
    print("\n[2/4] جاري تهيئة قاعدة بيانات SQLite وجداول المواعيد المجدولة...")
    try:
        init_db(current_config.DATABASE_URL)
        print("[✓] تم إنشاء جداول قاعدة البيانات وجدولة 5 مواعيد نشر يومية افتراضية بنجاح.")
    except Exception as exc:
        print(f"[✗] خطأ أثناء تهيئة قاعدة البيانات: {exc}")
        return 1

    # Step 3: Check Environment Settings
    print("\n[3/4] جاري فحص ملف الإعدادات والمتغيرات البيئية (.env)...")
    env_file = current_config.BASE_DIR / ".env"
    if not env_file.exists():
        example_file = current_config.BASE_DIR / ".env.example"
        if example_file.exists():
            shutil.copy(example_file, env_file)
            print("[✓] تم نسخ ملف الإعدادات النموذجي .env.example إلى .env بنجاح.")
        else:
            env_file.write_text("# YouTube AI Autopilot Configuration\nAPP_ENV=production\n")
            print("[✓] تم إنشاء ملف .env افتراضي جديد.")
    else:
        print("[✓] ملف .env موجود بالفعل.")

    # Step 4: Health Check
    print("\n[4/4] جاري تشغيل فحص الجاهزية والتحقق من التبعيات...")
    try:
        from src.cloud.health import HealthCheck
        hc = HealthCheck(db_path=str(current_config.DATA_DIR / "autopilot.db"))
        report = hc.run_health_check()
        summary_text = getattr(report, "summary_ar", "") or getattr(report, "summary_en", "")
        print(f"[✓] حالة النظام: {report.overall_status.upper()} - {summary_text}")
    except Exception as exc:
        print(f"[!] تحذير أثناء فحص الصحة: {exc}")

    print("\n" + "=" * 60)
    print("  تهانينا! اكتمل إعداد النظام بنجاح وهو جاهز للعمل.")
    print("  Setup completed successfully! System is ready.")
    print("=" * 60)
    print("\nالخطوات التالية الموصى بها:")
    print("  1. تشغيل لوحة التحكم:        python main.py serve")
    print("  2. اختبار رندرة مقطع تجريبي: python main.py test-render")
    print("  3. اختبار محاكاة الإنتاج:    python main.py dry-run")
    print("  4. تشغيل الإنتاج الفعلي:     python main.py produce\n")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and configure argparse CLI command parser.

    Returns:
        argparse.ArgumentParser: Root CLI argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="YouTube AI Autopilot — Automated Video Generation & Channel Operations",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start the dashboard web application")
    p_serve.add_argument("--host", default="0.0.0.0", help="Binding network interface host")
    p_serve.add_argument("--port", type=int, default=5000, help="Listening TCP port")
    p_serve.set_defaults(func=cmd_serve)

    # produce
    p_produce = subparsers.add_parser("produce", help="Run scheduled daily video production")
    p_produce.set_defaults(func=cmd_produce)

    # dry-run
    p_dry_run = subparsers.add_parser("dry-run", help="Run production pipeline simulation without upload")
    p_dry_run.set_defaults(func=cmd_dry_run)

    # health
    p_health = subparsers.add_parser("health", help="Inspect system and API operational health")
    p_health.set_defaults(func=cmd_health)

    # setup
    p_setup = subparsers.add_parser("setup", help="Initial setup wizard and configuration")
    p_setup.add_argument("--non-interactive", action="store_true", help="Run setup in non-interactive mode")
    p_setup.set_defaults(func=cmd_setup)

    # test-render
    p_test_render = subparsers.add_parser("test-render", help="Render sample video and thumbnail")
    p_test_render.set_defaults(func=cmd_test_render)

    # collect-analytics
    p_analytics = subparsers.add_parser("collect-analytics", help="Collect YouTube performance analytics")
    p_analytics.set_defaults(func=cmd_collect_analytics)

    # init-db
    p_init_db = subparsers.add_parser("init-db", help="Initialize database schema and seed slots")
    p_init_db.set_defaults(func=cmd_init_db)

    return parser


def main() -> None:
    """Application CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
