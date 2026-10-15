"""
YouTube OAuth 2.0 Authentication and Token Management.

Provides secure OAuth 2.0 authorization flow, token persistence using SQLite
with AES-based Fernet encryption, automatic background token refresh, credential
validation, channel profile queries, and token revocation.
All user-facing errors include clear Arabic translations for the dashboard.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Arabic Error Messages for Dashboard UI
# ---------------------------------------------------------------------------

ARABIC_MESSAGES: Dict[str, str] = {
    "auth_success": "تم ربط وتفويض حساب YouTube بنجاح.",
    "auth_revoked": "تم إلغاء تفويض حساب YouTube وحذف الرموز المشفرة بنجاح.",
    "no_credentials": "لم يتم العثور على بيانات اعتماد YouTube صالحة. يرجى تسجيل الدخول وربط قناتك أولاً.",
    "expired_credentials": "انتهت صلاحية رمز الوصول، وجارٍ محاولة التجديد التلقائي.",
    "refresh_failed": "فشل تجديد رمز الوصول لحساب YouTube. قد يكون الرمز ملغياً، يرجى إعادة تسجيل الدخول.",
    "missing_client_secrets": "بيانات الاعتماد YOUTUBE_CLIENT_ID أو YOUTUBE_CLIENT_SECRET مفقودة في ملف الإعدادات (.env).",
    "network_error": "تعذر الاتصال بخوادم Google OAuth. يرجى التحقق من اتصال الشبكة.",
    "invalid_code": "رمز التفويض المستلم غير صالح أو منتهي الصلاحية.",
    "channel_info_failed": "تعذر استرداد بيانات القناة من YouTube API.",
    "db_error": "حدث خطأ أثناء حفظ أو استرجاع بيانات الاعتماد من قاعدة البيانات المشفرة.",
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SecureToken:
    """Represents an OAuth 2.0 credential record with encryption status."""
    service_name: str
    access_token: str
    refresh_token: Optional[str]
    token_uri: str
    client_id: str
    client_secret: str
    scopes: List[str]
    expiry: Optional[datetime.datetime]
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Token Encryption Helper
# ---------------------------------------------------------------------------

class TokenEncryption:
    """Manages AES encryption and decryption of sensitive OAuth tokens using Fernet."""

    def __init__(self, key_str: Optional[str] = None, data_dir: Optional[Path] = None) -> None:
        """
        Initialize TokenEncryption with an explicit key or resolve from environment/storage.

        Args:
            key_str: Optional 32-byte or passphrase key.
            data_dir: Path to directory where key file can be persisted if none provided.
        """
        self.data_dir = data_dir or Path("data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw_key = key_str or os.getenv("ENCRYPTION_KEY")

        if not raw_key:
            raw_key = self._get_or_create_key_file()

        fernet_key = self._derive_fernet_key(raw_key)
        self._fernet = Fernet(fernet_key)

    def _get_or_create_key_file(self) -> str:
        """Read an existing local key file or create a persistent cryptographically secure key."""
        key_file = self.data_dir / ".encryption_key"
        if key_file.exists():
            try:
                content = key_file.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception as e:
                logger.warning("Could not read key file %s: %s", key_file, e)

        # Generate a new random urlsafe base64 key
        new_key = Fernet.generate_key().decode("utf-8")
        try:
            key_file.write_text(new_key, encoding="utf-8")
            os.chmod(key_file, 0o600)  # Restrict to owner read/write
            logger.info("Generated new persistent encryption key in %s", key_file)
        except Exception as e:
            logger.warning("Could not persist generated key file %s: %s", key_file, e)
        return new_key

    @staticmethod
    def _derive_fernet_key(passphrase: str) -> bytes:
        """
        Derive a valid 32-byte URL-safe base64-encoded Fernet key from any passphrase or key.
        """
        try:
            # Check if it's already a valid 32-byte Fernet key
            decoded = base64.urlsafe_b64decode(passphrase.encode("utf-8"))
            if len(decoded) == 32:
                return passphrase.encode("utf-8")
        except Exception:
            pass

        # Hash passphrase using SHA-256 to ensure exact 32-byte length
        h = hashlib.sha256(passphrase.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(h)

    def encrypt(self, plain_text: Optional[str]) -> Optional[str]:
        """Encrypt a string and return URL-safe base64 ciphertext."""
        if plain_text is None:
            return None
        return self._fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: Optional[str]) -> Optional[str]:
        """Decrypt URL-safe base64 ciphertext back to plaintext."""
        if cipher_text is None:
            return None
        return self._fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")


# ---------------------------------------------------------------------------
# YouTube Authentication Manager
# ---------------------------------------------------------------------------

class YouTubeAuth:
    """
    Production-grade YouTube OAuth 2.0 authentication and token manager.

    Features:
      - Complete OAuth 2.0 flow with offline access for long-term autopilot operation.
      - Scopes: youtube.upload, youtube.readonly, youtube-analytics.readonly.
      - Encrypted database storage for access and refresh tokens.
      - Automatic background credential refresh when expired.
      - Complete credential validation before executing API tasks.
      - Token revocation and database cleanup.
      - Channel profile retrieval with Arabic status reporting.
    """

    DEFAULT_SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    SERVICE_NAME = "youtube_v3"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        db_path: Optional[Union[str, Path]] = None,
        encryption_key: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize the YouTubeAuth manager.

        Args:
            client_id: YouTube OAuth Client ID (falls back to YOUTUBE_CLIENT_ID env).
            client_secret: YouTube OAuth Client Secret (falls back to YOUTUBE_CLIENT_SECRET env).
            redirect_uri: OAuth callback URI (falls back to YOUTUBE_REDIRECT_URI env).
            db_path: Path to SQLite database file. Defaults to 'data/autopilot.db'.
            encryption_key: Key for encrypting tokens.
            scopes: List of OAuth scopes. Defaults to upload, readonly, analytics.
        """
        self.client_id = client_id or os.getenv("YOUTUBE_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("YOUTUBE_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.getenv(
            "YOUTUBE_REDIRECT_URI", "http://localhost:5000/oauth/callback"
        )
        self.scopes = scopes or self.DEFAULT_SCOPES

        # Resolve Database path from parameter or DATABASE_URL
        if db_path is not None:
            self.db_path = Path(db_path).resolve()
        else:
            db_url = os.getenv("DATABASE_URL", "sqlite:///data/autopilot.db")
            if db_url.startswith("sqlite:///"):
                db_url = db_url.replace("sqlite:///", "", 1)
            self.db_path = Path(db_url).resolve()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize encryption
        self._encryption = TokenEncryption(
            key_str=encryption_key,
            data_dir=self.db_path.parent,
        )

        # Cached in-memory credentials
        self._credentials: Optional[Credentials] = None

        # Last error tracking
        self.last_error: Optional[str] = None
        self.last_error_ar: Optional[str] = None

        # Ensure database table exists
        self._init_db()

    # -----------------------------------------------------------------------
    # Database Initialization & Operations
    # -----------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Create and return a configured SQLite connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the youtube_oauth_tokens table in SQLite if it does not already exist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS youtube_oauth_tokens (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        service_name TEXT NOT NULL UNIQUE,
                        access_token_enc TEXT NOT NULL,
                        refresh_token_enc TEXT,
                        token_uri_enc TEXT NOT NULL,
                        client_id_enc TEXT NOT NULL,
                        client_secret_enc TEXT NOT NULL,
                        scopes TEXT NOT NULL,
                        expiry TEXT,
                        channel_id TEXT,
                        channel_title TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to initialize database table youtube_oauth_tokens: %s", e)
            self._set_error("Database initialization error", ARABIC_MESSAGES["db_error"])

    def _save_token(
        self,
        access_token: str,
        refresh_token: Optional[str],
        token_uri: str,
        client_id: str,
        client_secret: str,
        scopes: List[str],
        expiry: Optional[datetime.datetime],
        channel_id: Optional[str] = None,
        channel_title: Optional[str] = None,
    ) -> bool:
        """Encrypt and persist token fields to SQLite."""
        try:
            enc_access = self._encryption.encrypt(access_token)
            enc_refresh = self._encryption.encrypt(refresh_token) if refresh_token else None
            enc_uri = self._encryption.encrypt(token_uri)
            enc_cid = self._encryption.encrypt(client_id)
            enc_sec = self._encryption.encrypt(client_secret)
            scopes_json = json.dumps(scopes)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            expiry_iso = expiry.isoformat() if expiry else None

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO youtube_oauth_tokens (
                        service_name, access_token_enc, refresh_token_enc, token_uri_enc,
                        client_id_enc, client_secret_enc, scopes, expiry, channel_id, channel_title,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(service_name) DO UPDATE SET
                        access_token_enc=excluded.access_token_enc,
                        refresh_token_enc=COALESCE(excluded.refresh_token_enc, youtube_oauth_tokens.refresh_token_enc),
                        token_uri_enc=excluded.token_uri_enc,
                        client_id_enc=excluded.client_id_enc,
                        client_secret_enc=excluded.client_secret_enc,
                        scopes=excluded.scopes,
                        expiry=excluded.expiry,
                        channel_id=COALESCE(excluded.channel_id, youtube_oauth_tokens.channel_id),
                        channel_title=COALESCE(excluded.channel_title, youtube_oauth_tokens.channel_title),
                        updated_at=excluded.updated_at
                    """,
                    (
                        self.SERVICE_NAME,
                        enc_access,
                        enc_refresh,
                        enc_uri,
                        enc_cid,
                        enc_sec,
                        scopes_json,
                        expiry_iso,
                        channel_id,
                        channel_title,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
            logger.info("Successfully persisted encrypted tokens for service: %s", self.SERVICE_NAME)
            return True
        except Exception as e:
            logger.error("Failed to persist encrypted token to SQLite: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["db_error"])
            return False

    def _load_token(self) -> Optional[SecureToken]:
        """Load and decrypt token record from SQLite."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT service_name, access_token_enc, refresh_token_enc, token_uri_enc,
                           client_id_enc, client_secret_enc, scopes, expiry,
                           channel_id, channel_title, created_at, updated_at
                    FROM youtube_oauth_tokens
                    WHERE service_name = ?
                    """,
                    (self.SERVICE_NAME,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                access_token = self._encryption.decrypt(row["access_token_enc"])
                refresh_token = self._encryption.decrypt(row["refresh_token_enc"]) if row["refresh_token_enc"] else None
                token_uri = self._encryption.decrypt(row["token_uri_enc"])
                client_id = self._encryption.decrypt(row["client_id_enc"])
                client_secret = self._encryption.decrypt(row["client_secret_enc"])
                scopes = json.loads(row["scopes"])

                expiry = None
                if row["expiry"]:
                    try:
                        expiry = datetime.datetime.fromisoformat(row["expiry"])
                    except Exception:
                        expiry = None

                return SecureToken(
                    service_name=row["service_name"],
                    access_token=access_token or "",
                    refresh_token=refresh_token,
                    token_uri=token_uri or "https://oauth2.googleapis.com/token",
                    client_id=client_id or "",
                    client_secret=client_secret or "",
                    scopes=scopes,
                    expiry=expiry,
                    channel_id=row["channel_id"],
                    channel_title=row["channel_title"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
        except Exception as e:
            logger.error("Failed to load or decrypt token from SQLite: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["db_error"])
            return None

    def _delete_token(self) -> bool:
        """Remove the token record from SQLite upon revocation or logout."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM youtube_oauth_tokens WHERE service_name = ?", (self.SERVICE_NAME,))
                conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to delete token from SQLite: %s", e)
            return False

    # -----------------------------------------------------------------------
    # OAuth 2.0 Flow Interface
    # -----------------------------------------------------------------------

    def _get_client_config(self) -> Dict[str, Any]:
        """Build the client configuration dictionary required by google-auth-oauthlib."""
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET must be configured. "
                + ARABIC_MESSAGES["missing_client_secrets"]
            )
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri],
            }
        }

    def get_auth_url(self, state: Optional[str] = None) -> str:
        """
        Generate the Google OAuth 2.0 authorization URL for user consent.

        Args:
            state: Optional CSRF anti-forgery state token.

        Returns:
            The URL string to redirect the user to.
        """
        try:
            client_config = self._get_client_config()
            flow = Flow.from_client_config(
                client_config=client_config,
                scopes=self.scopes,
                state=state,
            )
            flow.redirect_uri = self.redirect_uri
            auth_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",  # Force consent to ensure refresh_token is always returned
            )
            logger.info("Generated OAuth authorization URL successfully")
            return auth_url
        except ValueError as ve:
            self._set_error(str(ve), ARABIC_MESSAGES["missing_client_secrets"])
            raise
        except Exception as e:
            self._set_error(str(e), ARABIC_MESSAGES["network_error"])
            logger.error("Failed to construct authorization URL: %s", e)
            raise

    def exchange_code(self, code: str, state: Optional[str] = None) -> Credentials:
        """
        Exchange an authorization code received in the OAuth redirect callback for tokens.

        Args:
            code: The OAuth authorization code string.
            state: Optional state token.

        Returns:
            google.oauth2.credentials.Credentials instance.
        """
        if not code or not code.strip():
            self._set_error("Authorization code is empty", ARABIC_MESSAGES["invalid_code"])
            raise ValueError("Authorization code must not be empty.")

        try:
            client_config = self._get_client_config()
            flow = Flow.from_client_config(
                client_config=client_config,
                scopes=self.scopes,
                state=state,
            )
            flow.redirect_uri = self.redirect_uri
            flow.fetch_token(code=code.strip())
            creds = flow.credentials

            # Cache in memory
            self._credentials = creds

            # Fetch channel metadata if accessible to store channel id and title
            channel_id = None
            channel_title = None
            try:
                info = self._fetch_channel_metadata(creds)
                channel_id = info.get("channel_id")
                channel_title = info.get("title")
            except Exception as ch_err:
                logger.warning("Could not immediately fetch channel profile after auth: %s", ch_err)

            # Persist encrypted
            self._save_token(
                access_token=creds.token,
                refresh_token=creds.refresh_token,
                token_uri=creds.token_uri,
                client_id=creds.client_id or self.client_id,
                client_secret=creds.client_secret or self.client_secret,
                scopes=list(creds.scopes or self.scopes),
                expiry=creds.expiry,
                channel_id=channel_id,
                channel_title=channel_title,
            )

            logger.info("OAuth code successfully exchanged and credentials persisted.")
            return creds
        except Exception as e:
            logger.error("Failed to exchange OAuth code for credentials: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["invalid_code"])
            raise

    # -----------------------------------------------------------------------
    # Credential Retrieval & Automatic Refresh
    # -----------------------------------------------------------------------

    def get_credentials(self) -> Optional[Credentials]:
        """
        Retrieve valid Google OAuth Credentials, automatically refreshing expired tokens.

        Returns:
            Valid Credentials object, or None if unauthenticated or refresh failed.
        """
        # 1. Check in-memory credentials
        if self._credentials is not None:
            if self._validate_and_refresh(self._credentials):
                return self._credentials

        # 2. Load from encrypted database
        record = self._load_token()
        if not record:
            self._set_error("No stored credentials found", ARABIC_MESSAGES["no_credentials"])
            return None

        # Build Credentials object (expiry must be naive UTC datetime for google.auth)
        expiry_naive = record.expiry.replace(tzinfo=None) if record.expiry else None

        creds = Credentials(
            token=record.access_token,
            refresh_token=record.refresh_token,
            token_uri=record.token_uri,
            client_id=record.client_id or self.client_id,
            client_secret=record.client_secret or self.client_secret,
            scopes=record.scopes,
            expiry=expiry_naive,
        )

        # 3. Validate and refresh if necessary
        if self._validate_and_refresh(creds):
            self._credentials = creds
            return creds

        return None

    def _validate_and_refresh(self, creds: Credentials) -> bool:
        """Validate credential freshness and perform automatic refresh if expired."""
        try:
            # If valid and not expired, return True
            if creds.valid and not creds.expired:
                return True

            # If token expired or invalid, attempt refresh if refresh_token exists
            if creds.refresh_token:
                logger.info("Access token expired or approaching expiry. Triggering automatic refresh...")
                request = Request()
                creds.refresh(request)

                if creds.valid:
                    # Update token record in SQLite
                    self._save_token(
                        access_token=creds.token,
                        refresh_token=creds.refresh_token,
                        token_uri=creds.token_uri,
                        client_id=creds.client_id or self.client_id,
                        client_secret=creds.client_secret or self.client_secret,
                        scopes=list(creds.scopes or self.scopes),
                        expiry=creds.expiry,
                    )
                    logger.info("OAuth access token successfully refreshed and saved to database.")
                    return True

            logger.warning("Credentials cannot be refreshed because no refresh token is present.")
            self._set_error("No refresh token available", ARABIC_MESSAGES["refresh_failed"])
            return False

        except RefreshError as re:
            logger.error("Token refresh failed (revoked or expired): %s", re)
            self._set_error(str(re), ARABIC_MESSAGES["refresh_failed"])
            return False
        except Exception as e:
            logger.error("Unexpected error during token refresh: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["network_error"])
            return False

    def refresh_token(self) -> bool:
        """
        Explicitly force a token refresh cycle and persist the updated credentials.

        Returns:
            True if credentials refreshed successfully, False otherwise.
        """
        creds = self.get_credentials()
        if not creds:
            return False

        try:
            request = Request()
            creds.refresh(request)
            if creds.valid:
                self._credentials = creds
                self._save_token(
                    access_token=creds.token,
                    refresh_token=creds.refresh_token,
                    token_uri=creds.token_uri,
                    client_id=creds.client_id or self.client_id,
                    client_secret=creds.client_secret or self.client_secret,
                    scopes=list(creds.scopes or self.scopes),
                    expiry=creds.expiry,
                )
                logger.info("Explicit token refresh succeeded.")
                return True
            return False
        except Exception as e:
            logger.error("Explicit token refresh failed: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["refresh_failed"])
            return False

    def revoke_token(self) -> bool:
        """
        Revoke the OAuth 2.0 authorization with Google and delete encrypted tokens from SQLite.

        Returns:
            True if token was revoked and deleted, False otherwise.
        """
        record = self._load_token()
        token_to_revoke = None
        if record:
            token_to_revoke = record.refresh_token or record.access_token

        success = True
        if token_to_revoke:
            try:
                response = requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token_to_revoke},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                if response.status_code == 200:
                    logger.info("Successfully revoked token via Google OAuth endpoint.")
                else:
                    logger.warning(
                        "Google token revocation returned HTTP %d: %s",
                        response.status_code,
                        response.text,
                    )
            except Exception as e:
                logger.warning("Could not reach Google revoke endpoint: %s", e)
                success = False

        # Delete from local SQLite
        db_deleted = self._delete_token()
        self._credentials = None
        logger.info("Local token data erased from database.")
        return success and db_deleted

    def is_authenticated(self) -> bool:
        """
        Check if the system currently holds valid, ready-to-use YouTube credentials.

        Returns:
            True if credentials exist and are valid/refreshable, False otherwise.
        """
        creds = self.get_credentials()
        return creds is not None and creds.valid

    # -----------------------------------------------------------------------
    # Channel Profile Information
    # -----------------------------------------------------------------------

    def _fetch_channel_metadata(self, creds: Credentials) -> Dict[str, Any]:
        """Execute a channel query using the YouTube Data API v3."""
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        response = youtube.channels().list(
            part="snippet,statistics,contentDetails,status",
            mine=True,
        ).execute()

        items = response.get("items", [])
        if not items:
            return {}

        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        status = item.get("status", {})

        thumbnails = snippet.get("thumbnails", {})
        best_thumb = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
        )

        return {
            "channel_id": item.get("id"),
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "custom_url": snippet.get("customUrl", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail_url": best_thumb,
            "country": snippet.get("country", ""),
            "subscriber_count": int(statistics.get("subscriberCount", 0)),
            "video_count": int(statistics.get("videoCount", 0)),
            "view_count": int(statistics.get("viewCount", 0)),
            "privacy_status": status.get("privacyStatus", ""),
            "is_linked": status.get("isLinked", False),
        }

    def get_channel_info(self) -> Dict[str, Any]:
        """
        Retrieve YouTube channel information for the currently authenticated channel.

        Returns:
            Dictionary containing channel details and Arabic status messages.
        """
        creds = self.get_credentials()
        if not creds:
            return {
                "success": False,
                "error": "Not authenticated",
                "error_ar": ARABIC_MESSAGES["no_credentials"],
                "channel": None,
            }

        try:
            channel_info = self._fetch_channel_metadata(creds)
            if not channel_info:
                return {
                    "success": False,
                    "error": "No channel found for current credentials",
                    "error_ar": ARABIC_MESSAGES["channel_info_failed"],
                    "channel": None,
                }

            return {
                "success": True,
                "channel": channel_info,
                "message_ar": ARABIC_MESSAGES["auth_success"],
            }
        except HttpError as he:
            logger.error("YouTube API HTTP error while fetching channel: %s", he)
            self._set_error(str(he), ARABIC_MESSAGES["channel_info_failed"])
            return {
                "success": False,
                "error": str(he),
                "error_ar": ARABIC_MESSAGES["channel_info_failed"],
                "channel": None,
            }
        except Exception as e:
            logger.error("Error retrieving channel info: %s", e)
            self._set_error(str(e), ARABIC_MESSAGES["network_error"])
            return {
                "success": False,
                "error": str(e),
                "error_ar": ARABIC_MESSAGES["network_error"],
                "channel": None,
            }

    # -----------------------------------------------------------------------
    # Error Helpers
    # -----------------------------------------------------------------------

    def _set_error(self, err_en: str, err_ar: str) -> None:
        """Store the most recent error message in English and Arabic."""
        self.last_error = err_en
        self.last_error_ar = err_ar

    def get_last_error(self, language: str = "ar") -> Optional[str]:
        """Return the last error message in the requested language ('ar' or 'en')."""
        if language.lower() == "ar":
            return self.last_error_ar
        return self.last_error
