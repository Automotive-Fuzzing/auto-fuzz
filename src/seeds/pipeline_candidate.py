# src/seeds/pipeline_candidate.py
from __future__ import annotations

from typing import Dict, Optional

from .seed_manager import SeedManager
from .seed_queue import SeedQueue
from .tx_log import TxLog, TxFrame


class CandidatePipeline:
    def __init__(
        self,
        *,
        cfg: Dict,
        can_iface: object,
        seed_manager: SeedManager,
        seed_queue: SeedQueue,
        tx_log: TxLog,
    ):
        self.cfg = cfg
        self.can = can_iface
        self.sm = seed_manager
        self.q = seed_queue
        self.txlog = tx_log
        self.running = False

    def send_one(self) -> Optional[int]:
        """
        큐에서 seed 하나를 꺼내 전송하고 seed_id 반환.
        전송할 seed가 없거나 실패하면 None 반환.
        """
        can_cfg = self.cfg.get("can", {})
        force_default_id = bool(can_cfg.get("force_default_id", True))
        default_id = int(can_cfg.get("default_id", "0x6A6"), 16)

        seed_id = self.q.pop()
        if seed_id is None:
            return None

        seed = self.sm.get_seed(seed_id)
        if seed is None:
            self.sm.update_status(seed_id, "error")
            return None

        arb_id = default_id if force_default_id else int(seed.arb_id)
        payload = bytes(seed.payload or b"")[: int(seed.dlc)]
        dlc = int(seed.dlc)
        is_ext = bool(seed.is_extended)

        frame = TxFrame(arb_id=arb_id, payload=payload, dlc=dlc)

        try:
            self.can.send_raw(frame.payload, arb_id=frame.arb_id, is_extended=is_ext)
        except Exception:
            self.sm.update_status(seed_id, "error")
            return None

        try:
            send_idx = self.txlog.append(frame, seed_id=seed_id)
            self.sm.set_last_send_idx(seed_id, send_idx)
        except Exception:
            pass

        self.sm.update_status(seed_id, "sent")
        return seed_id

    def stop(self) -> None:
        self.running = False
