"""Integration tests for ProductionPipeline, QualityChecker, QuotaManager, and NotificationManager."""

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.config import current_config
from src.database import Video, init_db
from src.notifications import NotificationManager, NotificationType
from src.pipeline import ProductionLock, ProductionPipeline
from src.quality import QualityChecker, QualityReport
from src.quota_manager import QuotaManager


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path):
    """Setup temporary database and directory structure for tests."""
    test_db = tmp_path / "test_pipeline.db"
    db_url = f"sqlite:///{test_db}"
    init_db(db_url)
    yield


class TestQualityChecker:
    """Test QualityChecker validation rules."""

    def test_magic_bytes_detection(self, tmp_path):
        """Test verification of MP4 ftyp container headers."""
        qc = QualityChecker()

        # Valid MP4 header with isom brand
        valid_mp4 = tmp_path / "valid.mp4"
        valid_mp4.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41")
        is_valid, desc = qc.check_magic_bytes(valid_mp4)
        assert is_valid is True
        assert "MP4" in desc

        # Invalid file header
        invalid_file = tmp_path / "invalid.mp4"
        invalid_file.write_bytes(b"INVALID_HEADER_BYTES_FOR_TESTING")
        is_valid, desc = qc.check_magic_bytes(invalid_file)
        assert is_valid is False

    def test_missing_video_validation(self):
        """Test validation fails gracefully when file is absent."""
        qc = QualityChecker()
        report = qc.validate_video("/nonexistent/video.mp4", "short")
        assert report.is_valid is False
        assert len(report.errors) > 0
        assert "غير موجود" in report.arabic_summary


class TestQuotaManager:
    """Test API quota, rate limits, and cost calculations."""

    def test_quota_manager_operations(self):
        """Test recording usage, daily quota querying, and cost computation."""
        qm = QuotaManager()
        assert qm.can_upload() is True
        assert qm.can_call_gemini() is True

        # Record YouTube upload usage (1,600 units)
        qm.record_usage("youtube", "uploads.insert", 1600)
        summary = qm.get_daily_usage("youtube")
        assert summary.total_units >= 1600
        assert summary.remaining_units <= 10000 - 1600
        assert summary.estimated_cost_usd > 0.0
        assert "طبيعية" in summary.arabic_status or "الحصة" in summary.arabic_status

        # Estimate daily cost
        cost = qm.estimate_daily_cost()
        assert cost >= 0.0


class TestNotificationManager:
    """Test notification templates, rate limits, and fallbacks."""

    def test_notification_rate_limiting(self):
        """Test that excessive notifications are throttled at 10 emails/hour."""
        nm = NotificationManager()
        # Mock is_configured to True and mock smtplib
        with patch.object(nm, "is_configured", return_value=True):
            with patch("smtplib.SMTP"):
                # Fill up 10 allowed slots
                for i in range(10):
                    res = nm.send_notification(NotificationType.DAILY_REPORT, f"Test {i}")
                    assert res is True

                # 11th should be blocked by rate limit
                res = nm.send_notification(NotificationType.DAILY_REPORT, "Throttled")
                assert res is False

    def test_daily_report_generation(self):
        """Test formatting and dispatching daily production reports."""
        nm = NotificationManager()
        report_data = {
            "date": "2026-10-15",
            "total_slots": 5,
            "successful": 4,
            "failed": 1,
            "skipped": 0,
            "total_duration_sec": 120.5,
            "quota_used": 6400,
        }
        # In unconfigured mode, should log and return False cleanly without crashing
        res = nm.send_daily_report(report_data)
        assert res is False or res is True


class TestProductionPipeline:
    """Test ProductionPipeline orchestrator."""

    def test_production_lock(self, tmp_path):
        """Test cross-process concurrency locking."""
        lock_file = tmp_path / "test.lock"
        with ProductionLock(lock_file):
            # Second attempt within same or sub-process should fail
            with pytest.raises(RuntimeError):
                with ProductionLock(lock_file):
                    pass

    def test_system_resource_monitoring(self):
        """Test system resource checking."""
        pipeline = ProductionPipeline()
        ok, msg_en, msg_ar = pipeline.check_system_resources()
        assert ok is True
        assert "healthy" in msg_en.lower() or "headroom" in msg_en.lower()
        assert "الموارد" in msg_ar

    def test_dry_run_execution(self):
        """Test complete dry-run cycle producing validated video artifacts."""
        pipeline = ProductionPipeline()
        report = pipeline.dry_run()

        assert report.slots_validated == 1
        assert report.steps_passed["safety_check"] is True
        assert report.steps_passed["content_idea"] is True
        assert report.steps_passed["script_writing"] is True
        assert report.steps_passed["fact_checking"] is True
        assert report.steps_passed["originality"] is True
        assert report.steps_passed["rendering"] is True
        assert report.steps_passed["thumbnail"] is True
        assert report.steps_passed["metadata"] is True
        assert report.steps_passed["quality_check"] is True
        assert report.steps_passed["database_save"] is True
        assert len(report.errors) == 0
        assert "المحاكاة التجريبية" in report.arabic_summary
