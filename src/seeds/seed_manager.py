from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dbc_parser import parse as parse_dbc

@dataclass
class SignalMeta:
    start_bit: int
    length: int
    byte_order: str                 
    is_signed: bool
    scale: float
    offset: float
    minimum: Optional[float]
    maximum: Optional[float]
    unit: Optional[str]
    choices: Optional[Dict[int, str]]  # {값: 라벨}

@dataclass
class Seed:
    message_name: str
    frame_id: int
    signal_name: str
    dlc: int
    meta: SignalMeta

class SeedManager:
    """
    필수 기능:
      - add_seed(seed): 1건 저장
      - get_all(): 전건 조회
    meta는 JSON 문자열로 저장
    """
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seeds (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_name TEXT NOT NULL,
                frame_id     INTEGER NOT NULL,
                signal_name  TEXT NOT NULL,
                dlc          INTEGER NOT NULL,
                meta_json    TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    def add_seed(self, seed: Seed) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO seeds (message_name, frame_id, signal_name, dlc, meta_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                seed.message_name,
                seed.frame_id,
                seed.signal_name,
                seed.dlc,
                json.dumps(asdict(seed.meta), ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_all(self) -> List[Seed]:
        rows = self.conn.execute(
            "SELECT message_name, frame_id, signal_name, dlc, meta_json FROM seeds ORDER BY id ASC"
        ).fetchall()

        out: List[Seed] = []
        for r in rows:
            meta = SignalMeta(**json.loads(r["meta_json"]))
            out.append(
                Seed(
                    message_name=r["message_name"],
                    frame_id=int(r["frame_id"]),
                    signal_name=r["signal_name"],
                    dlc=int(r["dlc"]),
                    meta=meta,
                )
            )
        return out

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

def from_dbc(parsed: Dict[str, Any]) -> List[Seed]:
    # dbc_parser.parse() 결과(dict) → Seed 리스트

    seeds: List[Seed] = []
    for mname, m in (parsed.get("messages") or {}).items():
        frame_id = int(m["frame_id"])
        dlc = int(m.get("dlc", 8))
        for sname, s in (m.get("signals") or {}).items():
            meta = SignalMeta(
                start_bit=int(s["start_bit"]),
                length=int(s["length"]),
                byte_order=str(s["byte_order"]),     
                is_signed=bool(s["is_signed"]),
                scale=float(s["scale"]),
                offset=float(s["offset"]),
                minimum=s.get("minimum"),
                maximum=s.get("maximum"),
                unit=s.get("unit"),
                choices=s.get("choices"),
            )
            seeds.append(
                Seed(
                    message_name=mname,
                    frame_id=frame_id,
                    signal_name=sname,
                    dlc=dlc,
                    meta=meta,
                )
            )
    return seeds

if __name__ == "__main__":
    """
    사용법:
        python -m src.seeds.seed_manager <path_to_dbc> [--db .seeds.db]
    """
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="Path to .dbc (required)")
    ap.add_argument("--db", default=".seeds.db", help="SQLite file (default: .seeds.db)")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"[ERROR] DBC 파일을 찾을 수 없습니다: {in_path}", file=sys.stderr)
        sys.exit(2)
    if in_path.suffix.lower() != ".dbc":
        print(f"[ERROR] 이 단계는 .dbc만 지원합니다: {in_path}", file=sys.stderr)
        sys.exit(2)

    # 1) DBC 파싱(정규화 dict)
    parsed = parse_dbc(in_path)

    # 2) 파싱 결과 → Seed 리스트
    seeds = from_dbc(parsed)
    if not seeds:
        print("[WARN] 변환된 Seed가 없습니다. DBC 내용을 확인하세요.", file=sys.stderr)

    # 3) DB 저장 → 조회
    mgr = SeedManager(args.db)
    try:
        for s in seeds:
            mgr.add_seed(s)

        loaded = mgr.get_all()
        print(f"[INFO] saved={len(seeds)}, loaded={len(loaded)}")
        for s in loaded:
            print(
                f"- {s.message_name} (0x{s.frame_id:X}, dlc={s.dlc}) :: "
                f"{s.signal_name} [{s.meta.start_bit}:{s.meta.length} {s.meta.byte_order}]"
            )
    finally:
        mgr.close()