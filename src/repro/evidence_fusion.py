from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .report import ResultFrame, MONITOR_NAMES

PASS = "PASS"
SOFT_FAIL = "SOFT_FAIL"
HARD_FAIL = "HARD_FAIL"

_DEFAULT_SOFT = 0.3
_DEFAULT_HARD = 0.7

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "timing": 0.3,
    "uds": 0.4,
    "dbc": 0.3,
}


@dataclass
class FusionResult:
    verdict: str
    fusion_score: float
    repro_rate: float
    detail: Dict[str, float]


class EvidenceFusion:
    """
    ResultFrame -> verdict + fusion_score 계산

    mode='binary':
        fusion_score = reproduction_rate

    mode='weight':
        fusion_score = weighted anomaly rate
        = sum((w_i / sum_w) * anomaly_rate_i)

    mode='score':
        fusion_score = weighted mean score
        = sum((w_i / sum_w) * mean_score_i)
        (비교/실험용)
    """

    def __init__(
        self,
        mode: str = "binary",
        soft_threshold: float = _DEFAULT_SOFT,
        hard_threshold: float = _DEFAULT_HARD,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        if mode not in ("binary", "weight", "score"):
            raise ValueError(
                f"mode는 'binary', 'weight', 'score' 중 하나여야 합니다. got={mode!r}"
            )
        if soft_threshold >= hard_threshold:
            raise ValueError(
                f"soft_threshold({soft_threshold}) < hard_threshold({hard_threshold}) 이어야 합니다."
            )

        self.mode = mode
        self.soft_threshold = soft_threshold
        self.hard_threshold = hard_threshold
        self.weights = weights or dict(_DEFAULT_WEIGHTS)

    def evaluate(self, frame: ResultFrame) -> FusionResult:
        if self.mode == "binary":
            return self._binary(frame)
        if self.mode == "weight":
            return self._weighted_anomaly(frame)
        return self._weighted_score(frame)

    def _verdict(self, score: float) -> str:
        if score >= self.hard_threshold:
            return HARD_FAIL
        if score >= self.soft_threshold:
            return SOFT_FAIL
        return PASS

    def _summary_obj(self, frame: ResultFrame, name: str):
        return frame.monitor_summary.get(name)

    def _mean_score(self, frame: ResultFrame, name: str) -> float:
        s = self._summary_obj(frame, name)
        if s is None:
            return 0.0
        return s.mean_score if hasattr(s, "mean_score") else float(s.get("mean_score", 0.0))

    def _fail_count(self, frame: ResultFrame, name: str) -> int:
        s = self._summary_obj(frame, name)
        if s is None:
            return 0
        return s.fail_count if hasattr(s, "fail_count") else int(s.get("fail_count", 0))

    def _anomaly_rate(self, frame: ResultFrame, name: str) -> float:
        trials = max(int(frame.trials), 1)
        return round(self._fail_count(frame, name) / trials, 4)

    def _binary(self, frame: ResultFrame) -> FusionResult:
        rr = frame.reproduction_rate
        detail = {name: self._anomaly_rate(frame, name) for name in MONITOR_NAMES}
        return FusionResult(
            verdict=self._verdict(rr),
            fusion_score=round(rr, 4),
            repro_rate=rr,
            detail=detail,
        )

    def _weighted_anomaly(self, frame: ResultFrame) -> FusionResult:
        total_w = sum(self.weights.get(m, 0.0) for m in MONITOR_NAMES)
        if total_w <= 0:
            raise ValueError("weights 합이 0 이하입니다.")

        detail: Dict[str, float] = {}
        score = 0.0

        for name in MONITOR_NAMES:
            w = self.weights.get(name, 0.0)
            anomaly_rate = self._anomaly_rate(frame, name)
            contrib = (w / total_w) * anomaly_rate
            detail[name] = round(contrib, 4)
            score += contrib

        score = round(min(score, 1.0), 4)
        return FusionResult(
            verdict=self._verdict(score),
            fusion_score=score,
            repro_rate=frame.reproduction_rate,
            detail=detail,
        )

    def _weighted_score(self, frame: ResultFrame) -> FusionResult:
        total_w = sum(self.weights.get(m, 0.0) for m in MONITOR_NAMES)
        if total_w <= 0:
            raise ValueError("weights 합이 0 이하입니다.")

        detail: Dict[str, float] = {}
        score = 0.0

        for name in MONITOR_NAMES:
            w = self.weights.get(name, 0.0)
            mean_score = self._mean_score(frame, name)
            contrib = (w / total_w) * mean_score
            detail[name] = round(contrib, 4)
            score += contrib

        score = round(min(score, 1.0), 4)
        return FusionResult(
            verdict=self._verdict(score),
            fusion_score=score,
            repro_rate=frame.reproduction_rate,
            detail=detail,
        )