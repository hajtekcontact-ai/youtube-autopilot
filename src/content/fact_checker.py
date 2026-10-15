"""Fact checker module for YouTube AI Autopilot.

Validates video script assertions against established scientific consensus,
detects misinformation patterns, flags unverified or absolute claims, and enforces
strict regulatory red flags for medical, financial, and legal claims.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Project imports with graceful fallback
try:
    from src.config import current_config, Config
except ImportError:
    current_config = None
    Config = None

try:
    from src.utils.helpers import safe_json_loads
except ImportError:
    def safe_json_loads(text: Any, default: Any = None) -> Any:
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            return default

# Optional Gemini API
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    genai = None
    HAS_GENAI = False


@dataclass
class FactCheckResult:
    """Structured result of a script fact-checking analysis."""

    passed: bool
    confidence_score: float  # 0.0 to 1.0
    flagged_claims: List[Dict[str, Any]] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    checked_by: str = "rules"  # "gemini", "rules", or "hybrid"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize fact check result to dictionary.

        Returns:
            Dict[str, Any]: JSON-serializable dictionary.
        """
        return {
            "passed": self.passed,
            "confidence_score": round(self.confidence_score, 2),
            "flagged_claims": self.flagged_claims,
            "red_flags": self.red_flags,
            "recommendations": self.recommendations,
            "checked_by": self.checked_by,
            "details": self.details,
        }


class FactChecker:
    """Production fact-checking engine combining regex pattern analysis and Gemini LLM.

    Evaluates scripts for:
    1. Dangerous medical claims and unverified health cures
    2. Deceptive financial advice and guaranteed yield schemes
    3. Unauthorized legal claims and jurisdictional myths
    4. Pseudoscience and persistent popular myths (e.g., 10% brain myth)
    5. Unsubstantiated absolute statements and hyperbole
    """

    # Categorized rule patterns with severity: 'critical', 'high', 'medium'
    PATTERN_RULES: List[Dict[str, Any]] = [
        # Medical Claims (Critical / High)
        {
            "category": "medical",
            "severity": "critical",
            "regex": r"\b(cure[s]?|curing|cured)\s+(?:of\s+)?(cancer|diabetes|autism|alzheimer['s]?|aids|hiv|covid|dementia)\b",
            "issue": "Unverified medical cure claim for serious chronic/fatal illness.",
            "recommendation": "Reframe as 'ongoing experimental research' or 'therapeutic management'. Never claim a definitive cure.",
        },
        {
            "category": "medical",
            "severity": "critical",
            "regex": r"\bmiracle\s+(?:cure|drug|remedy|treatment|pill|supplement|herb)\b",
            "issue": "Sensationalized 'miracle' medical treatment terminology.",
            "recommendation": "Remove 'miracle' descriptors; describe specific chemical compounds and clinical phases.",
        },
        {
            "category": "medical",
            "severity": "critical",
            "regex": r"\bstop\s+taking\s+(?:your|prescribed|prescription)\s+(?:medication|medicine|drugs|pills|treatment)\b",
            "issue": "Direct advice instructing viewers to cease medical prescription.",
            "recommendation": "Immediately remove advice to cease medication; include disclaimer: 'Consult a physician before altering medical treatments.'",
        },
        {
            "category": "medical",
            "severity": "high",
            "regex": r"\bbig\s+pharma\s+(?:is\s+hiding|doesn't\s+want\s+you\s+to\s+know|conspiracy|secret)\b",
            "issue": "Conspiratorial medical trope regarding suppressed treatments.",
            "recommendation": "Cite verifiable regulatory documentation or peer-reviewed clinical trials rather than conspiracy framing.",
        },
        {
            "category": "medical",
            "severity": "high",
            "regex": r"\bdetox(?:ifies|ification)?\s+(?:toxins|body|liver|colon)\s+(?:overnight|instantly|in\s+\d+\s+hours|completely)\b",
            "issue": "Pseudoscience detox myth; organs continuously filter without external 'cleanses'.",
            "recommendation": "Clarify that the liver, kidneys, and lungs perform continuous biological waste filtration.",
        },
        {
            "category": "medical",
            "severity": "high",
            "regex": r"\blose\s+\d+\s+(?:pounds|lbs|kg|kilos)\s+in\s+\d+\s+(?:days|hours)\b",
            "issue": "Extreme rapid weight loss claim posing metabolic health risks.",
            "recommendation": "Cite healthy clinical guidelines of 1-2 lbs per week via sustainable caloric balance.",
        },

        # Financial Advice (Critical / High)
        {
            "category": "financial",
            "severity": "critical",
            "regex": r"\bguaranteed\s+(?:returns?|profit[s]?|income|wealth|gains?|yield|roi)\b",
            "issue": "Unlawful promise of guaranteed financial investment return.",
            "recommendation": "Remove 'guaranteed'. Financial regulations require disclaiming that all investments carry capital risk.",
        },
        {
            "category": "financial",
            "severity": "critical",
            "regex": r"\brisk-?free\s+(?:investment|trading|strategy|crypto|income|arbitrage)\b",
            "issue": "False claim that an investment vehicle is risk-free.",
            "recommendation": "Acknowledge downside risks and market volatility.",
        },
        {
            "category": "financial",
            "severity": "high",
            "regex": r"\b(?:make|earn)\s+\$\d+(?:,\d+)?\s+(?:a\s+day|per\s+day|overnight|in\s+minutes|every\s+hour)\b",
            "issue": "Get-rich-quick income solicitation pattern.",
            "recommendation": "Provide realistic industry averages and emphasize requisite skill and time investment.",
        },
        {
            "category": "financial",
            "severity": "high",
            "regex": r"\b(?:100x|1000x|10000x)\s+(?:crypto|token|coin|gem|moon|opportunity)\b",
            "issue": "Hyperbolic cryptocurrency speculative promotion.",
            "recommendation": "Focus on underlying blockchain protocol architecture rather than speculative price targets.",
        },
        {
            "category": "financial",
            "severity": "high",
            "regex": r"\bsecret\s+(?:loophole|glitch|cheat\s+code)\s+to\s+(?:unlimited|free)\s+money\b",
            "issue": "Exploit or fraud implication in financial transactions.",
            "recommendation": "Focus on legitimate financial literacy principles.",
        },

        # Legal Statements (Critical / High)
        {
            "category": "legal",
            "severity": "critical",
            "regex": r"\bpolice\s+(?:cannot|can't)\s+legally\s+(?:stop|search|arrest|detain)\b",
            "issue": "Inaccurate or dangerously absolute assertion of police authority limits.",
            "recommendation": "Qualify with specific jurisdictional statutes (e.g., probable cause, Fourth Amendment).",
        },
        {
            "category": "legal",
            "severity": "critical",
            "regex": r"\bsovereign\s+citizen\b",
            "issue": "Sovereign citizen pseudolegal doctrine consistently rejected in all judicial courts.",
            "recommendation": "Do not endorse pseudolegal theories regarding state sovereignty.",
        },
        {
            "category": "legal",
            "severity": "critical",
            "regex": r"\b(?:secret|legal)\s+loophole\s+to\s+(?:not\s+pay|avoid\s+paying|evade)\s+taxes\b",
            "issue": "Tax evasion framing presented as a secret loophole.",
            "recommendation": "Distinguish between standard tax deductions/credits and illegal tax avoidance schemes.",
        },
        {
            "category": "legal",
            "severity": "high",
            "regex": r"\bguaranteed\s+to\s+win\s+in\s+court\b",
            "issue": "Definitive guarantee of judicial outcome.",
            "recommendation": "Court decisions depend on judicial discretion, precedents, and evidence. Remove guarantees.",
        },

        # Pseudoscience & Common Urban Myths (High)
        {
            "category": "pseudoscience",
            "severity": "high",
            "regex": r"\bhumans?\s+only\s+use\s+10%?\s+of\s+(?:their|our)\s+brains?\b",
            "issue": "The 10% brain myth has been thoroughly debunked by fMRI and PET neuroimaging.",
            "recommendation": "Clarify that humans use virtually 100% of their brains over a 24-hour cycle.",
        },
        {
            "category": "pseudoscience",
            "severity": "high",
            "regex": r"\b(?:the\s+)?earth\s+is\s+flat\b",
            "issue": "Flat Earth assertion contrary to all astronomical and geodetic measurements.",
            "recommendation": "Frame as a historical belief or discuss geodesy and planetary curvature.",
        },
        {
            "category": "pseudoscience",
            "severity": "high",
            "regex": r"\bvaccines?\s+cause[s]?\s+autism\b",
            "issue": "Debunked anti-vaccine myth based on retracted Wakefield paper.",
            "recommendation": "Acknowledge that extensive multi-million-subject global studies found zero causal link.",
        },
        {
            "category": "pseudoscience",
            "severity": "high",
            "regex": r"\b5g\s+(?:causes|spreads|creates|activates)\s+(?:radiation|cancer|illness|covid)\b",
            "issue": "Pseudoscience conspiracy linking non-ionizing RF radiation to pathogens.",
            "recommendation": "Cite WHO and FCC safety guidelines regarding non-ionizing radio frequencies.",
        },
        {
            "category": "pseudoscience",
            "severity": "high",
            "regex": r"\b(?:free\s+energy\s+device|perpetual\s+motion\s+machine)\b",
            "issue": "Violates the First and Second Laws of Thermodynamics.",
            "recommendation": "Explain that energy cannot be created out of nothing; conservation of energy strictly applies.",
        },
        {
            "category": "pseudoscience",
            "severity": "medium",
            "regex": r"\bgreat\s+wall\s+of\s+china\s+(?:is\s+the\s+only\s+man-made\s+object\s+visible|is\s+visible)\s+from\s+(?:the\s+moon|space)\b",
            "issue": "Common myth: astronauts confirm the Great Wall is not visible to the naked human eye from orbit or the Moon.",
            "recommendation": "Note that astronaut photos require high-zoom lenses; highways and airports are more visible from orbit.",
        },

        # Unsubstantiated Absolutes & Hyperbole (Medium)
        {
            "category": "sensationalism",
            "severity": "medium",
            "regex": r"\bscientists?\s+(?:are\s+completely\s+baffled|have\s+no\s+explanation|can't\s+explain\s+this\s+at\s+all)\b",
            "issue": "Sensationalized claim that science has zero working hypotheses.",
            "recommendation": "Explain the leading competing scientific hypotheses rather than asserting complete ignorance.",
        },
        {
            "category": "sensationalism",
            "severity": "medium",
            "regex": r"\bundeniable\s+proof\s+of\s+alien[s]?\b",
            "issue": "Unproven assertion of extraterrestrial life existence.",
            "recommendation": "Frame SETI findings as anomalies or technosignature candidates under ongoing investigation.",
        },
        {
            "category": "sensationalism",
            "severity": "medium",
            "regex": r"\bthis\s+(?:secret|discovery)\s+will\s+change\s+everything\s+forever\b",
            "issue": "Sensational hyperbole reducing documentary credibility.",
            "recommendation": "Tone down hyperbole and quantify specific measured impacts.",
        },
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        rpm_limit: int = 15,
    ) -> None:
        """Initialize FactChecker.

        Args:
            api_key: Optional Gemini API key. Defaults to current_config.GEMINI_API_KEY.
            model_name: Optional Gemini model name. Defaults to current_config.GEMINI_MODEL.
            rpm_limit: API requests per minute.
        """
        self.api_key = api_key or (current_config.GEMINI_API_KEY if current_config else "")
        self.model_name = model_name or (current_config.GEMINI_MODEL if current_config else "gemini-1.5-flash")
        self.min_interval = 60.0 / max(1, rpm_limit)
        self.last_call_time = 0.0
        self._lock = threading.Lock()

        self._gemini_initialized = False
        self._init_gemini()

    def _init_gemini(self) -> None:
        """Configure Gemini client if API key and library are present."""
        if HAS_GENAI and self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self._gemini_initialized = True
                logger.info("FactChecker: Gemini API initialized successfully.")
            except Exception as exc:
                logger.warning("FactChecker: Failed to initialize Gemini API: %s. Using rule engine.", exc)
                self._gemini_initialized = False
        else:
            self._gemini_initialized = False

    def _rate_limit(self) -> None:
        """Enforce rate limits on outgoing Gemini requests."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call_time = time.time()

    def _check_with_rules(self, script_text: str) -> FactCheckResult:
        """Evaluate script against local regex patterns for red flags and misinformation.

        Args:
            script_text: Voiceover narration text.

        Returns:
            FactCheckResult: Local rule-based result.
        """
        flagged_claims: List[Dict[str, Any]] = []
        red_flags: List[str] = []
        recommendations: List[str] = []

        confidence = 1.0
        has_critical = False

        for rule in self.PATTERN_RULES:
            pattern = rule["regex"]
            matches = list(re.finditer(pattern, script_text, re.IGNORECASE))
            if matches:
                severity = rule["severity"]
                category = rule["category"]

                for m in matches:
                    matched_snippet = m.group(0)
                    flag_entry = {
                        "category": category,
                        "severity": severity,
                        "matched_text": matched_snippet,
                        "issue": rule["issue"],
                        "recommendation": rule["recommendation"],
                    }
                    flagged_claims.append(flag_entry)
                    red_flags.append(f"[{severity.upper()}] {rule['issue']} ('{matched_snippet}')")

                if rule["recommendation"] not in recommendations:
                    recommendations.append(rule["recommendation"])

                # Penalize confidence based on severity
                if severity == "critical":
                    confidence -= 0.40
                    has_critical = True
                elif severity == "high":
                    confidence -= 0.20
                elif severity == "medium":
                    confidence -= 0.08

        confidence = max(0.0, min(1.0, round(confidence, 2)))
        passed = (confidence >= 0.70) and not has_critical

        if passed and not flagged_claims:
            recommendations.append("Script passed all primary fact-checking and regulatory screening rules.")

        return FactCheckResult(
            passed=passed,
            confidence_score=confidence,
            flagged_claims=flagged_claims,
            red_flags=red_flags,
            recommendations=recommendations,
            checked_by="rules",
            details={
                "total_flags": len(flagged_claims),
                "has_critical": has_critical,
                "character_length": len(script_text),
            },
        )

    def _check_with_gemini(self, script_text: str) -> Optional[FactCheckResult]:
        """Perform comprehensive LLM-based fact verification with Gemini.

        Args:
            script_text: Voiceover narration text.

        Returns:
            Optional[FactCheckResult]: Gemini fact-check evaluation, or None if failed.
        """
        if not self._gemini_initialized or genai is None:
            return None

        self._rate_limit()

        prompt = f"""You are a professional scientific and legal fact-checker for educational documentary channels.

Review the following YouTube voiceover script text for factual accuracy, pseudoscience, misleading statistics, and regulatory violations.

SCRIPT TEXT:
\"\"\"
{script_text[:4000]}
\"\"\"

EVALUATION CRITERIA:
1. Scientific Accuracy: Does the text adhere to established scientific consensus?
2. Historical Precision: Are names, dates, and historical events represented accurately?
3. Medical / Health Statements: Does it make unauthorized medical cure claims or instruct patients against treatments?
4. Financial / Investment Statements: Does it promise guaranteed profits or promote get-rich-quick schemes?
5. Legal Statements: Does it propagate pseudolegal doctrines or illegal loophole advice?
6. False Balances & Conspiracies: Does it present debunked conspiracies as legitimate alternative viewpoints?

OUTPUT FORMAT:
Respond with a single valid JSON object with the following schema:
{{
  "passed": true,
  "confidence_score": 0.95,
  "flagged_claims": [
    {{
      "claim": "Specific quote from script",
      "category": "science|history|medical|financial|legal|sensationalism",
      "severity": "critical|high|medium|low",
      "explanation": "Why this statement is scientifically inaccurate or problematic",
      "correction": "Recommended replacement text or framing"
    }}
  ],
  "red_flags": [
    "Summary string of any serious red flag found"
  ],
  "recommendations": [
    "Concrete actionable editorial revision for the writer"
  ]
}}

Return ONLY the raw JSON object. Do not include markdown formatting or commentary."""

        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config={"temperature": 0.2, "max_output_tokens": 2048},
            )
            response = model.generate_content(prompt)
            raw_text = response.text.strip()

            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
                raw_text = re.sub(r"\n?```$", "", raw_text).strip()

            data = safe_json_loads(raw_text)
            if not isinstance(data, dict):
                return None

            passed = bool(data.get("passed", True))
            confidence = float(data.get("confidence_score", 0.9))
            flagged = data.get("flagged_claims", [])
            red_flags = data.get("red_flags", [])
            recommendations = data.get("recommendations", [])

            return FactCheckResult(
                passed=passed,
                confidence_score=max(0.0, min(1.0, round(confidence, 2))),
                flagged_claims=flagged if isinstance(flagged, list) else [],
                red_flags=red_flags if isinstance(red_flags, list) else [],
                recommendations=recommendations if isinstance(recommendations, list) else [],
                checked_by="gemini",
                details={"model": self.model_name},
            )

        except Exception as exc:
            logger.warning("Gemini fact checking error: %s. Using rule-based fallback.", exc)
            return None

    def check_script(self, script_text: str) -> FactCheckResult:
        """Validate script text for factual accuracy, regulatory violations, and misinformation.

        Combines local pattern rules with Gemini AI validation if available.

        Args:
            script_text: Voiceover narration text.

        Returns:
            FactCheckResult: Detailed analysis with passed flag, confidence score, and recommendations.
        """
        if not script_text or not script_text.strip():
            return FactCheckResult(
                passed=False,
                confidence_score=0.0,
                red_flags=["Empty or null script content."],
                recommendations=["Provide non-empty script narration for validation."],
                checked_by="rules",
            )

        # Step 1: Run local rule validation
        rule_result = self._check_with_rules(script_text)

        # Step 2: Attempt Gemini validation if configured
        gemini_result: Optional[FactCheckResult] = None
        if self._gemini_initialized:
            gemini_result = self._check_with_gemini(script_text)

        # If Gemini succeeded, synthesize hybrid result
        if gemini_result:
            # Combine flagged claims (prioritizing rule criticals)
            combined_flags = list(rule_result.flagged_claims)
            combined_red_flags = list(rule_result.red_flags)
            combined_recs = list(rule_result.recommendations)

            for g_flag in gemini_result.flagged_claims:
                if isinstance(g_flag, dict):
                    combined_flags.append(g_flag)

            for rf in gemini_result.red_flags:
                if rf not in combined_red_flags:
                    combined_red_flags.append(rf)

            for rec in gemini_result.recommendations:
                if rec not in combined_recs:
                    combined_recs.append(rec)

            # Harmonic mean / minimum confidence for safety
            combined_confidence = min(rule_result.confidence_score, gemini_result.confidence_score)
            passed = rule_result.passed and gemini_result.passed and (combined_confidence >= 0.70)

            return FactCheckResult(
                passed=passed,
                confidence_score=round(combined_confidence, 2),
                flagged_claims=combined_flags,
                red_flags=combined_red_flags,
                recommendations=combined_recs,
                checked_by="hybrid",
                details={
                    "rule_score": rule_result.confidence_score,
                    "gemini_score": gemini_result.confidence_score,
                },
            )

        return rule_result
