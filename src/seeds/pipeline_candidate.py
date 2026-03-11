# src/seeds/pipeline_candidate.py
from __future__ import annotations

import time
from typing import Dict, Optional

from .seed_manager import SeedManager
from .seed_queue import SeedQueue
from .tx_log import TxLog, TxFrame
from .mutation_engine import MutationEngine


class CandidatePipeline:
    def __init__(
        self,
        *,
        cfg: Dict,
        can_iface: object,
        seed_manager: SeedManager,
        seed_queue: SeedQueue,
        tx_log: TxLog,
        mutation_engine: MutationEngine,
    ):
        self.cfg = cfg
        self.can = can_iface
        self.sm = seed_manager
        self.q = seed_queue
        self.txlog = tx_log
        self.mut = mutation_engine
        self.running = False

    def run(
        self,
        *,
        max_iters: Optional[int] = None,
        per_send_gap_s: float = 0.01,
        per_seed_gap_s: float = 0.2,
        mutation_weights: Optional[Dict[str, float]] = None,
    ) -> int:
        """
        반환: 처리(전송 시도)한 seed 개수
        """
        self.running = True
        done = 0
        iters = 0

        can_cfg = self.cfg.get("can", {})
        force_default_id = bool(can_cfg.get("force_default_id", True))
        default_id = int(can_cfg.get("default_id", "0x6A6"), 16)

        while self.running:
            if max_iters is not None and iters >= max_iters:
                break
            iters += 1

            # 1) pop: (seed_queue 테이블에서 claim=DELETE)
            seed_id = self.q.pop()
            if seed_id is None:
                break

            seed = self.sm.get_seed(seed_id)
            if seed is None:
                # DB에서 사라졌거나 이상한 상태면 error 처리
                self.sm.update_status(seed_id, "error")
                continue

            # 2) frame 결정
            arb_id = default_id if force_default_id else int(seed.arb_id)
            payload = bytes(seed.payload or b"")[: int(seed.dlc)]
            dlc = int(seed.dlc)
            is_ext = bool(seed.is_extended)

            frame = TxFrame(
                arb_id=arb_id,
                payload=payload,
                dlc=dlc,
            )

            # 3) CAN send
            try:
                self.can.send_raw(frame.payload, arb_id=frame.arb_id, is_extended=is_ext)
            except Exception:
                self.sm.update_status(seed_id, "error")
                continue

            # 4) tx_log + anchor
            try:
                send_idx = self.txlog.append(frame, seed_id=seed_id)
                self.sm.set_last_send_idx(seed_id, send_idx)
            except Exception:
                send_idx = None  # tx_log 실패해도 퍼징은 계속

            # 5) 전송 성공 처리
            self.sm.update_status(seed_id, "sent")
            done += 1
            time.sleep(per_send_gap_s)

            # 6) mutation -> child 생성/저장 -> queue push
            try:
                child_ids = self.mut.mutate_and_store_children(
                    seed,
                    weights=mutation_weights or {},
                    min_length=1,
                    priority_delta=0,
                    extra_meta={"anchor_send_idx": send_idx} if send_idx is not None else None,
                )
                for cid in child_ids:
                    self.q.push(cid)  # seed_queue 테이블에 INSERT OR IGNORE
            except Exception:
                pass

            time.sleep(per_seed_gap_s)

        return done

    def stop(self) -> None:
        self.running = False