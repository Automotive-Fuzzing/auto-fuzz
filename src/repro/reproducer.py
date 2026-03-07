from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from .candidate import CandidateSnapshot
from .evidence_fusion import EvidenceFusion, FusionResult
from .report import ResultFrame
from .storage import ReproStorage


@runtime_checkable
class TxLog(Protocol):

    def get_frame_window(
        self,
        center_idx: int,
        pre: int,
        post: int,
    ) -> List[Tuple[int, bytes]]: ...


class Reproducer:


    def __init__(
        self,
        can_iface,
        monitor_manager,
        storage: ReproStorage,
        fusion: Optional[EvidenceFusion] = None,
        *,
        tx_log: Optional[TxLog] = None,
        pre_window: int = 5,
        post_window: int = 2,
        trial_gap: float = 0.1,
        under_load: bool = False,
    ) -> None:
       
        self.can_iface = can_iface
        self.monitor_manager = monitor_manager
        self.storage = storage
        self.fusion = fusion or EvidenceFusion()
        self.tx_log = tx_log
        self.pre_window = pre_window
        self.post_window = post_window
        self.trial_gap = trial_gap
        self.under_load = under_load

        if under_load:
            print("[WARN] under_load=True: 배경 트래픽 유지는 현재 미구현 (stub)")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        candidate: CandidateSnapshot,
        n: int = 5,
        *,
        timing_timeout: float = 5.0,
        dbc_timeout: float = 5.0,
        top_n: Optional[int] = None,
        report_mode: str = "fixed",
    ) -> Tuple[ResultFrame, FusionResult]:
     
        print(
            f"[Reproducer] seed_id={candidate.id} | n={n} | "
            f"arb_id={hex(candidate.arb_id)} | payload={candidate.payload.hex()}"
        )

        frames = self._restore_frames(candidate)
        print(f"[Reproducer] replay 프레임 수: {len(frames)}")

        trial_results: List[Dict[str, Any]] = []
        for i in range(n):
            print(f"[Reproducer] -- trial {i + 1}/{n} --")

            # replay -> monitor 수집
            self.can_iface.replay(frames)
            result = self.monitor_manager.collect_results(
                timing_timeout=timing_timeout,
                dbc_timeout=dbc_timeout,
            )
            trial_results.append(result)

            if i < n - 1:
                time.sleep(self.trial_gap)

        # 7단계: ResultFrame 생성 + verdict 판정
        frame = ResultFrame.from_trial_results(candidate.id, trial_results, top_n=top_n)
        fusion_result = self.fusion.evaluate(frame)

        print(
            f"[Reproducer] verdict={fusion_result.verdict} | "
            f"repro_rate={fusion_result.repro_rate:.2f} | "
            f"fusion_score={fusion_result.fusion_score:.4f}"
        )

        # 8단계: candidate 업데이트 후 storage 저장
        candidate.repro_verdict = fusion_result.verdict
        candidate.repro_rate = fusion_result.repro_rate
        candidate.repro_json = frame.to_dict()
        candidate.last_evidence = fusion_result.detail

        self.storage.save_candidate(candidate.id, candidate)
        self.storage.save_report(candidate.id, frame.to_dict(), mode=report_mode)

        return frame, fusion_result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _restore_frames(self, candidate: CandidateSnapshot) -> List[Tuple[int, bytes]]:
        
        if self.tx_log is not None and candidate.last_send_idx is not None:
            try:
                frames = self.tx_log.get_frame_window(
                    candidate.last_send_idx,
                    pre=self.pre_window,
                    post=self.post_window,
                )
                if frames:
                    print(
                        f"[Reproducer] tx_log 윈도우: center={candidate.last_send_idx} "
                        f"pre={self.pre_window} post={self.post_window} -> {len(frames)} frames"
                    )
                    return frames
                print("[Reproducer] tx_log 윈도우가 비어 있음 -> fallback")
            except Exception as e:
                print(f"[Reproducer] tx_log 오류 -> fallback ({e})")

        print("[Reproducer] fallback: 단일 프레임 (arb_id, payload)")
        return [(candidate.arb_id, candidate.payload)]
