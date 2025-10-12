# ─────────────────────────────────────────────────────────────────────────────
# file: src/seeds/seed_manager.py
# ─────────────────────────────────────────────────────────────────────────────
# 파일의 경로와 역할 명확히 문서화
"""
Seed storage layer using SQLite3 + dataclasses.

Tables
------
seeds(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id TEXT NOT NULL,
  msg_name TEXT,
  signal_name TEXT,
  metadata TEXT NOT NULL,   -- JSON string (arbitrary signal meta)
  created_at TEXT NOT NULL  -- ISO8601
)

Public API (minimal for the assignment)
---------------------------------------
- Seed dataclass
- SeedManager(db_path).add_seed(seed) -> int
- SeedManager(db_path).get_all() -> list[Seed]
- from_dbc(parsed_dict) -> list[Seed]

Includes a runnable demo at bottom:
$ python -m src.seeds.seed_manager
"""

from __future__ import annotations # type hint 순환 참조를 허용하기 위한 선언

from dataclasses import dataclass, asdict # 보일러플레이트 없이 데이터 전용 클래스 생성 및 asdict로 dataclass를 dict로 바꿀 수 있도록 함
from pathlib import Path # 파일 경로 객체를 위한 import
from typing import Any, Dict, List, Optional # 정적 타입 힌트를 위한 제네릭 타입
import json # 메타데이터 직렬화/역직렬화
import sqlite3 # 내장 DB 사용
from datetime import datetime, timezone # DB에 저장할 ISO8601 타임스탬프 생성


# ── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class Seed: # 시드를 표현하는 데이터 모델 정의
    msg_id: str # CAN ID는 필수
    msg_name: Optional[str] # 메시지 이름 (없을 수도 있음)
    signal_name: Optional[str]
    metadata: Dict[str, Any]
    id: Optional[int] = None
    created_at: Optional[str] = None  # ISO8601 when loaded from DB


# (Optional) richer structure for signal meta if needed later
@dataclass
class SignalMeta: # 신호 메타 정보를 안전하게 다루고 싶을 때 사용하는 데이터 모델
    start_bit: int # 신호 시작 비트
    length: int # 신호 길이
    factor: float = 1.0 # 물리값 변환 계수 기본값 : 1
    offset: float = 0.0 # 물리값 변환 오프셋 기본값 0.0
    unit: str = "" # 단위 문자열


# ── Storage ─────────────────────────────────────────────────────────────────
class SeedManager: # SQLite 기반의 시드 저장 및 조회하는 클래스 
    def __init__(self, db_path: str = "./auto_fuzz.sqlite3") -> None: # 생성자: DB 파일 경로를 인자로 받으며 기본값은 현재 디렉토리의 auto_fuzz.sqlite3
        self.db_path = db_path # 인스턴스에 DB 경로 저장
        self._ensure_schema() # 인스턴스 초기화 시점에 스키마를 보장

    def _connect(self) -> sqlite3.Connection: # 내부 전용 : DB 연결을 반환하는 헬퍼 메서드
        conn = sqlite3.connect(self.db_path) # 지정된 DB 파일에 연결
        conn.row_factory = sqlite3.Row # 선택된 결과를 sqlite3.Row로 받아 컬럼명으로 접근 가능하게 함ㄴ
        return conn # 연결 객체 반환

    def _ensure_schema(self) -> None: # 내부 전용 : 스키마를 생성하는 메서드
        with self._connect() as conn: # 컨텍스트 매니저로 DB 연결을 열고, 블록 끝에서 자동으로 커밋/종료를 보장.
            conn.execute( # 단일 SQL 실행 시작
                """
                CREATE TABLE IF NOT EXISTS seeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT NOT NULL,
                    msg_name TEXT,
                    signal_name TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    # CRUD (subset)
    def add_seed(self, seed: Seed) -> int: # Seed 한 건을 DB에 추가, 삽입된 행의 ID를 변환하는 공개 메서드
        now = datetime.now(timezone.utc).isoformat() # 현재 UTC 시각을 ISO8601 문자열로 생성
        with self._connect() as conn: # DB 연결을 열고 컨텍스트 매니저로 안전하게 사용.
            cur = conn.execute( # INSERT 쿼리를 수행하고 커서를 받음
                """
                INSERT INTO seeds (msg_id, msg_name, signal_name, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    seed.msg_id, # 바인딩할 값 (dataclass에서 가져옴)
                    seed.msg_name,
                    seed.signal_name,
                    json.dumps(seed.metadata, ensure_ascii=False), # metadata는 dict -> JSON 문자열 직렬화
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_all(self) -> List[Seed]: # 모든 시드 레코드를 Seed 객체 리스트로 변환하는 공개 메서드
        with self._connect() as conn: # DB 연결 열기
            rows = conn.execute( 
                "SELECT id, msg_id, msg_name, signal_name, metadata, created_at FROM seeds ORDER BY id ASC"
            ).fetchall() # SELECT 쿼리 수행 후 전체 행을 리스트로 가져옴
        out: List[Seed] = [] # 반환용 리스트 초기화
        for r in rows: # 각 행 순회
            meta = json.loads(r["metadata"]) if r["metadata"] else {} # DB에 저장된 JSON 문자열 metadata를 dict로 파싱
            out.append(
                Seed(
                    id=int(r["id"]),
                    msg_id=str(r["msg_id"]),
                    msg_name=r["msg_name"],
                    signal_name=r["signal_name"],
                    metadata=meta,
                    created_at=str(r["created_at"]),
                )
            )
        return out


# ── Helpers ─────────────────────────────────────────────────────────────────
def from_dbc(parsed: Dict[str, Any]) -> List[Seed]: # dbc_parser.parse()가 만든 정규화된 dict를 받아 Seed 리스트로 바꾸는 함수
    """
    Convert parsed DBC (normalized dict) to a list of Seed objects.

    Policy: one seed per signal; pack the signal's useful attributes into metadata.
    """
    seeds: List[Seed] = [] # 결과 리스트 초기화
    for m in parsed.get("messages", []): # 정규화 dict에서 메시지 목록을 순회
        msg_id = str(m.get("id"))
        msg_name = str(m.get("name", "")) or None
        for s in m.get("signals", []): # 각 메시지의 신호들을 순회
            signal_name = str(s.get("name", "")) or None
            meta = { # 이 신호를 설명하는 핵심 속성들을 metadata dict로 구성.
                "start_bit": int(s.get("start_bit", 0)), 
                "length": int(s.get("length", 1)),
                "factor": float(s.get("factor", 1.0)),
                "offset": float(s.get("offset", 0.0)),
                "unit": str(s.get("unit", "")),
                "tx": m.get("tx"),
                "rx": s.get("rx"),
            }
            seeds.append(Seed(msg_id=msg_id, msg_name=msg_name, signal_name=signal_name, metadata=meta)) # Seed 객체를 만들고 결과 리스트에 추가
    return seeds


# ── Demo flow: parse → to seeds → store → fetch ─────────────────────────────
if __name__ == "__main__":
    # To keep the demo self-contained, we'll import the parser and use inline JSON
    from src.seeds.dbc_parser import parse as parse_dbc

    # 1) Fake DBC (can be replaced by a file later) 테스트를 위한 DBC
    fake = {
        "messages": [
            {
                "id": "0x366",
                "name": "Blinkmodi_02",
                "tx": "BCM",
                "signals": [
                    {"name": "Blinken_re", "start_bit": 0, "length": 8, "factor": 1.0, "offset": 0.0, "unit": ""},
                    {"name": "li_Kombi_Takt", "start_bit": 8, "length": 8, "factor": 1.0, "offset": 0.0, "unit": ""},
                ],
            },
            {
                "id": "0x6B8",
                "name": "Kombi_03",
                "tx": "Gateway",
                "signals": [
                    {"name": "Speed", "start_bit": 16, "length": 16, "factor": 0.1, "offset": 0.0, "unit": "km/h"},
                ],
            },
        ]
    }

    # 2) Parse → normalized dict
    parsed = parse_dbc(content=json.dumps(fake))

    # 3) Convert to seeds
    seeds = from_dbc(parsed)

    # 4) Persist to SQLite
    db = SeedManager("./auto_fuzz.sqlite3")
    print("Inserting seeds…")
    for s in seeds:
        rowid = db.add_seed(s)
        print(f"  [+] rowid={rowid} msg={s.msg_id}:{s.msg_name} sig={s.signal_name}")

    # 5) Read back
    print("\nAll seeds from DB:")
    for s in db.get_all():
        print(f"  id={s.id} created={s.created_at} msg={s.msg_id}:{s.msg_name} sig={s.signal_name} meta={json.dumps(s.metadata, ensure_ascii=False)}")


# ─────────────────────────────────────────────────────────────────────────────
# Quick HOWTO (for your README.md)
# ─────────────────────────────────────────────────────────────────────────────
# python -m src.seeds.seed_manager
#   → runs an end-to-end demo using fake in-memory JSON
#
# If you want to parse a file instead, create sample.json and do:
# from src.seeds.dbc_parser import parse
# parsed = parse("./sample.json")
# seeds = from_dbc(parsed)
# SeedManager().add_seed(...)
