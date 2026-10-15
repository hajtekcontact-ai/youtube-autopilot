"""Originality checker module for YouTube AI Autopilot.

Performs content deduplication and plagiarism detection on generated scripts.
Combines SHA-256 cryptographic hashing for exact match identification with
difflib SequenceMatcher fuzzy matching against historical video scripts stored
in the SQLite database and runtime cache.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Project imports with graceful fallback
try:
    from src.config import current_config, Config
except ImportError:
    current_config = None
    Config = None

try:
    from src.database import Video as DBVideo, get_db
except ImportError:
    DBVideo = None
    get_db = None

try:
    from sqlalchemy import text
except ImportError:
    text = None


@dataclass
class OriginalityResult:
    """Detailed evaluation result returned by the OriginalityChecker."""

    is_original: bool
    similarity_score: float  # Highest similarity score (0.0 to 1.0)
    threshold: float = 0.70
    content_hash: str = ""
    matched_id: Optional[str] = None
    matched_title: Optional[str] = None
    reason: str = "Script is original."
    top_matches: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the result to a serializable dictionary.

        Returns:
            Dict[str, Any]: JSON-compatible dictionary.
        """
        return {
            "is_original": self.is_original,
            "similarity_score": round(self.similarity_score, 4),
            "threshold": self.threshold,
            "content_hash": self.content_hash,
            "matched_id": self.matched_id,
            "matched_title": self.matched_title,
            "reason": self.reason,
            "top_matches": self.top_matches,
            "details": self.details,
        }


class OriginalityChecker:
    """Production content deduplication and fuzzy sequence similarity auditor.

    Guarantees script uniqueness by:
    1. SHA-256 exact-match hash detection
    2. Character/Token SequenceMatcher fuzzy similarity scoring
    3. Querying previously rendered and published scripts from the database
    4. Rejecting scripts that exceed the configurable similarity threshold (default 0.70)
    """

    DEFAULT_THRESHOLD: float = 0.70

    def __init__(self, default_threshold: float = DEFAULT_THRESHOLD) -> None:
        """Initialize the OriginalityChecker.

        Args:
            default_threshold: Similarity score above which a script is rejected (0.0 to 1.0).
        """
        self.default_threshold = max(0.0, min(1.0, float(default_threshold)))

        # In-memory script index
        self._indexed_scripts: List[Dict[str, Any]] = []
        self._known_hashes: Set[str] = set()

        self._load_scripts_from_database()

    @staticmethod
    def normalize_text(text_in: str) -> str:
        """Clean and normalize text for robust fuzzy comparison.

        Strips punctuation, excessive whitespace, and visual stage directions.

        Args:
            text_in: Raw narration string.

        Returns:
            str: Lowercase alphanumeric string with single space separation.
        """
        if not text_in:
            return ""
        # Remove parenthesized or bracketed visual cues like [Visual: ...]
        no_cues = re.sub(r"\[.*?\]|\(.*?\)", "", text_in)
        # Keep alphanumeric characters and basic spacing
        clean = re.sub(r"[^a-zA-Z0-9\s]", " ", no_cues.lower())
        return " ".join(clean.split())

    @classmethod
    def compute_hash(cls, text_in: str) -> str:
        """Compute SHA-256 hexadecimal digest for normalized text.

        Args:
            text_in: Raw or normalized text.

        Returns:
            str: 64-character lowercase hexadecimal hash.
        """
        normalized = cls.normalize_text(text_in)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _load_scripts_from_database(self) -> None:
        """Load all historical scripts from the database into the memory index."""
        if get_db is None or text is None:
            return

        try:
            with get_db() as session:
                rows = session.execute(
                    text("SELECT id, title, script FROM videos WHERE script IS NOT NULL AND script != ''")
                ).fetchall()

                loaded_count = 0
                for r in rows:
                    v_id = str(r[0])
                    v_title = str(r[1]) if r[1] else "Untitled Video"
                    v_script = str(r[2]) if r[2] else ""

                    if v_script.strip():
                        self.register_script(video_id=v_id, title=v_title, script_text=v_script)
                        loaded_count += 1

                logger.info("OriginalityChecker: Indexed %d historical scripts from database.", loaded_count)
        except Exception as exc:
            logger.debug("Database script loading handled or skipped: %s", exc)

    def register_script(self, video_id: str, title: str, script_text: str) -> None:
        """Add a script to the in-memory index for subsequent duplicate checks.

        Args:
            video_id: Unique video identifier.
            title: Title of the video.
            script_text: Full voiceover script.
        """
        if not script_text or not script_text.strip():
            return

        norm = self.normalize_text(script_text)
        s_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()

        # Check if hash already indexed
        if s_hash in self._known_hashes:
            return

        words_set = set(norm.split())

        self._known_hashes.add(s_hash)
        self._indexed_scripts.append({
            "video_id": str(video_id),
            "title": str(title),
            "raw_text": script_text,
            "normalized": norm,
            "hash": s_hash,
            "words_set": words_set,
        })

    def _calculate_jaccard(self, set_a: Set[str], set_b: Set[str]) -> float:
        """Compute fast Jaccard word set similarity for candidate screening.

        Args:
            set_a: First set of words.
            set_b: Second set of words.

        Returns:
            float: Jaccard similarity between 0.0 and 1.0.
        """
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return float(intersection) / float(union) if union > 0 else 0.0

    def find_similar(self, script_text: str, top_n: int = 5) -> List[Dict[str, Any]]:
        """Find the top N most similar scripts in the database and index.

        Args:
            script_text: Target script narration.
            top_n: Maximum number of closest matches to return.

        Returns:
            List[Dict[str, Any]]: Sorted list of matched records with similarity scores.
        """
        if not script_text or not script_text.strip():
            return []

        norm_target = self.normalize_text(script_text)
        target_hash = hashlib.sha256(norm_target.encode("utf-8")).hexdigest()
        target_words = set(norm_target.split())

        matches: List[Dict[str, Any]] = []

        for candidate in self._indexed_scripts:
            c_hash = candidate["hash"]
            c_norm = candidate["normalized"]

            # Exact match check
            if target_hash == c_hash:
                matches.append({
                    "video_id": candidate["video_id"],
                    "title": candidate["title"],
                    "similarity": 1.0,
                    "match_type": "exact_hash",
                    "snippet": candidate["raw_text"][:200] + "...",
                })
                continue

            # Quick Jaccard screening to skip radically different texts
            jaccard = self._calculate_jaccard(target_words, candidate["words_set"])
            if jaccard < 0.20 and len(norm_target) > 200:
                # Similarity cannot realistically exceed 0.65; assign fast lower-bound
                ratio = round(jaccard * 0.9, 4)
            else:
                # Run exact SequenceMatcher
                ratio = round(difflib.SequenceMatcher(None, norm_target, c_norm).ratio(), 4)

            matches.append({
                "video_id": candidate["video_id"],
                "title": candidate["title"],
                "similarity": ratio,
                "match_type": "fuzzy_sequence",
                "snippet": candidate["raw_text"][:200] + "...",
            })

        # Sort descending by similarity score
        matches.sort(key=lambda x: x["similarity"], reverse=True)
        return matches[:top_n]

    def is_original(
        self,
        script_text: str,
        threshold: Optional[float] = None,
    ) -> OriginalityResult:
        """Audit a script for originality against all indexed and historical scripts.

        Rejects the script if any match exceeds or equals the similarity threshold.

        Args:
            script_text: Voiceover narration to test.
            threshold: Optional custom similarity threshold (defaults to 0.70).

        Returns:
            OriginalityResult: Complete result containing pass/fail, max similarity,
                               content hash, and closest matching scripts.
        """
        active_threshold = threshold if threshold is not None else self.default_threshold
        active_threshold = max(0.0, min(1.0, float(active_threshold)))

        if not script_text or not script_text.strip():
            return OriginalityResult(
                is_original=False,
                similarity_score=0.0,
                threshold=active_threshold,
                content_hash="",
                reason="Script content is empty.",
            )

        norm_target = self.normalize_text(script_text)
        target_hash = hashlib.sha256(norm_target.encode("utf-8")).hexdigest()

        # Step 1: Check Exact Hash
        if target_hash in self._known_hashes:
            matched_cand = next((c for c in self._indexed_scripts if c["hash"] == target_hash), None)
            matched_id = matched_cand["video_id"] if matched_cand else "unknown"
            matched_title = matched_cand["title"] if matched_cand else "Existing Video"

            logger.warning(
                "Originality check REJECTED: Exact SHA-256 match found with video '%s' (ID: %s)",
                matched_title,
                matched_id,
            )
            return OriginalityResult(
                is_original=False,
                similarity_score=1.0,
                threshold=active_threshold,
                content_hash=target_hash,
                matched_id=matched_id,
                matched_title=matched_title,
                reason=f"Exact duplicate detected (SHA-256 match with '{matched_title}').",
                top_matches=[{
                    "video_id": matched_id,
                    "title": matched_title,
                    "similarity": 1.0,
                    "match_type": "exact_hash",
                }],
            )

        # Step 2: Fuzzy Sequence Matching
        top_matches = self.find_similar(script_text, top_n=5)
        max_similarity = top_matches[0]["similarity"] if top_matches else 0.0
        best_match = top_matches[0] if top_matches else None

        matched_id = best_match["video_id"] if best_match else None
        matched_title = best_match["title"] if best_match else None

        if max_similarity >= active_threshold:
            logger.warning(
                "Originality check REJECTED: Similarity %.2f exceeds threshold %.2f (matched '%s', ID: %s)",
                max_similarity,
                active_threshold,
                matched_title,
                matched_id,
            )
            reason = (
                f"Similarity score of {max_similarity:.2f} exceeds threshold of {active_threshold:.2f} "
                f"(closely matches '{matched_title}')."
            )
            is_orig = False
        else:
            logger.info(
                "Originality check PASSED: Max similarity %.2f is below threshold %.2f",
                max_similarity,
                active_threshold,
            )
            reason = f"Script is original (maximum similarity: {max_similarity:.2f})."
            is_orig = True

        return OriginalityResult(
            is_original=is_orig,
            similarity_score=max_similarity,
            threshold=active_threshold,
            content_hash=target_hash,
            matched_id=matched_id,
            matched_title=matched_title,
            reason=reason,
            top_matches=top_matches,
            details={
                "indexed_scripts_count": len(self._indexed_scripts),
                "script_word_count": len(norm_target.split()),
            },
        )
