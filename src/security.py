"""Security and cryptography module for YouTube AI Autopilot.

Provides Fernet encryption for API tokens, bcrypt password hashing, input sanitization,
thread-safe rate limiting, emergency stop kill switches, API cost monitoring,
cryptographic secret generation, CSRF validation, and session verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from src.config import current_config

logger = logging.getLogger(__name__)

# State lock for concurrency control
_security_lock = threading.Lock()

# Rate limiting storage: key -> list of timestamp floats
_rate_limits: Dict[str, List[float]] = {}
_rate_limit_lock = threading.Lock()

# Persistent state directory
STATE_DIR = current_config.DATA_DIR / "state"
EMERGENCY_STOP_FILE = STATE_DIR / "emergency_stop.lock"
COST_STATE_FILE = STATE_DIR / "cost_tracker.json"


# ============================================================================
# 1. Token Encryption / Decryption (Fernet)
# ============================================================================

def _derive_fernet_key(raw_key: Optional[str] = None) -> bytes:
    """Derive a valid 32-byte urlsafe base64 Fernet key from any string or secret.

    Args:
        raw_key: Optional raw encryption key string. Defaults to Config.ENCRYPTION_KEY.

    Returns:
        bytes: 32-byte base64-encoded Fernet key.
    """
    key_str = raw_key or current_config.ENCRYPTION_KEY
    if not key_str:
        # Fall back to application secret or fixed salt if not explicitly provided
        key_str = current_config.DASHBOARD_SECRET_KEY or "fallback-autopilot-transient-key"

    try:
        # Check if already a valid Fernet key
        key_bytes = key_str.strip().encode("utf-8")
        decoded = base64.urlsafe_b64decode(key_bytes)
        if len(decoded) == 32:
            return key_bytes
    except Exception:
        pass

    # Deterministically derive 32 bytes using SHA-256 and base64-encode
    digest = hashlib.sha256(key_str.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_token(plain_token: str, secret_key: Optional[str] = None) -> str:
    """Encrypt sensitive string tokens (e.g. OAuth tokens, API secrets) using Fernet.

    Args:
        plain_token: Plaintext token string.
        secret_key: Optional encryption key.

    Returns:
        str: Encrypted ciphertext as URL-safe ASCII string.

    Raises:
        ValueError: If plain_token is empty.
    """
    if not plain_token:
        raise ValueError("Cannot encrypt empty token.")

    fernet_key = _derive_fernet_key(secret_key)
    f = Fernet(fernet_key)
    token_bytes = plain_token.encode("utf-8")
    encrypted_bytes = f.encrypt(token_bytes)
    return encrypted_bytes.decode("utf-8")


def decrypt_token(cipher_token: str, secret_key: Optional[str] = None) -> str:
    """Decrypt a Fernet-encrypted ciphertext string.

    Args:
        cipher_token: Encrypted token string.
        secret_key: Optional encryption key.

    Returns:
        str: Decrypted plaintext string.

    Raises:
        ValueError: If ciphertext is invalid, corrupted, or key does not match.
    """
    if not cipher_token:
        return ""

    fernet_key = _derive_fernet_key(secret_key)
    f = Fernet(fernet_key)
    try:
        decrypted_bytes = f.decrypt(cipher_token.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as exc:
        logger.error("Failed to decrypt token: invalid token or mismatched key.")
        raise ValueError("Decryption failed: Token is invalid or secret key does not match.") from exc


# ============================================================================
# 2. Password Hashing (bcrypt)
# ============================================================================

def hash_password(password: str, rounds: int = 12) -> str:
    """Hash a plaintext password using bcrypt.

    Args:
        password: Plaintext password.
        rounds: Work factor (number of salt rounds).

    Returns:
        str: Bcrypt hashed string.

    Raises:
        ValueError: If password is empty.
    """
    if not password:
        raise ValueError("Password cannot be empty.")

    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=rounds)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash in constant time.

    Args:
        plain_password: Provided password string.
        hashed_password: Stored bcrypt hash string.

    Returns:
        bool: True if password matches hash, False otherwise.
    """
    if not plain_password or not hashed_password:
        return False

    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception as exc:
        logger.warning("Error verifying password hash: %s", exc)
        return False


# ============================================================================
# 3. Input Validation and Sanitization
# ============================================================================

def sanitize_text(text: str, max_length: int = 5000, allow_html: bool = False) -> str:
    """Sanitize user or AI input string to prevent injection and control character abuse.

    Args:
        text: Input string.
        max_length: Maximum allowed length.
        allow_html: Whether to allow safe HTML or escape all tags.

    Returns:
        str: Cleaned and truncated string.
    """
    if not text:
        return ""

    # Remove null bytes and ASCII control characters (preserving tabs, newlines, CR)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    if not allow_html:
        cleaned = html.escape(cleaned)

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned.strip()


def sanitize_path(target_path: Union[str, Path], base_dir: Optional[Union[str, Path]] = None) -> Path:
    """Sanitize a file path and protect against directory traversal attacks (e.g. '../').

    Ensures the target resolved path is strictly contained within base_dir.

    Args:
        target_path: Proposed path.
        base_dir: Permitted directory root. Defaults to project BASE_DIR.

    Returns:
        Path: Canonical resolved path.

    Raises:
        ValueError: If target path escapes the permitted base directory.
    """
    root = Path(base_dir or current_config.BASE_DIR).resolve()
    target = (root / target_path).resolve() if not Path(target_path).is_absolute() else Path(target_path).resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path traversal detected: {target_path} is outside allowed root {root}") from exc

    return target


def validate_email(email: str) -> bool:
    """Validate format of an email address.

    Args:
        email: Email string to test.

    Returns:
        bool: True if valid email format.
    """
    if not email or len(email) > 254:
        return False
    email_regex = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    return bool(email_regex.match(email.strip()))


def validate_url(url: str, allowed_schemes: Tuple[str, ...] = ("http", "https")) -> bool:
    """Validate format and scheme of a URL.

    Args:
        url: URL string.
        allowed_schemes: Allowed URI schemes.

    Returns:
        bool: True if valid URL.
    """
    if not url or len(url) > 2048:
        return False
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in allowed_schemes and bool(parsed.netloc)
    except Exception:
        return False


def validate_youtube_id(video_id: str) -> bool:
    """Validate YouTube 11-character video identifier format.

    Args:
        video_id: YouTube video ID string.

    Returns:
        bool: True if valid format.
    """
    if not video_id:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_-]{11}$", video_id.strip()))


# ============================================================================
# 4. Rate Limiting Helper (Thread-safe sliding window)
# ============================================================================

class RateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, List[float]] = {}

    def is_allowed(self, key: str, max_requests: int = 60, window_seconds: int = 60) -> bool:
        """Check if request for key is allowed under rate limit and record it if allowed.

        Args:
            key: Rate limit identifier (e.g., client IP or endpoint name).
            max_requests: Maximum allowed requests in window.
            window_seconds: Window duration in seconds.

        Returns:
            bool: True if permitted, False if limit exceeded.
        """
        now = time.time()
        cutoff = now - window_seconds

        with self._lock:
            timestamps = self._records.get(key, [])
            # Filter timestamps outside window
            valid_timestamps = [t for t in timestamps if t > cutoff]

            if len(valid_timestamps) >= max_requests:
                self._records[key] = valid_timestamps
                return False

            valid_timestamps.append(now)
            self._records[key] = valid_timestamps
            return True

    def get_remaining(self, key: str, max_requests: int = 60, window_seconds: int = 60) -> int:
        """Get remaining permitted requests within the window.

        Args:
            key: Rate limit identifier.
            max_requests: Limit ceiling.
            window_seconds: Window duration in seconds.

        Returns:
            int: Remaining allowed request count.
        """
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            valid = [t for t in self._records.get(key, []) if t > cutoff]
            return max(0, max_requests - len(valid))

    def reset(self, key: Optional[str] = None) -> None:
        """Clear rate limit records for a key or all keys.

        Args:
            key: Specific key to clear or None for all.
        """
        with self._lock:
            if key:
                self._records.pop(key, None)
            else:
                self._records.clear()


# Global rate limiter instance
rate_limiter = RateLimiter()


# ============================================================================
# 5. Emergency Stop Check & Control
# ============================================================================

def is_emergency_stop_active() -> bool:
    """Check whether the system-wide emergency stop switch is currently triggered.

    Evaluates environment variable, configuration setting, and filesystem lock.

    Returns:
        bool: True if emergency stop is active, halting pipeline activities.
    """
    if os.getenv("EMERGENCY_STOP", "").strip().lower() in ("true", "1", "yes", "y", "on"):
        return True
    if current_config.EMERGENCY_STOP:
        return True

    return EMERGENCY_STOP_FILE.exists()


def set_emergency_stop(active: bool, reason: str = "") -> None:
    """Set or clear the system emergency stop switch.

    Args:
        active: True to engage emergency stop, False to disengage.
        reason: Optional justification recorded in audit log.
    """
    with _security_lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if active:
            data = {
                "active": True,
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason or "Emergency stop triggered by operator/system",
            }
            EMERGENCY_STOP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.critical("EMERGENCY STOP ACTIVATED: %s", reason, extra={"event_key": "emergency_stop_activated"})
        else:
            if EMERGENCY_STOP_FILE.exists():
                EMERGENCY_STOP_FILE.unlink(missing_ok=True)
            logger.warning("EMERGENCY STOP DEACTIVATED: Resuming normal operations.", extra={"event_key": "emergency_stop_deactivated"})


# ============================================================================
# 6. Cost Kill Switch Check & Tracking
# ============================================================================

def _load_cost_data() -> Dict[str, Any]:
    """Load cost tracking JSON file."""
    if not COST_STATE_FILE.exists():
        return {"dates": {}, "kill_switch_active": False}
    try:
        return json.loads(COST_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"dates": {}, "kill_switch_active": False}


def _save_cost_data(data: Dict[str, Any]) -> None:
    """Save cost tracking JSON file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COST_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_cost_kill_switch_active() -> bool:
    """Check whether the cost kill switch is engaged.

    Returns:
        bool: True if spending limit is tripped or config flag is set.
    """
    if os.getenv("COST_KILL_SWITCH", "").strip().lower() in ("true", "1", "yes", "y", "on"):
        return True
    if current_config.COST_KILL_SWITCH:
        return True

    data = _load_cost_data()
    return bool(data.get("kill_switch_active", False))


def set_cost_kill_switch(active: bool) -> None:
    """Engage or disengage the cost kill switch.

    Args:
        active: Boolean state.
    """
    with _security_lock:
        data = _load_cost_data()
        data["kill_switch_active"] = active
        _save_cost_data(data)
        event_key = "cost_kill_switch_activated" if active else "cost_kill_switch_deactivated"
        logger.warning("Cost kill switch set to: %s", active, extra={"event_key": event_key})


def record_cost(amount_usd: float, description: str = "") -> float:
    """Record an API expenditure and verify against daily budget threshold.

    If cumulative daily spending exceeds MAX_DAILY_API_COST_USD (when > 0),
    the cost kill switch is automatically triggered.

    Args:
        amount_usd: Expense in USD.
        description: Description of API call.

    Returns:
        float: Updated daily expenditure total.
    """
    if amount_usd <= 0.0:
        return get_daily_cost()

    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    with _security_lock:
        data = _load_cost_data()
        dates = data.setdefault("dates", {})
        day_record = dates.setdefault(today_str, {"total": 0.0, "items": []})

        day_record["total"] = round(day_record.get("total", 0.0) + amount_usd, 4)
        day_record.setdefault("items", []).append({
            "timestamp": datetime.utcnow().isoformat(),
            "amount": amount_usd,
            "description": description,
        })

        new_total: float = day_record["total"]

        max_allowed = current_config.MAX_DAILY_API_COST_USD
        if max_allowed > 0.0 and new_total >= max_allowed:
            data["kill_switch_active"] = True
            logger.critical(
                "Daily API cost ($%.2f) reached budget limit ($%.2f). Cost kill switch activated!",
                new_total, max_allowed,
                extra={"event_key": "cost_kill_switch_activated"}
            )

        _save_cost_data(data)
        return new_total


def get_daily_cost(date_str: Optional[str] = None) -> float:
    """Get total recorded API cost in USD for a given date.

    Args:
        date_str: Target date formatted as YYYY-MM-DD. Defaults to UTC today.

    Returns:
        float: Cumulative expense in USD.
    """
    target_date = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    data = _load_cost_data()
    return float(data.get("dates", {}).get(target_date, {}).get("total", 0.0))


def check_daily_cost_limit(max_cost_usd: Optional[float] = None) -> bool:
    """Verify whether current daily expenditure is within permitted limits.

    Args:
        max_cost_usd: Optional override for maximum daily budget.

    Returns:
        bool: True if cost is within budget, False if limit reached or kill switch active.
    """
    if is_cost_kill_switch_active():
        return False

    limit = max_cost_usd if max_cost_usd is not None else current_config.MAX_DAILY_API_COST_USD
    if limit <= 0.0:
        # 0.0 means free-tier only or unlimited if not specified
        return True

    current_spend = get_daily_cost()
    return current_spend < limit


# ============================================================================
# 7. Secure Secret Generation
# ============================================================================

def generate_secret_token(length: int = 32) -> str:
    """Generate a cryptographically secure URL-safe random string.

    Args:
        length: Desired byte entropy length.

    Returns:
        str: URL-safe random secret token.
    """
    return secrets.token_urlsafe(length)


def generate_encryption_key() -> str:
    """Generate a brand-new, valid base64-encoded 32-byte Fernet key.

    Returns:
        str: Fernet key string suitable for ENCRYPTION_KEY environment variable.
    """
    return Fernet.generate_key().decode("utf-8")


# ============================================================================
# 8. API Key Validation (Format Checks)
# ============================================================================

def validate_gemini_api_key(api_key: str) -> bool:
    """Verify structure and character set of a Google Gemini API Key.

    Google API keys typically begin with 'AIza' and are 39 characters in total.

    Args:
        api_key: Candidate API key.

    Returns:
        bool: True if key matches the expected format.
    """
    if not api_key:
        return False
    return bool(re.match(r"^AIza[0-9A-Za-z_\-]{30,45}$", api_key.strip()))


def validate_youtube_client_secret(secret: str) -> bool:
    """Verify structure of a YouTube / Google OAuth Client Secret.

    Google Client Secrets typically start with 'GOCSPX-' or are 24-40 alphanumeric chars.

    Args:
        secret: Candidate client secret string.

    Returns:
        bool: True if secret format is valid.
    """
    if not secret:
        return False
    clean = secret.strip()
    if clean.startswith("GOCSPX-"):
        return len(clean) >= 28
    return len(clean) >= 20 and bool(re.match(r"^[a-zA-Z0-9_\-]+$", clean))


def validate_api_key_format(key: str, min_len: int = 16) -> bool:
    """General validation check for API keys and tokens.

    Args:
        key: Candidate key string.
        min_len: Minimum allowed length.

    Returns:
        bool: True if key meets length and character constraints.
    """
    if not key or len(key.strip()) < min_len:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_\-\.\:\+]+$", key.strip()))


# ============================================================================
# 9. CSRF Protection Helper
# ============================================================================

def generate_csrf_token(session_id: str, secret_key: Optional[str] = None) -> str:
    """Generate a signed, timestamped CSRF token bound to a session identifier.

    Args:
        session_id: User session identifier.
        secret_key: Secret key for signing. Defaults to DASHBOARD_SECRET_KEY.

    Returns:
        str: Token in the format '{timestamp}.{session_id}.{signature}'.
    """
    secret = (secret_key or current_config.DASHBOARD_SECRET_KEY).encode("utf-8")
    timestamp = str(int(time.time()))
    payload = f"{timestamp}:{session_id}".encode("utf-8")
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"{timestamp}.{session_id}.{sig}"


def validate_csrf_token(
    token: str,
    session_id: str,
    max_age_seconds: int = 3600,
    secret_key: Optional[str] = None,
) -> bool:
    """Validate a signed CSRF token against session ID and expiration time.

    Args:
        token: Incoming token string.
        session_id: Expected session ID.
        max_age_seconds: Maximum allowed token age in seconds (default 1 hour).
        secret_key: Secret signing key.

    Returns:
        bool: True if token signature is valid, session matches, and token is not expired.
    """
    if not token or not session_id:
        return False

    parts = token.split(".")
    if len(parts) != 3:
        return False

    token_time_str, token_session_id, received_sig = parts

    # Session identifier check
    if not hmac.compare_digest(token_session_id, session_id):
        return False

    # Timestamp expiration check
    try:
        token_time = int(token_time_str)
    except ValueError:
        return False

    now = int(time.time())
    if now - token_time > max_age_seconds or token_time > now + 60:
        return False

    # HMAC signature check
    secret = (secret_key or current_config.DASHBOARD_SECRET_KEY).encode("utf-8")
    expected_payload = f"{token_time_str}:{session_id}".encode("utf-8")
    expected_sig = hmac.new(secret, expected_payload, hashlib.sha256).hexdigest()

    return hmac.compare_digest(received_sig, expected_sig)


# ============================================================================
# 10. Session Security
# ============================================================================

def create_session_payload(
    user_id: str,
    ip_address: str,
    user_agent: str,
    ttl_seconds: int = 86400,
) -> Dict[str, Any]:
    """Create a secured session dictionary with hashed fingerprinting for IP and User-Agent.

    Args:
        user_id: Authenticated user ID or username.
        ip_address: Client remote IP address.
        user_agent: Client browser user agent string.
        ttl_seconds: Session duration in seconds (default 24 hours).

    Returns:
        Dict[str, Any]: Signed session metadata payload.
    """
    now = int(time.time())
    ip_salt = current_config.DASHBOARD_SECRET_KEY[:8]
    ip_hash = hashlib.sha256(f"{ip_address}:{ip_salt}".encode("utf-8")).hexdigest()[:16]
    ua_hash = hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:16]

    return {
        "session_id": generate_secret_token(24),
        "user_id": user_id,
        "issued_at": now,
        "expires_at": now + ttl_seconds,
        "ip_hash": ip_hash,
        "ua_hash": ua_hash,
    }


def verify_session(
    session_data: Dict[str, Any],
    current_ip: str,
    current_ua: str,
    verify_ip: bool = False,
) -> bool:
    """Verify session expiration and client browser fingerprinting.

    Args:
        session_data: Decrypted session dictionary.
        current_ip: Current request IP address.
        current_ua: Current request User-Agent.
        verify_ip: Whether to strictly enforce IP matching (disabled by default for mobile users).

    Returns:
        bool: True if session is valid and active.
    """
    if not isinstance(session_data, dict):
        return False

    # Check expiration
    expires_at = session_data.get("expires_at", 0)
    if time.time() > expires_at:
        return False

    # Check User-Agent fingerprint
    expected_ua_hash = session_data.get("ua_hash")
    if expected_ua_hash:
        current_ua_hash = hashlib.sha256(current_ua.encode("utf-8")).hexdigest()[:16]
        if not hmac.compare_digest(expected_ua_hash, current_ua_hash):
            logger.warning("Session rejected: User-Agent fingerprint mismatch.")
            return False

    # Strict IP check if requested
    if verify_ip:
        expected_ip_hash = session_data.get("ip_hash")
        ip_salt = current_config.DASHBOARD_SECRET_KEY[:8]
        current_ip_hash = hashlib.sha256(f"{current_ip}:{ip_salt}".encode("utf-8")).hexdigest()[:16]
        if not hmac.compare_digest(expected_ip_hash, current_ip_hash):
            logger.warning("Session rejected: IP address mismatch.")
            return False

    return True


# Convenience aliases for interface compatibility
sanitize_input = sanitize_text
generate_secret = generate_secret_token
check_emergency_stop = is_emergency_stop_active
check_cost_kill_switch = is_cost_kill_switch_active

