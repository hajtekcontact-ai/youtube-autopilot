"""Adaptive Content Strategy Module.

Implements a Multi-Armed Bandit (MAB) with Thompson Sampling for autonomous
optimization of content strategy variants (topics, lengths, posting times,
hook styles, visual styles). Enforces bounded experimentation (max 20% deviation),
rigorous performance evaluations, explainable decisions in Arabic and English,
and strictly prohibits video re-uploads or artificial engagement.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.getenv("DATABASE_URL", "data/autopilot.db").replace("sqlite:///", "")

# Arm definitions across 5 strategic dimensions
STRATEGY_DIMENSIONS: Dict[str, Dict[str, str]] = {
    "topic_category": {
        "tech_innovations": "ابتكارات تكنولوجية ثورية",
        "science_mysteries": "أسرار وحقائق علمية غامضة",
        "ai_future": "مستقبل وتطبيقات الذكاء الاصطناعي",
        "productivity_hacks": "حيل تسريع الإنتاجية الرقمية",
        "space_discoveries": "اكتشافات وظواهر الفضاء السحيق",
        "cybersecurity_tips": "أسرار الحماية والأمن الرقمي",
    },
    "video_length": {
        "short_30s": "شورتس سريع (30 ثانية)",
        "short_45s": "شورتس متوازن (45 ثانية)",
        "short_60s": "شورتس مكثف (60 ثانية)",
        "long_8m": "فيديو طويل معمق (8 دقائق)",
        "long_12m": "فيديو وثائقي شامل (12 دقيقة)",
    },
    "posting_time": {
        "09:00": "الفترة الصباحية (09:00 ص)",
        "12:00": "فترة منتصف اليوم (12:00 ظ)",
        "15:00": "فترة ما بعد الظهر (03:00 م)",
        "18:00": "فترة ذروة المساء (06:00 م)",
        "21:00": "فترة السهرة والليل (09:00 م)",
    },
    "hook_style": {
        "curiosity_gap": "فجوة الفضول غير المحلولة",
        "shocking_fact": "حقيقة غير متوقعة أو صادمة",
        "question_hook": "سؤال استفزازي مباشر للجمهور",
        "bold_statement": "تصريح قوي ومثير للجدل المعرفي",
        "story_teaser": "لقطة بداية قصة درامية مشوقة",
    },
    "visual_style": {
        "hyper_dynamic": "إيقاع بصري سريع وحركي مكثف",
        "cinematic_dark": "أسلوب سينمائي داكن وفخم",
        "minimalist_tech": "تصميم تقني مبسط وعصري",
        "neon_future": "ألوان نيون مستقبلية متوهجة",
        "educational_diagram": "رسوم بيانية ومخططات بصرية توضيحية",
    },
}

# Ethical Safety Invariants
STRICT_SAFETY_POLICY = {
    "allow_video_deletion": False,
    "allow_video_reupload": False,
    "allow_synthetic_engagement": False,
    "min_videos_for_conclusions": 10,
    "max_exploration_rate": 0.20,  # Max 20% deviation from proven strategies
}


@dataclass
class StrategyRecommendation:
    """Represents an actionable content strategy recommendation.

    Attributes:
        experiment_id: Unique UUID tracking this strategy decision.
        topic_category: Selected content niche arm.
        video_length: Selected target duration arm.
        posting_time: Selected schedule time arm.
        hook_style: Selected hook technique arm.
        visual_style: Selected visual aesthetic arm.
        is_exploration: True if picked to explore; False if exploiting top performer.
        rationale_en: English engineering explanation of the decision.
        rationale_ar: Arabic user-facing explanation for the dashboard.
        confidence_score: Bayesian confidence level (0.0 - 1.0).
        arm_scores: Estimated expected rewards for chosen arms.
        created_at: ISO timestamp of creation.
    """

    experiment_id: str
    topic_category: str
    video_length: str
    posting_time: str
    hook_style: str
    visual_style: str
    is_exploration: bool
    rationale_en: str
    rationale_ar: str
    confidence_score: float
    arm_scores: Dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert recommendation to dictionary."""
        return asdict(self)


@dataclass
class EvaluationResult:
    """Represents a comprehensive performance evaluation of a published video.

    Attributes:
        video_id: Internal video identifier.
        performance_tier: Tier rating ('exceptional', 'strong', 'average', 'underperforming', 'insufficient_data').
        composite_score: Normalized reward score (0.0 - 1.0).
        metrics: Raw observed performance metrics.
        strengths: English identified strengths.
        strengths_ar: Arabic identified strengths.
        weaknesses: English identified weaknesses.
        weaknesses_ar: Arabic identified weaknesses.
        causes_ar: Arabic root-cause diagnostic strings.
        actionable_recommendations_ar: Arabic concrete recommendations for future videos.
        follow_up_suggestions_ar: Arabic follow-up ideas (Part 2, topic extensions).
        summary_ar: Arabic dashboard summary.
        summary_en: English technical summary.
        evaluated_at: ISO timestamp of evaluation.
    """

    video_id: str
    performance_tier: str
    composite_score: float
    metrics: Dict[str, Any]
    strengths: List[str] = field(default_factory=list)
    strengths_ar: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    weaknesses_ar: List[str] = field(default_factory=list)
    causes_ar: List[str] = field(default_factory=list)
    actionable_recommendations_ar: List[str] = field(default_factory=list)
    follow_up_suggestions_ar: List[str] = field(default_factory=list)
    summary_ar: str = ""
    summary_en: str = ""
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert evaluation result to dictionary."""
        return asdict(self)


class AdaptiveStrategy:
    """Multi-Armed Bandit strategy engine utilizing Thompson Sampling.

    Optimizes content dimensions (topics, durations, posting slots, hooks, styles)
    based on normalized composite feedback (views, watch time, CTR, subscriber gains).
    Ensures bounded exploration (max 20% deviation) and enforces strict safety principles.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialize AdaptiveStrategy.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtain thread-safe SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Initialize database tables for experiments and Thompson Sampling priors."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT UNIQUE NOT NULL,
                    video_id TEXT,
                    topic_category TEXT NOT NULL,
                    video_length TEXT NOT NULL,
                    posting_time TEXT NOT NULL,
                    hook_style TEXT NOT NULL,
                    visual_style TEXT NOT NULL,
                    is_exploration INTEGER NOT NULL DEFAULT 0,
                    rationale_en TEXT,
                    rationale_ar TEXT,
                    views INTEGER DEFAULT 0,
                    watch_time REAL DEFAULT 0.0,
                    ctr REAL DEFAULT 0.0,
                    subscribers_gained INTEGER DEFAULT 0,
                    composite_reward REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bandit_arms_state (
                    dimension TEXT NOT NULL,
                    arm_value TEXT NOT NULL,
                    alpha REAL NOT NULL DEFAULT 1.0,
                    beta REAL NOT NULL DEFAULT 1.0,
                    trials INTEGER NOT NULL DEFAULT 0,
                    successes REAL NOT NULL DEFAULT 0.0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (dimension, arm_value)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    youtube_video_id TEXT NOT NULL,
                    window TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    estimated_minutes_watched REAL NOT NULL DEFAULT 0.0,
                    average_view_duration REAL NOT NULL DEFAULT 0.0,
                    subscribers_gained INTEGER NOT NULL DEFAULT 0,
                    subscribers_lost INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    dislikes INTEGER NOT NULL DEFAULT 0,
                    shares INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    impressions INTEGER NOT NULL DEFAULT 0,
                    impression_ctr REAL NOT NULL DEFAULT 0.0,
                    annotation_ctr REAL NOT NULL DEFAULT 0.0,
                    raw_data TEXT,
                    source TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_video_id ON experiment_results(video_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_status ON experiment_results(status);")

            # Seed default bandit priors for all arms if empty
            for dim, arms in STRATEGY_DIMENSIONS.items():
                for arm_key in arms.keys():
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO bandit_arms_state (dimension, arm_value, alpha, beta, trials, successes)
                        VALUES (?, ?, 1.0, 1.0, 0, 0.0);
                        """,
                        (dim, arm_key),
                    )
            conn.commit()

    def _get_total_completed_experiments(self) -> int:
        """Count total completed experiments to check minimum sample threshold."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM experiment_results WHERE status = 'completed'")
            row = cur.fetchone()
            return row["cnt"] if row else 0

    def calculate_composite_score(
        self,
        views: int,
        watch_time: float,
        ctr: float,
        subscribers_gained: int,
        expected_views: float = 1000.0,
        expected_watch_min: float = 300.0,
    ) -> float:
        """Compute normalized composite reward signal in range [0.0, 1.0].

        Weights:
            - Views (0.25): Reach and audience discovery
            - Watch Time (0.35): Viewer engagement and algorithm retention signal
            - Click-Through Rate (0.25): Title and thumbnail hook effectiveness
            - Subscribers Gained (0.15): High-intent audience conversion

        Args:
            views: Observed view count.
            watch_time: Observed total watch time in minutes.
            ctr: Observed impression click-through rate percentage (e.g. 6.5 for 6.5%).
            subscribers_gained: Net subscribers gained.
            expected_views: Benchmark view target for normalization.
            expected_watch_min: Benchmark watch minutes for normalization.

        Returns:
            Composite reward float between 0.0 and 1.0.
        """
        # Sub-score normalizations (sigmoid-like / clamped linear)
        s_views = min(1.0, max(0.0, views / max(1.0, expected_views)))
        s_watch = min(1.0, max(0.0, watch_time / max(1.0, expected_watch_min)))
        # CTR target: 10% = 1.0 score, 5% = 0.5 score
        s_ctr = min(1.0, max(0.0, ctr / 10.0))
        # Subs target: 10 subs per video = 1.0
        s_subs = min(1.0, max(0.0, subscribers_gained / 10.0))

        composite = (0.25 * s_views) + (0.35 * s_watch) + (0.25 * s_ctr) + (0.15 * s_subs)
        return float(np.clip(composite, 0.0, 1.0))

    def _sample_arm(
        self, dimension: str, explore: bool
    ) -> Tuple[str, float, float]:
        """Select an arm for a given dimension using Thompson Sampling with bounded exploration.

        Args:
            dimension: Strategic dimension name.
            explore: Whether exploration mode is active for this selection.

        Returns:
            Tuple of (selected_arm_key, estimated_score, confidence).
        """
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT arm_value, alpha, beta, trials FROM bandit_arms_state WHERE dimension = ?",
                (dimension,),
            )
            rows = cur.fetchall()

        if not rows:
            # Fallback to first arm in defined dimension
            first_arm = next(iter(STRATEGY_DIMENSIONS[dimension]))
            return first_arm, 0.5, 0.0

        arm_stats: List[Dict[str, Any]] = []
        for r in rows:
            arm_val = r["arm_value"]
            alpha = float(r["alpha"])
            beta = float(r["beta"])
            trials = int(r["trials"])
            # Thompson sample from Beta(alpha, beta)
            sample_val = float(np.random.beta(max(0.1, alpha), max(0.1, beta)))
            mean_val = alpha / (alpha + beta)
            # Confidence grows with sample size (asymptotic to 1.0)
            confidence = min(1.0, trials / 20.0)
            arm_stats.append({
                "arm_value": arm_val,
                "sample": sample_val,
                "mean": mean_val,
                "trials": trials,
                "confidence": confidence,
            })

        if explore:
            # Exploration mode: prioritize underexplored arms or highest upper-credible bound
            # Pick from least tested arms or highest Thompson sample
            arm_stats.sort(key=lambda x: (x["trials"], -x["sample"]))
            chosen = arm_stats[0]
        else:
            # Exploitation mode (80%): pick proven top performer by Thompson sample
            arm_stats.sort(key=lambda x: -x["sample"])
            chosen = arm_stats[0]

        return chosen["arm_value"], chosen["sample"], chosen["confidence"]

    def select_strategy(self) -> StrategyRecommendation:
        """Select an optimal content strategy using Thompson Sampling.

        Enforces bounded experimentation: at most 20% chance of exploratory
        diversification, with 80% exploitation of highest-performing arms.

        Returns:
            StrategyRecommendation with selected arms, explainable rationale, and Arabic UI text.
        """
        total_completed = self._get_total_completed_experiments()
        is_cold_start = total_completed < STRICT_SAFETY_POLICY["min_videos_for_conclusions"]

        # Exploration vs Exploitation decision: Max 20% deviation
        uniform_draw = np.random.uniform(0.0, 1.0)
        is_exploration = bool(uniform_draw < STRICT_SAFETY_POLICY["max_exploration_rate"]) or is_cold_start

        experiment_id = f"exp_{uuid.uuid4().hex[:12]}"
        selected_arms: Dict[str, str] = {}
        arm_scores: Dict[str, float] = {}
        confidences: List[float] = []

        for dim in ["topic_category", "video_length", "posting_time", "hook_style", "visual_style"]:
            arm_key, score, conf = self._sample_arm(dim, explore=is_exploration)
            selected_arms[dim] = arm_key
            arm_scores[dim] = round(score, 3)
            confidences.append(conf)

        overall_confidence = round(float(np.mean(confidences)), 2)

        # Generate Explainable Rationale
        topic_label_ar = STRATEGY_DIMENSIONS["topic_category"].get(selected_arms["topic_category"], selected_arms["topic_category"])
        len_label_ar = STRATEGY_DIMENSIONS["video_length"].get(selected_arms["video_length"], selected_arms["video_length"])
        time_label_ar = STRATEGY_DIMENSIONS["posting_time"].get(selected_arms["posting_time"], selected_arms["posting_time"])
        hook_label_ar = STRATEGY_DIMENSIONS["hook_style"].get(selected_arms["hook_style"], selected_arms["hook_style"])
        visual_label_ar = STRATEGY_DIMENSIONS["visual_style"].get(selected_arms["visual_style"], selected_arms["visual_style"])

        if is_cold_start:
            mode_desc = "Cold Start Initial Gathering"
            rationale_en = (
                f"Initial data gathering phase ({total_completed}/10 videos). "
                f"Equally distributing trials across {selected_arms['topic_category']} "
                f"and {selected_arms['hook_style']} to establish statistical priors."
            )
            rationale_ar = (
                f"مرحلة الاستكشاف الأولي وجمع البيانات ({total_completed}/10 فيديوهات). "
                f"يتم اختبار خوارزمية '{topic_label_ar}' مع أسلوب '{hook_label_ar}' "
                f"لإرساء المعايير الإحصائية المرجعية للنظام."
            )
        elif is_exploration:
            mode_desc = "Bounded Exploration (≤20% deviation)"
            rationale_en = (
                f"Exploration step ({round(uniform_draw, 2)} < 0.20 threshold). "
                f"Testing variant: {selected_arms['topic_category']} with {selected_arms['hook_style']} "
                f"to discover emergent audience preferences without harming channel momentum."
            )
            rationale_ar = (
                f"استكشاف منضبط (أقل من 20% حيود). تجربة مسار واعد: '{topic_label_ar}' "
                f"بأسلوب خطاف '{hook_label_ar}' وقالب بصري '{visual_label_ar}' "
                f"لاكتشاف فرص جماهيرية جديدة."
            )
        else:
            mode_desc = "Thompson Sampling Exploitation (80%)"
            rationale_en = (
                f"Exploiting proven high-yield strategy. Arm scores: "
                f"Topic={arm_scores['topic_category']}, Hook={arm_scores['hook_style']}. "
                f"High confidence ({overall_confidence}) based on superior retention and CTR."
            )
            rationale_ar = (
                f"تطبيق الاستراتيجية الأكثر كفاءة وموثوقية إحصائياً. المسار: '{topic_label_ar}' "
                f"بمدة '{len_label_ar}' وتوقيت '{time_label_ar}' "
                f"مع أسلوب '{hook_label_ar}'. درجة الثقة الإحصائية: {int(overall_confidence * 100)}%."
            )

        logger.info(
            "Selected strategy [%s]: %s (exploration=%s, confidence=%.2f)",
            experiment_id, mode_desc, is_exploration, overall_confidence
        )

        # Persist experiment record
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO experiment_results (
                    experiment_id, topic_category, video_length, posting_time,
                    hook_style, visual_style, is_exploration, rationale_en,
                    rationale_ar, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending');
                """,
                (
                    experiment_id,
                    selected_arms["topic_category"],
                    selected_arms["video_length"],
                    selected_arms["posting_time"],
                    selected_arms["hook_style"],
                    selected_arms["visual_style"],
                    1 if is_exploration else 0,
                    rationale_en,
                    rationale_ar,
                ),
            )
            conn.commit()

        return StrategyRecommendation(
            experiment_id=experiment_id,
            topic_category=selected_arms["topic_category"],
            video_length=selected_arms["video_length"],
            posting_time=selected_arms["posting_time"],
            hook_style=selected_arms["hook_style"],
            visual_style=selected_arms["visual_style"],
            is_exploration=is_exploration,
            rationale_en=rationale_en,
            rationale_ar=rationale_ar,
            confidence_score=overall_confidence,
            arm_scores=arm_scores,
        )

    def update_reward(self, experiment_id: str, metrics: Dict[str, Any]) -> None:
        """Update Bayesian bandit state and experiment record with observed performance metrics.

        Args:
            experiment_id: Unique experiment identifier returned by select_strategy().
            metrics: Dictionary containing 'views', 'watch_time' (or 'estimated_minutes_watched'),
                     'ctr' (or 'impression_ctr'), and 'subscribers_gained'.
        """
        views = int(metrics.get("views", 0))
        watch_time = float(metrics.get("watch_time", metrics.get("estimated_minutes_watched", 0.0)))
        ctr = float(metrics.get("ctr", metrics.get("impression_ctr", 0.0)))
        subs_gained = int(metrics.get("subscribers_gained", 0))

        composite_reward = self.calculate_composite_score(
            views=views,
            watch_time=watch_time,
            ctr=ctr,
            subscribers_gained=subs_gained,
        )

        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM experiment_results WHERE experiment_id = ?",
                (experiment_id,),
            )
            exp_row = cur.fetchone()
            if not exp_row:
                logger.error("Experiment ID %s not found. Cannot update reward.", experiment_id)
                return

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE experiment_results
                SET views = ?, watch_time = ?, ctr = ?, subscribers_gained = ?,
                    composite_reward = ?, status = 'completed', updated_at = ?
                WHERE experiment_id = ?;
                """,
                (views, watch_time, ctr, subs_gained, composite_reward, now_iso, experiment_id),
            )

            # Update Beta distributions for each arm used in this experiment
            # Pseudo-success = composite_reward, pseudo-failure = (1.0 - composite_reward)
            dimensions = [
                ("topic_category", exp_row["topic_category"]),
                ("video_length", exp_row["video_length"]),
                ("posting_time", exp_row["posting_time"]),
                ("hook_style", exp_row["hook_style"]),
                ("visual_style", exp_row["visual_style"]),
            ]

            for dim_name, arm_val in dimensions:
                conn.execute(
                    """
                    UPDATE bandit_arms_state
                    SET alpha = alpha + ?,
                        beta = beta + ?,
                        trials = trials + 1,
                        successes = successes + ?,
                        last_updated = ?
                    WHERE dimension = ? AND arm_value = ?;
                    """,
                    (
                        composite_reward,
                        1.0 - composite_reward,
                        composite_reward,
                        now_iso,
                        dim_name,
                        arm_val,
                    ),
                )
            conn.commit()

        logger.info(
            "Updated reward for experiment %s: score=%.3f (views=%d, watch=%.1f, ctr=%.2f%%)",
            experiment_id, composite_reward, views, watch_time, ctr
        )

    def evaluate_video(self, video_id: str) -> EvaluationResult:
        """Evaluate a specific video's performance, diagnostics, and next steps.

        Analyzes root causes when content underperforms, identifies success factors
        when content excels, and generates actionable Arabic/English guidance.

        Args:
            video_id: Internal video identifier.

        Returns:
            EvaluationResult detailing performance tier, strengths, weaknesses,
            Arabic causes analysis, and concrete follow-up suggestions.
        """
        # Retrieve snapshot metrics from database
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM analytics_snapshots
                WHERE video_id = ?
                ORDER BY created_at DESC LIMIT 1;
                """,
                (video_id,),
            )
            snapshot_row = cur.fetchone()

            cur_exp = conn.execute(
                "SELECT * FROM experiment_results WHERE video_id = ? LIMIT 1;",
                (video_id,),
            )
            exp_row = cur_exp.fetchone()

        if not snapshot_row and not exp_row:
            return EvaluationResult(
                video_id=video_id,
                performance_tier="insufficient_data",
                composite_score=0.0,
                metrics={},
                summary_ar="لا تتوفر بيانات أداء كافية لهذا الفيديو حتى الآن. يُرجى انتظار نافذة التقييم الأولى (48 ساعة).",
                summary_en="Insufficient analytics data. Awaiting initial 48h ingestion window.",
            )

        views = int(snapshot_row["views"] if snapshot_row else exp_row["views"])
        watch_time = float(snapshot_row["estimated_minutes_watched"] if snapshot_row else exp_row["watch_time"])
        ctr = float(snapshot_row["impression_ctr"] if snapshot_row else exp_row["ctr"])
        subs = int(snapshot_row["subscribers_gained"] if snapshot_row else exp_row["subscribers_gained"])
        avg_duration = float(snapshot_row["average_view_duration"]) if snapshot_row else 0.0

        composite_score = self.calculate_composite_score(views, watch_time, ctr, subs)

        strengths: List[str] = []
        strengths_ar: List[str] = []
        weaknesses: List[str] = []
        weaknesses_ar: List[str] = []
        causes_ar: List[str] = []
        recommendations_ar: List[str] = []
        follow_ups_ar: List[str] = []

        # Diagnostic Analysis
        if ctr >= 7.0:
            strengths.append(f"High CTR ({ctr:.1f}%) demonstrates compelling thumbnail and title.")
            strengths_ar.append(f"نسبة نقر ممتازة ({ctr:.1f}%): العنوان والصورة المصغرة نجحا بامتياز في إثارة الفضول.")
        elif ctr < 3.5:
            weaknesses.append(f"Low CTR ({ctr:.1f}%) suggests weak visual framing or unappealing title.")
            weaknesses_ar.append(f"نسبة نقر منخفضة ({ctr:.1f}%): العنوان أو الصورة المصغرة لم تجذب اهتمام الجمهور في صفحة الاقتراحات.")
            causes_ar.append("ضعف التباين البصري في الصورة المصغرة أو غياب عنصر الإثارة/الفضول المباشر في الكلمات الأولى من العنوان.")
            recommendations_ar.append("إعادة صياغة استراتيجية العناوين القادمة لتعتمد على أسلوب السؤال الاستفزازي أو فجوة الفضول (Curiosity Gap)، وزيادة وضوح النص في الصورة المصغرة بنسبة تباين لا تقل عن 70%.")

        if avg_duration >= 45.0 or (watch_time / max(1, views) >= 0.7):
            strengths.append("Exceptional audience retention and narrative pacing.")
            strengths_ar.append("معدل استبقاء جماهيري ممتاز: إيقاع الفيديو والسرد البصري حافظا على تركيز المشاهدين.")
        elif avg_duration < 20.0 and views > 20:
            weaknesses.append("High early drop-off indicates hook failure or sluggish pacing.")
            weaknesses_ar.append("انخفاض متوسط وقت المشاهدة: حدوث ارتداد مبكر في الثواني الأولى من الفيديو.")
            causes_ar.append("الخطاف الافتتاحي (Hook) استغرق أكثر من 3 ثوانٍ للوصول إلى النقطة المحورية، أو وجود مقدمة تقليدية بطيئة أدت لمغادرة المشاهدين فوراً.")
            recommendations_ar.append("قص أي مقدمات تقليدية والدخول مباشرة في قلب الحدث خلال أول 1.5 ثانية باستخدام جملة صادمة أو تأثير بصري حركي سريع.")

        if subs >= 5:
            strengths.append("High subscription conversion rate indicates high perceived value.")
            strengths_ar.append("معدل تحويل اشتراكات مرتفع: المحتوى نجح في إقناع المشاهد بالقيمة المستقبلية للقناة.")
            follow_ups_ar.append("إنتاج جزء ثانٍ (Part 2) مكمل لنفس الفكرة أو سلسلة مرتبطة بهذا الموضوع الناجح.")

        # Performance Tier Classification
        if composite_score >= 0.75:
            tier = "exceptional"
            summary_ar = f"أداء استثنائي متفوق (التقييم: {composite_score:.2f}/1.0). حقق الفيديو أرقاماً قياسية في التفاعل والاستبقاء."
            summary_en = f"Exceptional performance (Score: {composite_score:.2f}). Exceeded top channel benchmarks."
            follow_ups_ar.append("تثبيت هذا النمط البصري ونبرة الخطاف كقالب أساسي لمقاطع الأسبوع القادم.")
        elif composite_score >= 0.50:
            tier = "strong"
            summary_ar = f"أداء قوي ومتوازن (التقييم: {composite_score:.2f}/1.0). تفاعل إيجابي مع مؤشرات نمو مستقرة."
            summary_en = f"Strong and stable performance (Score: {composite_score:.2f})."
        elif composite_score >= 0.30:
            tier = "average"
            summary_ar = f"أداء متوسط (التقييم: {composite_score:.2f}/1.0). يحتاج الفيديو إلى ضبط معدل النقر وسرعة الإيقاع."
            summary_en = f"Average performance (Score: {composite_score:.2f}). Room for optimization in CTR and retention."
        else:
            tier = "underperforming"
            summary_ar = f"أداء دون المأمول (التقييم: {composite_score:.2f}/1.0). تم رصد ضعف في الخطاف الأولي ومعدل الاكتشاف."
            summary_en = f"Underperforming (Score: {composite_score:.2f}). Root cause analysis suggests hook and thumbnail revamp."

        # Safety Policy Verification
        # Note: Autopilot policy strictly forbids deleting or re-uploading videos
        recommendations_ar.append("ملاحظة نظام الأمان: يُمنع نهائياً حذف الفيديو أو إعادة رفعه؛ يتم التعلم من الأخطاء وتطبيق التحسينات على الفيديو القادم حصراً.")

        return EvaluationResult(
            video_id=video_id,
            performance_tier=tier,
            composite_score=round(composite_score, 3),
            metrics={
                "views": views,
                "watch_time_minutes": watch_time,
                "ctr_pct": ctr,
                "subscribers_gained": subs,
                "average_view_duration": avg_duration,
            },
            strengths=strengths,
            strengths_ar=strengths_ar,
            weaknesses=weaknesses,
            weaknesses_ar=weaknesses_ar,
            causes_ar=causes_ar,
            actionable_recommendations_ar=recommendations_ar,
            follow_up_suggestions_ar=follow_ups_ar,
            summary_ar=summary_ar,
            summary_en=summary_en,
        )

    def get_performance_report(self) -> Dict[str, Any]:
        """Generate an aggregated strategic intelligence report for dashboard display.

        Returns:
            Dictionary containing arm win rates, exploration statistics, top-performing
            variants, and bilingual summaries.
        """
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM bandit_arms_state ORDER BY dimension, (alpha / (alpha + beta)) DESC;")
            arm_rows = cur.fetchall()

            cur_exp = conn.execute("SELECT COUNT(*) AS total, SUM(is_exploration) AS explore_count FROM experiment_results;")
            stats_row = cur_exp.fetchone()

            cur_top = conn.execute(
                """
                SELECT * FROM experiment_results
                WHERE status = 'completed'
                ORDER BY composite_reward DESC LIMIT 5;
                """
            )
            top_experiments = cur_top.fetchall()

        total_experiments = stats_row["total"] if stats_row else 0
        exploration_count = stats_row["explore_count"] if stats_row and stats_row["explore_count"] else 0
        exploration_pct = round((exploration_count / max(1, total_experiments)) * 100.0, 1)

        dimensions_report: Dict[str, List[Dict[str, Any]]] = {}
        top_arms_by_dim: Dict[str, str] = {}

        for r in arm_rows:
            dim = r["dimension"]
            arm_val = r["arm_value"]
            alpha = float(r["alpha"])
            beta = float(r["beta"])
            trials = int(r["trials"])
            mean_score = round(alpha / (alpha + beta), 3)

            if dim not in dimensions_report:
                dimensions_report[dim] = []
                top_arms_by_dim[dim] = arm_val

            label_ar = STRATEGY_DIMENSIONS.get(dim, {}).get(arm_val, arm_val)
            dimensions_report[dim].append({
                "arm": arm_val,
                "label_ar": label_ar,
                "expected_reward": mean_score,
                "trials": trials,
                "alpha": round(alpha, 2),
                "beta": round(beta, 2),
            })

        top_experiments_list = []
        for te in top_experiments:
            top_experiments_list.append({
                "experiment_id": te["experiment_id"],
                "video_id": te["video_id"],
                "topic_category": te["topic_category"],
                "hook_style": te["hook_style"],
                "visual_style": te["visual_style"],
                "views": te["views"],
                "composite_reward": te["composite_reward"],
            })

        min_samples_met = total_experiments >= STRICT_SAFETY_POLICY["min_videos_for_conclusions"]

        summary_ar = (
            f"إجمالي التجارب الاستراتيجية: {total_experiments} | "
            f"نسبة الاستكشاف المنضبط: {exploration_pct}% | "
            f"حالة اكتمال العينات الإحصائية: {'مكتملة ومستقرة' if min_samples_met else 'قيد التجميع الأولي (<10 فيديوهات)'}."
        )

        return {
            "total_experiments": total_experiments,
            "exploration_count": exploration_count,
            "exploration_percentage": exploration_pct,
            "min_samples_met": min_samples_met,
            "top_arms_by_dimension": top_arms_by_dim,
            "dimensions": dimensions_report,
            "top_experiments": top_experiments_list,
            "summary_ar": summary_ar,
            "summary_en": (
                f"Total experiments: {total_experiments}. Exploration rate: {exploration_pct}%. "
                f"Statistical maturity: {'Robust' if min_samples_met else 'Cold Start'}."
            ),
        }
