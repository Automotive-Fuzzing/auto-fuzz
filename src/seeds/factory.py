# src/seeds/factory.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .seed_manager import Seed

MAX_CAN_DLC = 8


def _clamp_dlc(dlc: int) -> int:
    if dlc < 0:
        return 0
    return min(dlc, MAX_CAN_DLC)


def build_initial_seeds_from_parsed_dbc(
    parsed_dbc: Dict[str, Any],
    *,
    default_priority: int = 0,
) -> List[Seed]:
    """
    DbcParser.parse() 결과(dict)를 받아 초기 후보(Seed) 리스트를 생성.
    - 메시지(frame) 단위로 1개씩 생성
    - payload는 기본으로 dlc 길이만큼 0x00
    """
    out: List[Seed] = []

    for msg in parsed_dbc.get("messages", []):
        arb_id = int(msg["id"])
        dlc = _clamp_dlc(int(msg.get("dlc", 8)))
        is_ext = bool(msg.get("is_extended_frame", False))

        payload = bytes([0x00] * dlc)

        meta = {
            "msg_name": msg.get("name"),
            "dlc": dlc,
            "is_extended": is_ext,
            "comment": msg.get("comment"),
            "cycle_time": msg.get("cycle_time"),
        }

        out.append(
            Seed(
                arb_id=arb_id,
                payload=payload,
                dlc=dlc,
                is_extended=is_ext,
                parent_id=None,
                root_id=None,
                depth=0,
                priority=default_priority,
                status="queued",
                meta=meta,
            )
        )

    return out


def build_child_seed(
    parent: Seed,
    *,
    payload: bytes,
    priority_delta: int = 0,
    status: str = "queued",
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Seed:
    """
    parent Seed로부터 child Seed 생성 규칙.
    - parent_id/depth/root_id/priority/meta를 자동 구성
    """
    assert parent.id is not None, "build_child_seed: parent.id is None (parent must be loaded from DB)"

    dlc = _clamp_dlc(parent.dlc if parent.dlc is not None else len(payload))
    child_payload = payload[:dlc]

    pr = int(parent.priority) + int(priority_delta)

    meta = dict(parent.meta or {})
    meta.update({"parent": parent.id, "depth": parent.depth + 1})
    if extra_meta:
        meta.update(extra_meta)

    return Seed(
        arb_id=parent.arb_id,
        payload=child_payload,
        dlc=dlc,
        is_extended=parent.is_extended,
        parent_id=parent.id,
        root_id=parent.root_id,
        depth=parent.depth + 1,
        priority=pr,
        status=status,
        meta=meta,
    )