from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# 파이프라인 전체에서 monitor 이름 순서 통일
MONITOR_NAMES: tuple[str, ...] = ("timing", "uds", "dbc")

_FAIL_STATUSES = frozenset({"crashed", "timeout"})


@dataclass
class MonitorSummary:
    name: str
    fail_count: int         # 몇 번의 trial에서 fail 판정
    fail_bitmap: List[int]  # 길이 n, trial별 1(fail) / 0(pass)
    scores: List[float]     # trial별 원시 score
    mean_score: float       # 평균 score


@dataclass
class ResultFrame:
    seed_id: int
    trials: int                             # n
    reproduced: int                         # k: 하나 이상의 monitor가 fail한 trial 수
    reproduction_rate: float                # k / n
    monitor_summary: Dict[str, MonitorSummary]
    trial_results: List[Dict[str, Any]]     # per-trial raw (top_n 적용 후)

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
    def from_trial_results(
        seed_id: int,
        trial_results: List[Dict[str, Any]],
        *,
        top_n: Optional[int] = None,
    ) -> "ResultFrame":
        
        n = len(trial_results)
        if n == 0:
            return ResultFrame(
                seed_id=seed_id, trials=0, reproduced=0,
                reproduction_rate=0.0, monitor_summary={}, trial_results=[],
            )

        summaries: Dict[str, MonitorSummary] = {}
        for name in MONITOR_NAMES:
            scores: List[float] = []
            bitmap: List[int] = []

            for tr in trial_results:
                mv = tr.get(name, {})
                score = float(mv.get("score", 0.0))
                status = str(mv.get("status", "ok"))
                scores.append(score)
                bitmap.append(1 if (score > 0.0 or status in _FAIL_STATUSES) else 0)

            summaries[name] = MonitorSummary(
                name=name,
                fail_count=sum(bitmap),
                fail_bitmap=bitmap,
                scores=scores,
                mean_score=round(sum(scores) / n, 4),
            )

        # 하나 이상의 monitor가 fail한 trial 수 (k)
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