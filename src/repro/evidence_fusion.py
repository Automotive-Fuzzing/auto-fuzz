from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .report import ResultFrame, MONITOR_NAMES

PASS      = "PASS"
SOFT_FAIL = "SOFT_FAIL"
HARD_FAIL = "HARD_FAIL"

_DEFAULT_SOFT = 0.3
_DEFAULT_HARD = 0.7

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "timing": 0.3,
    "uds":    0.4,
    "dbc":    0.3,
}


@dataclass
class FusionResult:
    verdict: str            # PASS / SOFT_FAIL / HARD_FAIL
    fusion_score: float     # 0.0 ~ 1.0
    repro_rate: float
    detail: Dict[str, float]  # monitor별 기여도


class EvidenceFusion:
    """
    ResultFrame -> verdict + fusion_score 계산.

    mode='binary':
        fusion_score = repro_rate
        threshold 비교로 verdict 결정

    mode='weight':
        fusion_score = sum((w_i / sum_w) * mean_score_i)
        threshold 비교로 verdict 결정
    """

    def __init__(
        self,
        mode: str = "binary",
        soft_threshold: float = _DEFAULT_SOFT,
        hard_threshold: float = _DEFAULT_HARD,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        if mode not in ("binary", "weight"):
            raise ValueError(f"mode는 'binary' 또는 'weight' 중 하나여야 합니다. got={mode!r}")
        if soft_threshold >= hard_threshold:
            raise ValueError(
                f"soft_threshold({soft_threshold}) < hard_threshold({hard_threshold}) 이어야 합니다."
            )
        self.mode = mode
        self.soft_threshold = soft_threshold
        self.hard_threshold = hard_threshold
        self.weights = weights or dict(_DEFAULT_WEIGHTS)

    # ------------------------------------------------------------------

    def evaluate(self, frame: ResultFrame) -> FusionResult:
        if self.mode == "binary":
            return self._binary(frame)
        return self._weighted(frame)

    # ------------------------------------------------------------------

    def _verdict(self, score: float) -> str:
        if score >= self.hard_threshold:
            return HARD_FAIL
        if score >= self.soft_threshold:
            return SOFT_FAIL
        return PASS

    def _mean(self, frame: ResultFrame, name: str) -> float:
        """MonitorSummary 인스턴스 또는 dict 모두 처리"""
        s = frame.monitor_summary.get(name)
        if s is None:
            return 0.0
        return s.mean_score if hasattr(s, "mean_score") else float(s.get("mean_score", 0.0))

    def _binary(self, frame: ResultFrame) -> FusionResult:
        rr = frame.reproduction_rate
        detail = {name: self._mean(frame, name) for name in MONITOR_NAMES}
        return FusionResult(
            verdict=self._verdict(rr),
            fusion_score=round(rr, 4),
            repro_rate=rr,
            detail=detail,
        )

    def _weighted(self, frame: ResultFrame) -> FusionResult:
        total_w = sum(self.weights.get(m, 0.0) for m in MONITOR_NAMES)
        if total_w <= 0:
            raise ValueError("weights 합이 0 이하입니다.")

        detail: Dict[str, float] = {}
        score = 0.0
        for name in MONITOR_NAMES:
            w = self.weights.get(name, 0.0)
            contrib = (w / total_w) * self._mean(frame, name)
            detail[name] = round(contrib, 4)
            score += contrib

        score = round(min(score, 1.0), 4)
        return FusionResult(
            verdict=self._verdict(score),
            fusion_score=score,
            repro_rate=frame.reproduction_rate,
            detail=detail,
        )
