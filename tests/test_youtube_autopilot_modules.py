"""
Comprehensive Unit Tests for YouTube AI Autopilot Core Modules.

Tests:
  - ThumbnailGenerator (Styles, dimensions, typography, Arabic/English text)
  - YouTubeAuth (Token encryption, SQLite persistence, OAuth flow, Arabic errors)
  - YouTubeUploader (Quota tracking, duplicate prevention, private defaults, validation)
  - MetadataGenerator (Title/description/tags SEO optimization, category mapping, Shorts)
"""

from __future__ import annotations

import datetime
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from src.video.thumbnail_generator import ThumbnailGenerator
from src.youtube.auth import YouTubeAuth, TokenEncryption, ARABIC_MESSAGES
from src.youtube.metadata_generator import MetadataGenerator, VideoMetadata
from src.youtube.uploader import YouTubeUploader, UploadResult, QuotaStatus, UPLOADER_MESSAGES_AR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Fixture providing a temporary SQLite database path."""
    return tmp_path / "test_autopilot.db"


@pytest.fixture
def sample_video_file(tmp_path: Path) -> Path:
    """Fixture providing a temporary mock MP4 video file."""
    video_file = tmp_path / "mock_video.mp4"
    video_file.write_bytes(b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"X" * 1024)
    return video_file


@pytest.fixture
def sample_thumbnail_file(tmp_path: Path) -> Path:
    """Fixture providing a temporary mock JPEG thumbnail file."""
    thumb_file = tmp_path / "mock_thumbnail.jpg"
    img = Image.new("RGB", (1280, 720), color=(20, 30, 40))
    img.save(thumb_file, "JPEG")
    return thumb_file


# ---------------------------------------------------------------------------
# Thumbnail Generator Tests
# ---------------------------------------------------------------------------

class TestThumbnailGenerator:
    """Test suite for ThumbnailGenerator."""

    def test_regular_thumbnail_generation(self, tmp_path: Path) -> None:
        """Test generation of regular 1280x720 thumbnail."""
        gen = ThumbnailGenerator(output_dir=tmp_path)
        out_path = gen.generate_thumbnail(
            title="The Future of Quantum Computing in 2026",
            style="bold_modern",
            video_type="regular",
            category="science",
            subtitle="Deep Dive into Quantum Mechanics",
        )
        assert os.path.exists(out_path)
        with Image.open(out_path) as img:
            assert img.size == (1280, 720)

    def test_shorts_thumbnail_generation(self, tmp_path: Path) -> None:
        """Test generation of vertical 1080x1920 Shorts thumbnail."""
        gen = ThumbnailGenerator(output_dir=tmp_path)
        out_path = gen.generate_thumbnail(
            title="3 Crazy Python Tricks You Didn't Know!",
            style="cyber_glow",
            video_type="shorts",
            category="technology",
            subtitle="Level up your code",
        )
        assert os.path.exists(out_path)
        with Image.open(out_path) as img:
            assert img.size == (1080, 1920)

    @pytest.mark.parametrize("style", [
        "bold_modern",
        "cyber_glow",
        "tech_minimal",
        "breaking_bold",
        "education_pro",
        "cosmic_radial",
    ])
    def test_all_design_templates(self, tmp_path: Path, style: str) -> None:
        """Test that all 6 templates execute without error."""
        gen = ThumbnailGenerator(output_dir=tmp_path)
        out_path = gen.generate_thumbnail(
            title=f"Testing Template {style.title()}",
            style=style,
            video_type="regular",
            category="education",
        )
        assert os.path.exists(out_path)

    def test_arabic_title_support(self, tmp_path: Path) -> None:
        """Test thumbnail generation with Arabic typography."""
        gen = ThumbnailGenerator(output_dir=tmp_path)
        out_path = gen.generate_thumbnail(
            title="أحدث تطورات الذكاء الاصطناعي في عام 2026",
            style="education_pro",
            video_type="regular",
            category="technology",
            subtitle="دليل شامل للمطورين",
        )
        assert os.path.exists(out_path)
        with Image.open(out_path) as img:
            assert img.size == (1280, 720)


# ---------------------------------------------------------------------------
# YouTube Auth Tests
# ---------------------------------------------------------------------------

class TestYouTubeAuth:
    """Test suite for YouTubeAuth and token encryption."""

    def test_token_encryption_roundtrip(self, tmp_path: Path) -> None:
        """Test that TokenEncryption accurately encrypts and decrypts secrets."""
        enc = TokenEncryption(data_dir=tmp_path)
        plain = "ya29.sample_sensitive_access_token_super_secret"
        cipher = enc.encrypt(plain)
        assert cipher != plain
        decrypted = enc.decrypt(cipher)
        assert decrypted == plain

    def test_auth_url_generation(self, temp_db_path: Path) -> None:
        """Test generating OAuth consent URL."""
        auth = YouTubeAuth(
            client_id="dummy-client-id.apps.googleusercontent.com",
            client_secret="dummy-client-secret",
            db_path=temp_db_path,
        )
        url = auth.get_auth_url(state="test_state_123")
        assert "accounts.google.com" in url
        assert "dummy-client-id" in url
        assert "test_state_123" in url
        assert "access_type=offline" in url

    def test_database_token_persistence(self, temp_db_path: Path) -> None:
        """Test saving and retrieving encrypted token credentials."""
        auth = YouTubeAuth(
            client_id="dummy-client-id.apps.googleusercontent.com",
            client_secret="dummy-client-secret",
            db_path=temp_db_path,
        )

        future_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
        saved = auth._save_token(
            access_token="test_access_token_999",
            refresh_token="test_refresh_token_888",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="dummy-client-id.apps.googleusercontent.com",
            client_secret="dummy-client-secret",
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
            expiry=future_expiry,
            channel_id="UC_TEST_CHANNEL_1",
            channel_title="Autopilot Test Channel",
        )
        assert saved is True

        creds = auth.get_credentials()
        assert creds is not None
        assert creds.token == "test_access_token_999"
        assert creds.refresh_token == "test_refresh_token_888"

        # Test revocation / cleanup
        deleted = auth._delete_token()
        assert deleted is True
        assert auth._load_token() is None

    def test_arabic_error_messages(self, temp_db_path: Path) -> None:
        """Test Arabic error messaging when unauthenticated."""
        auth = YouTubeAuth(db_path=temp_db_path)
        creds = auth.get_credentials()
        assert creds is None
        err_ar = auth.get_last_error("ar")
        assert err_ar == ARABIC_MESSAGES["no_credentials"]


# ---------------------------------------------------------------------------
# Metadata Generator Tests
# ---------------------------------------------------------------------------

class TestMetadataGenerator:
    """Test suite for MetadataGenerator."""

    def test_regular_metadata_generation(self) -> None:
        """Test regular video metadata creation and validation."""
        mg = MetadataGenerator(channel_name="AI Autopilot")
        meta = mg.generate_metadata(
            script_data={
                "title": "Machine Learning in 2026: Key Breakthroughs",
                "summary": "Comprehensive overview of new machine learning advancements.",
                "keywords": ["Machine Learning", "AI", "Neural Networks", "Deep Learning"],
                "chapters": [
                    {"timestamp": "00:00", "title": "Intro"},
                    {"timestamp": "02:00", "title": "Architectures"},
                    {"timestamp": "06:00", "title": "Conclusion"},
                ],
                "category": "science_technology",
            },
            video_type="regular",
        )

        assert meta.title == "Machine Learning in 2026: Key Breakthroughs"
        assert meta.category_id == "28"
        assert "00:00 - Intro" in meta.description
        assert "AI Content Disclosure" in meta.description
        assert len(meta.tags) > 0
        assert sum(len(t) for t in meta.tags) + len(meta.tags) - 1 <= 500

        is_valid, errors = meta.validate()
        assert is_valid is True
        assert len(errors) == 0

    def test_shorts_metadata_formatting(self) -> None:
        """Test that Shorts metadata includes #Shorts and proper formatting."""
        mg = MetadataGenerator()
        meta = mg.generate_metadata(
            script_data={
                "title": "Quick Python One-Liner",
                "summary": "Speed up your data manipulation.",
                "keywords": ["Python", "Pandas"],
            },
            video_type="shorts",
        )

        assert "#Shorts" in meta.title
        assert "#Shorts" in meta.description
        assert "Shorts" in meta.tags

    def test_title_truncation_under_100_chars(self) -> None:
        """Test title length limitation to 100 characters."""
        mg = MetadataGenerator()
        long_title = "A" * 150
        meta = mg.generate_metadata(script_data={"title": long_title}, video_type="regular")
        assert len(meta.title) <= 100
        assert meta.title.endswith("...")

    def test_category_mapping(self) -> None:
        """Test category resolution to YouTube API category IDs."""
        assert MetadataGenerator.resolve_category_id("technology") == "28"
        assert MetadataGenerator.resolve_category_id("education") == "27"
        assert MetadataGenerator.resolve_category_id("gaming") == "20"
        assert MetadataGenerator.resolve_category_id("howto") == "26"


# ---------------------------------------------------------------------------
# YouTube Uploader Tests
# ---------------------------------------------------------------------------

class TestYouTubeUploader:
    """Test suite for YouTubeUploader."""

    def test_quota_tracking(self, temp_db_path: Path) -> None:
        """Test daily API quota calculation and recording."""
        uploader = YouTubeUploader(db_path=temp_db_path, daily_quota_limit=10000)
        status = uploader.check_upload_quota()
        assert status.used_today == 0
        assert status.remaining == 10000
        assert status.can_upload is True

        # Simulate upload consumption
        uploader._record_quota_consumption(1600, "video_upload", "v123")
        status2 = uploader.check_upload_quota()
        assert status2.used_today == 1600
        assert status2.remaining == 8400
        assert status2.can_upload is True

    def test_duplicate_prevention(self, temp_db_path: Path, sample_video_file: Path) -> None:
        """Test detecting and aborting duplicate video uploads."""
        uploader = YouTubeUploader(db_path=temp_db_path)
        file_hash = uploader.calculate_file_hash(sample_video_file)

        # Record in history
        uploader._record_uploaded_video(
            video_id="v_existing_456",
            title="Unique AI Tutorial",
            file_hash=file_hash,
            file_path=str(sample_video_file),
            file_size=sample_video_file.stat().st_size,
            privacy_status="private",
            video_type="regular",
            category_id="28",
            scheduled_time=None,
            quota_used=1600,
        )

        # Check duplicate directly
        dup = uploader.check_duplicate(file_hash, "Unique AI Tutorial")
        assert dup is not None
        assert dup["video_id"] == "v_existing_456"

        # Attempt upload_video and verify rejection
        meta = VideoMetadata(title="Unique AI Tutorial", description="Duplicate check")
        result = uploader.upload_video(sample_video_file, meta)
        assert result.success is False
        assert result.status == "duplicate"
        assert result.video_id == "v_existing_456"
        assert result.error_message_ar == UPLOADER_MESSAGES_AR["duplicate_detected"]

    def test_privacy_enforcement_downgrade(self, temp_db_path: Path, sample_video_file: Path) -> None:
        """Test that public uploads without allow_public=True are downgraded to private."""
        uploader = YouTubeUploader(db_path=temp_db_path)
        meta = VideoMetadata(title="Secret Video", description="Testing privacy", privacy_status="public")

        # Because unauthenticated, upload_video should halt at authentication,
        # but the privacy status was evaluated and downgraded.
        result = uploader.upload_video(sample_video_file, meta, allow_public=False)
        assert meta.privacy_status == "private"
        assert result.success is False

    def test_missing_video_file_handling(self, temp_db_path: Path) -> None:
        """Test graceful failure when video file does not exist."""
        uploader = YouTubeUploader(db_path=temp_db_path)
        meta = VideoMetadata(title="Missing", description="File does not exist")
        result = uploader.upload_video("non_existent_file.mp4", meta)
        assert result.success is False
        assert result.error_message_ar == UPLOADER_MESSAGES_AR["file_not_found"]
