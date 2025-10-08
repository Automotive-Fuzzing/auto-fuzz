# src/seed/seed_manager.py
import sqlite3
import json
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class Seed:
    id: str
    name: str
    signals: List[str]

class SeedManager:
    """Seed 데이터를 SQLite DB에 저장/조회하는 매니저"""

    def __init__(self, db_path: str = "seed_data.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self._create_table()

    def _create_table(self):
        query = """
        CREATE TABLE IF NOT EXISTS seeds (
            id TEXT PRIMARY KEY,
            name TEXT,
            metadata TEXT
        )
        """
        # ✅ 실제로 실행 + 커밋해야 테이블이 생성됨
        self.conn.execute(query)
        self.conn.commit()

    def add_seed(self, seed: Seed):
        query = "INSERT OR REPLACE INTO seeds (id, name, metadata) VALUES (?, ?, ?)"
        metadata_json = json.dumps({"signals": seed.signals}, ensure_ascii=False)
        self.conn.execute(query, (seed.id, seed.name, metadata_json))
        self.conn.commit()
    
    def get_all(self) -> List[Seed]:
        query = "SELECT id, name, metadata FROM seeds"
        rows = self.conn.execute(query).fetchall()

        # ✅ 변수명 통일 (result)
        result: List[Seed] = []
        for id_, name, metadata in rows:
            signals = json.loads(metadata).get("signals", [])
            result.append(Seed(id=id_, name=name, signals=signals))
        return result

    @staticmethod
    def from_dbc(parsed: Dict[str, Any]) -> List[Seed]:
        seeds: List[Seed] = []
        for msg_id, msg_data in parsed.items():
            seeds.append(
                Seed(
                    id=msg_id,
                    name=msg_data.get("name", "Unknown"),
                    signals=msg_data.get("signals", []),
                )
            )
        return seeds

if __name__ == "__main__":
    from dbc_parser import DBCParser

    parser = DBCParser("sample_dbc.json")
    parsed = parser.parse()

    seeds = SeedManager.from_dbc(parsed)
    m = SeedManager()
    for s in seeds:
        m.add_seed(s)

    print("=== DB 전체 조회 ===")
    for s in m.get_all():
        print(s)  # ✅ 소문자 s
