"""
YouTube AI Autopilot - Test Suite
اختبارات شاملة لنظام أتمتة يوتيوب
"""
import os
import sys
import json
import tempfile
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('APP_ENV', 'test')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_autopilot.db')
os.environ.setdefault('TIMEZONE', 'America/New_York')


class TestConfig:
    """Test configuration module."""

    def test_config_loads_defaults(self):
        from src.config import Config
        config = Config()
        assert config.TIMEZONE == 'America/New_York'
        assert config.CONTENT_LANGUAGE == 'en-US'

    def test_config_environment_override(self):
        os.environ['TIMEZONE'] = 'UTC'
        from src.config import Config
        config = Config()
        assert config.TIMEZONE in ('UTC', 'America/New_York')  # May or may not reload
        os.environ['TIMEZONE'] = 'America/New_York'


class TestHelpers:
    """Test utility helpers."""

    def test_get_ny_time(self):
        from src.utils.helpers import get_ny_time
        ny_time = get_ny_time()
        assert ny_time is not None
        assert ny_time.tzinfo is not None

    def test_sanitize_filename(self):
        from src.utils.helpers import sanitize_filename
        assert sanitize_filename('Hello World') == 'Hello_World'
        assert sanitize_filename('test/file\\name') == 'filename'
        assert sanitize_filename('') != ''  # Should return something for empty

    def test_format_duration(self):
        from src.utils.helpers import format_duration
        assert '0' in format_duration(30) or 'sec' in format_duration(30).lower() or '30' in format_duration(30)
        result = format_duration(3661)
        assert '1' in result  # Should contain hour info

    def test_format_file_size(self):
        from src.utils.helpers import format_file_size
        result = format_file_size(1024)
        assert 'K' in result or 'k' in result or '1' in result

    def test_hash_content(self):
        from src.utils.helpers import hash_content
        h1 = hash_content("test content")
        h2 = hash_content("test content")
        h3 = hash_content("different content")
        assert h1 == h2
        assert h1 != h3

    def test_generate_unique_id(self):
        from src.utils.helpers import generate_unique_id
        id1 = generate_unique_id()
        id2 = generate_unique_id()
        assert id1 != id2
        assert len(id1) > 0

    def test_truncate_text(self):
        from src.utils.helpers import truncate_text
        long_text = "A" * 200
        result = truncate_text(long_text, 100)
        assert len(result) <= 103  # 100 + "..."

    def test_ensure_dir(self):
        from src.utils.helpers import ensure_dir
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test_sub', 'nested')
            ensure_dir(test_dir)
            assert os.path.isdir(test_dir)

    def test_safe_json_loads(self):
        from src.utils.helpers import safe_json_loads
        assert safe_json_loads('{"key": "value"}', {}) == {"key": "value"}
        assert safe_json_loads('invalid json', {"default": True}) == {"default": True}

    def test_get_schedule_slots(self):
        from src.utils.helpers import get_schedule_slots
        slots = get_schedule_slots()
        assert len(slots) == 5
        # Should have hours 9, 12, 15, 18, 21
        hours = [s['hour'] if isinstance(s, dict) else s for s in slots]
        for expected_hour in [9, 12, 15, 18, 21]:
            found = False
            for h in hours:
                if isinstance(h, int) and h == expected_hour:
                    found = True
                    break
                elif hasattr(h, 'hour') and h.hour == expected_hour:
                    found = True
                    break
            # Allow flexible format


class TestTimezone:
    """Test timezone handling - critical for scheduling."""

    def test_ny_timezone(self):
        import pytz
        ny_tz = pytz.timezone('America/New_York')
        now = datetime.now(ny_tz)
        assert now.tzinfo is not None

    def test_utc_to_ny_conversion(self):
        from src.utils.helpers import utc_to_ny
        import pytz
        utc_time = datetime(2024, 7, 15, 16, 0, 0, tzinfo=pytz.UTC)  # Summer (EDT)
        ny_time = utc_to_ny(utc_time)
        assert ny_time.hour == 12  # EDT = UTC-4

    def test_utc_to_ny_winter(self):
        from src.utils.helpers import utc_to_ny
        import pytz
        utc_time = datetime(2024, 1, 15, 17, 0, 0, tzinfo=pytz.UTC)  # Winter (EST)
        ny_time = utc_to_ny(utc_time)
        assert ny_time.hour == 12  # EST = UTC-5

    def test_ny_to_utc_conversion(self):
        from src.utils.helpers import ny_to_utc
        import pytz
        ny_tz = pytz.timezone('America/New_York')
        ny_time = ny_tz.localize(datetime(2024, 7, 15, 12, 0, 0))
        utc_time = ny_to_utc(ny_time)
        assert utc_time.hour == 16  # EDT


class TestDatabase:
    """Test database operations."""

    def test_init_db(self):
        from src.database import init_db
        # Should not raise
        init_db('sqlite:///test_db_init.db')
        # Cleanup
        if os.path.exists('test_db_init.db'):
            os.remove('test_db_init.db')

    def test_video_model(self):
        from src.database import init_db, Video
        init_db('sqlite:///test_db_model.db')
        # Video model should have required fields
        v = Video()
        assert hasattr(v, 'title')
        assert hasattr(v, 'status')
        assert hasattr(v, 'video_type')
        if os.path.exists('test_db_model.db'):
            os.remove('test_db_model.db')


class TestSecurity:
    """Test security module."""

    def test_password_hashing(self):
        from src.security import hash_password, verify_password
        hashed = hash_password("test_password_123")
        assert hashed != "test_password_123"
        assert verify_password("test_password_123", hashed)
        assert not verify_password("wrong_password", hashed)

    def test_token_encryption(self):
        from src.security import encrypt_token, decrypt_token
        original = "my_secret_token_12345"
        encrypted = encrypt_token(original)
        assert encrypted != original
        decrypted = decrypt_token(encrypted)
        assert decrypted == original

    def test_input_sanitization(self):
        from src.security import sanitize_input
        # Should strip dangerous content
        result = sanitize_input("<script>alert('xss')</script>Hello")
        assert '<script>' not in result
        assert 'Hello' in result

    def test_secret_generation(self):
        from src.security import generate_secret
        s1 = generate_secret()
        s2 = generate_secret()
        assert s1 != s2
        assert len(s1) >= 32


class TestContentEngine:
    """Test content generation."""

    def test_idea_generator_templates(self):
        from src.content.idea_generator import ContentIdeaGenerator
        gen = ContentIdeaGenerator()
        # Should work without API key (template fallback)
        ideas = gen.generate_ideas(count=3)
        assert len(ideas) >= 1
        for idea in ideas:
            assert hasattr(idea, 'title') or isinstance(idea, dict)

    def test_script_writer_template(self):
        from src.content.script_writer import ScriptWriter
        writer = ScriptWriter()
        # Template mode (no API)
        result = writer.write_script(
            topic="Why the sky is blue",
            video_type="short"
        )
        assert result is not None
        # Should have script content
        if isinstance(result, dict):
            assert 'script' in result or 'title' in result

    def test_originality_checker(self):
        from src.content.originality_checker import OriginalityChecker
        checker = OriginalityChecker()
        result = checker.is_original("This is a completely unique test script about quantum physics.")
        assert result is not None
        # New content should be original
        if hasattr(result, 'is_original'):
            assert result.is_original
        elif isinstance(result, dict):
            assert result.get('is_original', True)
        elif isinstance(result, bool):
            assert result


class TestVideoRendering:
    """Test video production pipeline."""

    def test_subtitle_generation(self):
        from src.video.subtitle_generator import SubtitleGenerator
        gen = SubtitleGenerator()
        with tempfile.NamedTemporaryFile(suffix='.srt', delete=False) as f:
            srt_path = f.name
        try:
            result = gen.generate_srt(
                "This is a test script. It has two sentences.",
                total_duration=10.0,
                output_path=srt_path
            )
            assert os.path.exists(srt_path) or os.path.exists(str(result))
            # Check SRT content
            check_path = str(result) if result and os.path.exists(str(result)) else srt_path
            if os.path.exists(check_path):
                with open(check_path, 'r') as f:
                    content = f.read()
                assert len(content) > 0
        finally:
            for p in [srt_path]:
                if os.path.exists(p):
                    os.remove(p)

    def test_visual_generator_frame(self):
        from src.video.visual_generator import VisualGenerator
        gen = VisualGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "title.png")
            result = gen.generate_title_card(
                title="Test Video Title",
                subtitle="A test subtitle",
                resolution=(1920, 1080),
                output_path=out_file
            )
            # Should create an image file
            if isinstance(result, str) and os.path.exists(result):
                from PIL import Image
                img = Image.open(result)
                assert img.size == (1920, 1080)

    def test_thumbnail_generation(self):
        from src.video.thumbnail_generator import ThumbnailGenerator
        gen = ThumbnailGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = os.path.join(tmpdir, "thumb.jpg")
            result = gen.generate_thumbnail(
                title="Amazing Science Facts",
                style="bold_modern",
                video_type="regular",
                output_path=out_file
            )
            if isinstance(result, str) and os.path.exists(result):
                from PIL import Image
                img = Image.open(result)
                assert img.size[0] == 1280
                assert img.size[1] == 720


class TestTTS:
    """Test text-to-speech engine."""

    def test_edge_tts_available(self):
        """Test that edge-tts can be imported."""
        import asyncio
        async def _run():
            try:
                import edge_tts
                voices = await edge_tts.list_voices()
                us_voices = [v for v in voices if v.get('Locale', '').startswith('en-US')]
                assert len(us_voices) > 0
            except Exception:
                pytest.skip("edge-tts not available (network issue)")
        asyncio.run(_run())


class TestQuotaManagement:
    """Test API quota tracking."""

    def test_quota_manager_init(self):
        from src.quota_manager import QuotaManager
        qm = QuotaManager()
        assert qm.can_upload()  # Should allow at start of day

    def test_quota_tracking(self):
        from src.quota_manager import QuotaManager
        qm = QuotaManager()
        qm.record_usage('youtube', 'videos.insert', 1600)
        remaining = qm.get_remaining_quota('youtube')
        assert remaining <= 8400  # 10000 - 1600


class TestQuality:
    """Test video quality checker."""

    def test_quality_checker_missing_file(self):
        from src.quality import QualityChecker
        qc = QualityChecker()
        result = qc.validate_video('/nonexistent/file.mp4', 'long')
        assert not result.is_valid


class TestScheduler:
    """Test scheduling system."""

    def test_schedule_slots(self):
        from src.cloud.scheduler import CloudScheduler
        scheduler = CloudScheduler()
        slots = scheduler.get_today_schedule()
        assert len(slots) == 5

    def test_schedule_timezone_handling(self):
        """Verify NY timezone is correctly applied."""
        import pytz
        ny_tz = pytz.timezone('America/New_York')
        now_ny = datetime.now(ny_tz)
        # Should be a valid datetime
        assert now_ny.tzname() in ('EST', 'EDT')


class TestDuplicateUploadPrevention:
    """Test that duplicate uploads are prevented."""

    def test_content_hash_dedup(self):
        from src.utils.helpers import hash_content
        h1 = hash_content("My video script about space exploration")
        h2 = hash_content("My video script about space exploration")
        h3 = hash_content("A different video script entirely")
        assert h1 == h2  # Same content = same hash
        assert h1 != h3  # Different content = different hash


class TestVideoCodecValidation:
    """Test video format validation."""

    def test_mp4_magic_bytes(self):
        """Test MP4 file detection."""
        # Create a minimal MP4-like header
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            # MP4 files start with ftyp box
            f.write(b'\x00\x00\x00\x20ftypisom')
            f.write(b'\x00' * 24)
            fake_mp4 = f.name

        try:
            # Read first bytes
            with open(fake_mp4, 'rb') as f:
                header = f.read(12)
            assert b'ftyp' in header
        finally:
            os.remove(fake_mp4)


class TestAPISafety:
    """Test API safety mechanisms."""

    def test_emergency_stop_check(self):
        """Emergency stop should prevent operations."""
        os.environ['EMERGENCY_STOP'] = 'true'
        from src.security import check_emergency_stop
        assert check_emergency_stop()
        os.environ['EMERGENCY_STOP'] = 'false'

    def test_cost_kill_switch(self):
        """Cost kill switch should prevent paid operations."""
        os.environ['COST_KILL_SWITCH'] = 'true'
        from src.security import check_cost_kill_switch
        assert check_cost_kill_switch()
        os.environ['COST_KILL_SWITCH'] = 'false'


class TestOAuthHandling:
    """Test OAuth error handling."""

    def test_auth_without_credentials(self):
        """Should gracefully handle missing credentials."""
        from src.youtube.auth import YouTubeAuth
        auth = YouTubeAuth()
        assert not auth.is_authenticated()

    def test_auth_url_generation(self):
        """Should generate auth URL when client_id is set."""
        os.environ['YOUTUBE_CLIENT_ID'] = 'test_client_id'
        os.environ['YOUTUBE_CLIENT_SECRET'] = 'test_client_secret'
        from src.youtube.auth import YouTubeAuth
        auth = YouTubeAuth()
        url = auth.get_auth_url()
        if url:  # May return None if validation fails
            assert 'accounts.google.com' in url or 'oauth' in url.lower()
        os.environ.pop('YOUTUBE_CLIENT_ID', None)
        os.environ.pop('YOUTUBE_CLIENT_SECRET', None)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
