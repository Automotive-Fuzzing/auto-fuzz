from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

MONITOR_NAMES: tuple[str, ...] = ("timing", "uds", "dbc")

_FAIL_STATUSES = frozenset({"crashed", "timeout"})


@dataclass
class MonitorSummary:
    name: str
    fail_count: int
    fail_bitmap: List[int]
    scores: List[float]
    mean_score: float


@dataclass
class ResultFrame:
    seed_id: int
    trials: int
    reproduced: int
    reproduction_rate: float
    monitor_summary: Dict[str, MonitorSummary]
    trial_results: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "trials": self.trials,
            "reproduced": self.reproduced,
            "reproduction_rate": self.reproduction_rate,
            "monitor_summary": {
                name: asdict(s) if isinstance(s, MonitorSummary) else dict(s)
                for name, s in self.monitor_summary.items()
            },
            "trial_results": self.trial_results,
        }

    @staticmethod
    def _trial_failed(mv: Dict[str, Any]) -> bool:
        status = str(mv.get("status", "ok")).lower()
        if status in _FAIL_STATUSES:
            return True

        # 모니터가 직접 anomaly 여부를 넘겨주면 그것을 신뢰
        if "is_anomalous" in mv:
            return bool(mv.get("is_anomalous", False))

        # is_anomalous가 없으면 score 기반 fallback
        return float(mv.get("score", 0.0)) > 0.0

    @staticmethod
    def from_trial_results(
        seed_id: int,
        trial_results: List[Dict[str, Any]],
        *,
        top_n: Optional[int] = None,
    ) -> "ResultFrame":

        n = len(trial_results)
        if n == 0:
            return ResultFrame(
                seed_id=seed_id,
                trials=0,
                reproduced=0,
                reproduction_rate=0.0,
                monitor_summary={},
                trial_results=[],
            )

        summaries: Dict[str, MonitorSummary] = {}
        for name in MONITOR_NAMES:
            scores: List[float] = []
            bitmap: List[int] = []

            for tr in trial_results:
                mv = tr.get(name, {})
                score = float(mv.get("score", 0.0))
                scores.append(score)
                bitmap.append(1 if ResultFrame._trial_failed(mv) else 0)

            summaries[name] = MonitorSummary(
                name=name,
                fail_count=sum(bitmap),
                fail_bitmap=bitmap,
                scores=scores,
                mean_score=round(sum(scores) / n, 4),
            )

        reproduced = sum(
            1 for i in range(n)
            if any(summaries[m].fail_bitmap[i] for m in MONITOR_NAMES)
        )

        return ResultFrame(
            seed_id=seed_id,
            trials=n,
            reproduced=reproduced,
            reproduction_rate=round(reproduced / n, 4),
            monitor_summary=summaries,
            trial_results=trial_results if top_n is None else trial_results[:top_n],
        )