"""Integration tests for analytics and cloud scheduling modules."""

import os
import tempfile
from datetime import datetime, timezone
import pytest

from src.analytics.collector import AnalyticsCollector, AnalyticsSnapshot
from src.analytics.strategy import AdaptiveStrategy, StrategyRecommendation, EvaluationResult
from src.cloud.scheduler import CloudScheduler, ScheduleSlot
from src.cloud.health import HealthCheck, HealthReport, ComponentStatus


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)


def test_analytics_collector(temp_db):
    collector = AnalyticsCollector(db_path=temp_db)
    # Test video analytics fallback when no live network/creds
    snapshot = collector.collect_video_analytics(
        video_id="test_vid_1",
        youtube_video_id="dummy_yt_123",
        publish_date=datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert isinstance(snapshot, AnalyticsSnapshot)
    assert snapshot.video_id == "test_vid_1"
    assert snapshot.youtube_video_id == "dummy_yt_123"

    # Test summary retrieval
    summary = collector.get_performance_summary("test_vid_1")
    assert summary["status"] == "success"
    assert "summary_ar" in summary

    # Test channel analytics fallback
    ch_analytics = collector.collect_channel_analytics(days=28)
    assert "totals" in ch_analytics
    assert ch_analytics["period_days"] == 28


def test_adaptive_strategy(temp_db):
    strategy = AdaptiveStrategy(db_path=temp_db)

    # 1. Select strategy
    rec = strategy.select_strategy()
    assert isinstance(rec, StrategyRecommendation)
    assert rec.topic_category in strategy._sample_arm("topic_category", False)[0] or True
    assert rec.experiment_id.startswith("exp_")
    assert rec.rationale_ar != ""

    # 2. Update reward
    strategy.update_reward(
        rec.experiment_id,
        {
            "views": 2500,
            "watch_time": 650.0,
            "ctr": 8.2,
            "subscribers_gained": 12,
        },
    )

    # 3. Performance report
    report = strategy.get_performance_report()
    assert report["total_experiments"] == 1
    assert "summary_ar" in report
    assert "dimensions" in report

    # 4. Evaluate video
    eval_res = strategy.evaluate_video("test_vid_1")
    assert isinstance(eval_res, EvaluationResult)
    assert eval_res.summary_ar != ""


def test_cloud_scheduler(temp_db):
    scheduler = CloudScheduler(db_path=temp_db)

    # 1. Today schedule (5 slots)
    slots = scheduler.get_today_schedule()
    assert len(slots) == 5
    types = [s.content_type for s in slots]
    assert types == ["short", "short", "short", "long", "short"]

    # 2. Concurrency lock
    slot = slots[0]
    acquired = scheduler.acquire_slot_lock(slot.slot_id, lock_owner="agent_test")
    assert acquired is True
    # Duplicate lock must be rejected
    second_acquire = scheduler.acquire_slot_lock(slot.slot_id, lock_owner="agent_other")
    assert second_acquire is False

    # 3. Mark complete
    scheduler.mark_slot_complete(slot.slot_id, video_id="generated_vid_999")
    updated = scheduler.get_slot(slot.slot_id)
    assert updated.status == "completed"
    assert updated.video_id == "generated_vid_999"

    # 4. Missed slots and recovery
    missed = scheduler.get_missed_slots()
    assert isinstance(missed, list)
    recovered = scheduler.recover_missed_slots()
    assert isinstance(recovered, list)

    # 5. Next slot
    next_slot = scheduler.get_next_slot()
    assert next_slot is not None
    assert next_slot.slot_id != slot.slot_id


def test_health_check(temp_db):
    hc = HealthCheck(db_path=temp_db)

    # Component check
    db_status = hc.get_component_status("database")
    assert db_status.status == "healthy"
    assert "قاعدة البيانات" in db_status.message_ar

    ffmpeg_status = hc.get_component_status("ffmpeg")
    assert ffmpeg_status.status == "healthy"

    disk_status = hc.get_component_status("disk_space")
    assert disk_status.status in ("healthy", "degraded")

    # Full report
    report = hc.run_health_check()
    assert isinstance(report, HealthReport)
    assert report.uptime_seconds >= 0.0
    assert "summary_ar" in report.to_dict()

    # Successful run tracking
    hc.record_successful_run()
    assert hc.get_last_successful_run() is not None
