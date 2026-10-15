"""Configuration management module for YouTube AI Autopilot.

Loads configuration from environment variables, provides strongly-typed settings,
validates required parameters, handles timezone localization, and manages environment
profiles (development, production, testing).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import pytz

# Load environment variables from .env file if present
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()


def _get_bool(key: str, default: bool = False) -> bool:
    """Parse a boolean value from environment variables.

    Args:
        key: Environment variable name.
        default: Default boolean value if key is not found.

    Returns:
        bool: Parsed boolean representation.
    """
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "y", "on")


def _get_int(key: str, default: int) -> int:
    """Parse an integer value from environment variables.

    Args:
        key: Environment variable name.
        default: Default integer value.

    Returns:
        int: Parsed integer or default on error.
    """
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    """Parse a float value from environment variables.

    Args:
        key: Environment variable name.
        default: Default float value.

    Returns:
        float: Parsed float or default on error.
    """
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


class Config:
    """Base application configuration class with sensible defaults."""

    # Project Paths
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    LOGS_DIR: Path = BASE_DIR / "data" / "logs"
    ASSETS_DIR: Path = BASE_DIR / "assets"
    TEMP_DIR: Path = BASE_DIR / "data" / "temp"
    OUTPUT_DIR: Path = BASE_DIR / "data" / "output"

    # Application Environment
    APP_ENV: str = os.getenv("APP_ENV", "production").lower()
    APP_DEBUG: bool = _get_bool("APP_DEBUG", default=False)
    TESTING: bool = False

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'autopilot.db'}")
    DB_ECHO: bool = _get_bool("DB_ECHO", default=False)

    # Timezone Handling (America/New_York)
    TIMEZONE_STR: str = os.getenv("TIMEZONE", "America/New_York")
    TIMEZONE: str = os.getenv("TIMEZONE", "America/New_York")

    # YouTube API / OAuth 2.0 Credentials
    YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
    YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    YOUTUBE_REDIRECT_URI: str = os.getenv("YOUTUBE_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    YOUTUBE_API_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    # Gemini AI
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    # Dashboard & Admin Authentication
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    DASHBOARD_SECRET_KEY: str = os.getenv("DASHBOARD_SECRET_KEY", "change-me-in-production-dashboard-secret")
    SESSION_COOKIE_NAME: str = "yt_autopilot_session"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SECURE: bool = False  # Set True in production over HTTPS
    SESSION_LIFETIME_SECONDS: int = 86400  # 24 hours

    # Content Settings
    CHANNEL_NICHE: str = os.getenv("CHANNEL_NICHE", "science_technology_education")
    CONTENT_LANGUAGE: str = os.getenv("CONTENT_LANGUAGE", "en-US")
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-GuyNeural")
    SHORTS_PER_DAY: int = _get_int("SHORTS_PER_DAY", default=4)
    LONG_VIDEOS_PER_DAY: int = _get_int("LONG_VIDEOS_PER_DAY", default=1)
    DAILY_SCHEDULE_HOURS: List[int] = [9, 12, 15, 18, 21]

    # GitHub Actions / Automation
    GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # Notification (SMTP)
    NOTIFICATION_EMAIL: str = os.getenv("NOTIFICATION_EMAIL", "")
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "")
    SMTP_PORT: int = _get_int("SMTP_PORT", default=587)
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

    # Security Settings & Kill Switches
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    EMERGENCY_STOP: bool = _get_bool("EMERGENCY_STOP", default=False)
    COST_KILL_SWITCH: bool = _get_bool("COST_KILL_SWITCH", default=False)
    MAX_DAILY_API_COST_USD: float = _get_float("MAX_DAILY_API_COST_USD", default=0.00)
    COST_ALERT_THRESHOLD_PCT: float = 0.80

    # API Quota Limits
    YOUTUBE_DAILY_QUOTA_LIMIT: int = _get_int("YOUTUBE_DAILY_QUOTA_LIMIT", default=10000)
    GEMINI_DAILY_REQUEST_LIMIT: int = _get_int("GEMINI_DAILY_REQUEST_LIMIT", default=1500)
    GEMINI_RPM_LIMIT: int = _get_int("GEMINI_RPM_LIMIT", default=15)
    TTS_DAILY_CHAR_LIMIT: int = _get_int("TTS_DAILY_CHAR_LIMIT", default=500000)

    # Rate Limiting Settings
    DEFAULT_RATE_LIMIT_REQUESTS: int = 60
    DEFAULT_RATE_LIMIT_WINDOW_SECONDS: int = 60

    @property
    def timezone(self) -> pytz.BaseTzInfo:
        """Get the configured pytz timezone object for America/New_York or configured zone.

        Returns:
            pytz.BaseTzInfo: The pytz timezone instance.
        """
        try:
            return pytz.timezone(self.TIMEZONE_STR)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone("America/New_York")

    @classmethod
    def ensure_directories(cls) -> None:
        """Ensure all required runtime directories exist."""
        for path in (cls.DATA_DIR, cls.LOGS_DIR, cls.ASSETS_DIR, cls.TEMP_DIR, cls.OUTPUT_DIR):
            path.mkdir(parents=True, exist_ok=True)

    def validate(self) -> List[str]:
        """Validate critical configuration fields and return any issues found.

        Returns:
            List[str]: List of validation warning or error messages.
        """
        issues: List[str] = []

        # Validate Timezone
        try:
            pytz.timezone(self.TIMEZONE_STR)
        except pytz.UnknownTimeZoneError:
            issues.append(f"Invalid TIMEZONE: {self.TIMEZONE_STR}. Fallback to America/New_York.")

        # Database URL
        if not self.DATABASE_URL:
            issues.append("DATABASE_URL is not set.")

        # Emergency stop warning
        if self.EMERGENCY_STOP:
            issues.append("EMERGENCY_STOP is active! All production and publishing tasks will be halted.")

        # Cost kill switch warning
        if self.COST_KILL_SWITCH:
            issues.append("COST_KILL_SWITCH is active! Paid API calls will be blocked.")

        return issues

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """Convert configuration settings to a dictionary for inspection.

        Args:
            mask_secrets: Whether to redact sensitive API keys and tokens.

        Returns:
            Dict[str, Any]: Dictionary representation of configuration.
        """
        secret_keys = {
            "YOUTUBE_CLIENT_SECRET", "GEMINI_API_KEY", "ADMIN_PASSWORD_HASH",
            "DASHBOARD_SECRET_KEY", "GITHUB_TOKEN", "SMTP_PASSWORD", "ENCRYPTION_KEY"
        }
        res: Dict[str, Any] = {}
        for key in dir(self):
            if key.isupper() and not key.startswith("_"):
                val = getattr(self, key)
                if mask_secrets and key in secret_keys and val:
                    res[key] = "********"
                else:
                    res[key] = str(val) if isinstance(val, Path) else val
        return res


class DevelopmentConfig(Config):
    """Configuration profile for local development."""

    APP_ENV = "development"
    APP_DEBUG = True
    DB_ECHO = False
    SESSION_COOKIE_SECURE = False

    def validate(self) -> List[str]:
        """Validate development settings with relaxed constraints."""
        issues = super().validate()
        if not self.ENCRYPTION_KEY:
            issues.append("Development notice: ENCRYPTION_KEY is empty. Using transient key.")
        return issues


class TestingConfig(Config):
    """Configuration profile for automated tests."""

    APP_ENV = "testing"
    APP_DEBUG = True
    TESTING = True
    DATABASE_URL = "sqlite:///:memory:"
    DB_ECHO = False
    SESSION_COOKIE_SECURE = False
    MAX_DAILY_API_COST_USD = 0.00
    ENCRYPTION_KEY = "dGVzdC1lbmNyeXB0aW9uLWtleS1mb3ItdW5pdC10ZXN0cw=="

    def validate(self) -> List[str]:
        """Validate testing settings."""
        return []


class ProductionConfig(Config):
    """Configuration profile for live production deployment."""

    APP_ENV = "production"
    APP_DEBUG = False
    SESSION_COOKIE_SECURE = True

    def validate(self) -> List[str]:
        """Validate production configuration with strict checks.

        Returns:
            List[str]: List of validation errors.
        """
        issues = super().validate()

        if self.DASHBOARD_SECRET_KEY in ("", "change-me-in-production-dashboard-secret"):
            issues.append("CRITICAL: DASHBOARD_SECRET_KEY is insecure or unset in production.")

        if not self.ENCRYPTION_KEY:
            issues.append("CRITICAL: ENCRYPTION_KEY must be configured in production for token security.")

        if not self.ADMIN_PASSWORD_HASH:
            issues.append("WARNING: ADMIN_PASSWORD_HASH is not set. Dashboard admin login will fail.")

        if not self.YOUTUBE_CLIENT_ID or not self.YOUTUBE_CLIENT_SECRET:
            issues.append("WARNING: YouTube OAuth credentials missing. YouTube API operations will fail.")

        return issues


# Configuration mapping lookup
CONFIG_MAP: Dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(env_name: Optional[str] = None) -> Config:
    """Configuration factory returning an instantiated configuration profile.

    Args:
        env_name: Optional environment name ('development', 'testing', 'production').
                  Defaults to APP_ENV environment variable or 'production'.

    Returns:
        Config: Instantiated configuration object.
    """
    if env_name is None:
        env_name = os.getenv("APP_ENV", "production").lower()

    config_class = CONFIG_MAP.get(env_name, ProductionConfig)
    instance = config_class()
    instance.ensure_directories()
    return instance


# Global active configuration instance
current_config: Config = get_config()
