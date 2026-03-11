from __future__ import annotations

import json
import sqlite3
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class SignalMeta:
    start_bit: Optional[int] = None
    length: Optional[int] = None
    byte_order: Optional[str] = None
    is_signed: Optional[bool] = None
    factor: Optional[float] = None
    offset: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: Optional[str] = None
    comment: Optional[str] = None


@dataclass
class Seed:
    message_id: Optional[int] = None
    signal_name: str = ""
    desc: str = ""
    priority: int = 1
    metadata: SignalMeta = field(default_factory=SignalMeta)
    id: Optional[int] = None

    arb_id: Optional[int] = None
    payload: bytes = b""
    dlc: int = 8
    is_extended: bool = False

    parent_id: Optional[int] = None
    root_id: Optional[int] = None
    depth: int = 0
    status: str = "queued"
    meta: Dict[str, Any] = field(default_factory=dict)
    last_send_idx: Optional[int] = None

    repro_verdict: Optional[str] = None
    repro_rate: Optional[float] = None
    last_evidence: Optional[str] = None
    repro_json: Optional[Dict[str, Any]] = None

    fingerprint: Optional[str] = None

    def ensure_candidate_defaults(self) -> None:
        if self.arb_id is None and self.message_id is not None:
            self.arb_id = int(self.message_id)
        if self.message_id is None and self.arb_id is not None:
            self.message_id = int(self.arb_id)
        if self.dlc is None:
            self.dlc = len(self.payload or b"")
        if not self.payload:
            self.payload = bytes([0x00] * int(self.dlc))
        if self.fingerprint is None:
            self.fingerprint = _sha256_hex(bytes(self.payload or b""))


class SeedManager:
    def __init__(self, db_path: str = "seeds.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_table()
        self._migrate()

    def _create_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                message_id INTEGER,
                signal_name TEXT,
                desc TEXT,
                priority INTEGER NOT NULL DEFAULT 1,
                metadata TEXT,

                arb_id INTEGER,
                payload BLOB,
                dlc INTEGER DEFAULT 8,
                is_extended INTEGER NOT NULL DEFAULT 0,

                parent_id INTEGER,
                root_id INTEGER,
                depth INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                meta_json TEXT,

                last_send_idx INTEGER,

                repro_verdict TEXT,
                repro_rate REAL,
                last_evidence TEXT,
                repro_json TEXT,

                fingerprint TEXT,

                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        self.conn.commit()

    def _migrate(self):
        self._ensure_column("seeds", "arb_id", "INTEGER")
        self._ensure_column("seeds", "payload", "BLOB")
        self._ensure_column("seeds", "dlc", "INTEGER DEFAULT 8")
        self._ensure_column("seeds", "is_extended", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("seeds", "parent_id", "INTEGER")
        self._ensure_column("seeds", "root_id", "INTEGER")
        self._ensure_column("seeds", "depth", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("seeds", "status", "TEXT NOT NULL DEFAULT 'queued'")
        self._ensure_column("seeds", "meta_json", "TEXT")
        self._ensure_column("seeds", "last_send_idx", "INTEGER")
        self._ensure_column("seeds", "repro_verdict", "TEXT")
        self._ensure_column("seeds", "repro_rate", "REAL")
        self._ensure_column("seeds", "last_evidence", "TEXT")
        self._ensure_column("seeds", "repro_json", "TEXT")
        self._ensure_column("seeds", "fingerprint", "TEXT")

        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_seeds_candidate_dedup
            ON seeds(arb_id, dlc, fingerprint);
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seeds_priority
            ON seeds(priority DESC, id ASC);
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seeds_status_priority
            ON seeds(status, priority DESC, id ASC);
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seeds_root
            ON seeds(root_id);
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seeds_parent
            ON seeds(parent_id);
            """
        )
        self.conn.commit()

    def _ensure_column(self, table: str, col: str, col_type: str):
        rows = self.conn.execute(f"PRAGMA table_info({table});").fetchall()
        existing_cols = {row["name"] for row in rows}
        if col not in existing_cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type};")
            self.conn.commit()

    def add_seed(self, seed: Seed) -> int:
        seed.ensure_candidate_defaults()

        cur = self.conn.execute(
            """
            INSERT INTO seeds (
                message_id, signal_name, desc, priority, metadata,
                arb_id, payload, dlc, is_extended,
                parent_id, root_id, depth, status, meta_json,
                last_send_idx,
                repro_verdict, repro_rate, last_evidence, repro_json,
                fingerprint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                seed.message_id,
                seed.signal_name,
                seed.desc,
                seed.priority,
                self._dump_signal_meta(seed.metadata),
                seed.arb_id,
                bytes(seed.payload or b""),
                int(seed.dlc),
                1 if seed.is_extended else 0,
                seed.parent_id,
                seed.root_id,
                int(seed.depth),
                seed.status,
                json.dumps(seed.meta or {}, ensure_ascii=False),
                seed.last_send_idx,
                seed.repro_verdict,
                seed.repro_rate,
                seed.last_evidence,
                json.dumps(seed.repro_json, ensure_ascii=False) if seed.repro_json is not None else None,
                seed.fingerprint,
            ),
        )
        self.conn.commit()

        new_id = int(cur.lastrowid)

        if seed.root_id is None:
            root_id = seed.parent_id if seed.parent_id is not None else new_id
            if seed.parent_id is not None:
                parent = self.get_seed(seed.parent_id)
                if parent is not None and parent.root_id is not None:
                    root_id = parent.root_id
            self.conn.execute(
                "UPDATE seeds SET root_id = ?, updated_at = datetime('now') WHERE id = ?",
                (root_id, new_id),
            )
            self.conn.commit()

        return new_id

    def insert_seed(self, seed: Seed) -> Optional[int]:
        seed.ensure_candidate_defaults()

        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO seeds (
                message_id, signal_name, desc, priority, metadata,
                arb_id, payload, dlc, is_extended,
                parent_id, root_id, depth, status, meta_json,
                last_send_idx,
                repro_verdict, repro_rate, last_evidence, repro_json,
                fingerprint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                seed.message_id,
                seed.signal_name,
                seed.desc,
                seed.priority,
                self._dump_signal_meta(seed.metadata),
                seed.arb_id,
                bytes(seed.payload or b""),
                int(seed.dlc),
                1 if seed.is_extended else 0,
                seed.parent_id,
                seed.root_id,
                int(seed.depth),
                seed.status,
                json.dumps(seed.meta or {}, ensure_ascii=False),
                seed.last_send_idx,
                seed.repro_verdict,
                seed.repro_rate,
                seed.last_evidence,
                json.dumps(seed.repro_json, ensure_ascii=False) if seed.repro_json is not None else None,
                seed.fingerprint,
            ),
        )
        self.conn.commit()

        if cur.rowcount == 0:
            return None

        new_id = int(cur.lastrowid)

        if seed.root_id is None:
            root_id = seed.parent_id if seed.parent_id is not None else new_id
            if seed.parent_id is not None:
                parent = self.get_seed(seed.parent_id)
                if parent is not None and parent.root_id is not None:
                    root_id = parent.root_id
            self.conn.execute(
                "UPDATE seeds SET root_id = ?, updated_at = datetime('now') WHERE id = ?",
                (root_id, new_id),
            )
            self.conn.commit()

        return new_id

    def get_all(self) -> List[Seed]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM seeds
            ORDER BY priority DESC, id ASC
            """
        ).fetchall()
        return [self._row_to_seed(row) for row in rows]

    def get_seed(self, seed_id: int) -> Optional[Seed]:
        row = self.conn.execute(
            "SELECT * FROM seeds WHERE id = ?",
            (seed_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_seed(row)

    def update_priority(self, seed_id: int, new_priority: int):
        self.conn.execute(
            "UPDATE seeds SET priority = ?, updated_at = datetime('now') WHERE id = ?",
            (new_priority, seed_id),
        )
        self.conn.commit()

    def update_status(self, seed_id: int, new_status: str):
        self.conn.execute(
            "UPDATE seeds SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, seed_id),
        )
        self.conn.commit()

    def set_last_send_idx(self, seed_id: int, send_idx: int):
        self.conn.execute(
            "UPDATE seeds SET last_send_idx = ?, updated_at = datetime('now') WHERE id = ?",
            (send_idx, seed_id),
        )
        self.conn.commit()

    def save_repro_summary(
        self,
        seed_id: int,
        repro_verdict: str,
        repro_rate: float,
        last_evidence: Optional[str] = None,
    ):
        self.conn.execute(
            """
            UPDATE seeds
            SET repro_verdict = ?, repro_rate = ?, last_evidence = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (repro_verdict, repro_rate, last_evidence, seed_id),
        )
        self.conn.commit()

    def save_repro_report(self, seed_id: int, repro_report: Dict[str, Any]):
        self.conn.execute(
            """
            UPDATE seeds
            SET repro_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (json.dumps(repro_report, ensure_ascii=False), seed_id),
        )
        self.conn.commit()

    def delete_seed(self, seed_id: int):
        self.conn.execute("DELETE FROM seeds WHERE id = ?", (seed_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()

    def _dump_signal_meta(self, metadata: Any) -> str:
        if isinstance(metadata, SignalMeta):
            return json.dumps(metadata.__dict__, ensure_ascii=False)
        if isinstance(metadata, dict):
            return json.dumps(metadata, ensure_ascii=False)
        return json.dumps({}, ensure_ascii=False)

    def _row_to_seed(self, row: sqlite3.Row) -> Seed:
        metadata_raw = row["metadata"]
        metadata_dict = json.loads(metadata_raw) if metadata_raw else {}
        meta_json_raw = row["meta_json"]
        meta_dict = json.loads(meta_json_raw) if meta_json_raw else {}
        repro_json_raw = row["repro_json"]
        repro_dict = json.loads(repro_json_raw) if repro_json_raw else None

        return Seed(
            id=row["id"],
            message_id=row["message_id"],
            signal_name=row["signal_name"] or "",
            desc=row["desc"] or "",
            priority=row["priority"],
            metadata=SignalMeta(**metadata_dict),
            arb_id=row["arb_id"],
            payload=bytes(row["payload"]) if row["payload"] is not None else b"",
            dlc=row["dlc"] if row["dlc"] is not None else 8,
            is_extended=bool(row["is_extended"]) if row["is_extended"] is not None else False,
            parent_id=row["parent_id"],
            root_id=row["root_id"],
            depth=row["depth"] if row["depth"] is not None else 0,
            status=row["status"] or "queued",
            meta=meta_dict,
            last_send_idx=row["last_send_idx"],
            repro_verdict=row["repro_verdict"],
            repro_rate=row["repro_rate"],
            last_evidence=row["last_evidence"],
            repro_json=repro_dict,
            fingerprint=row["fingerprint"],
        )

    @staticmethod
    def from_dbc(parsed_dbc: Dict[str, Any]) -> List[List["Seed"]]:
        grouped_seeds: List[List[Seed]] = []

        for msg in parsed_dbc.get("messages", []):
            message_group: List[Seed] = []

            for sig in msg.get("signals", []):
                meta = SignalMeta(
                    start_bit=sig.get("start_bit"),
                    length=sig.get("length"),
                    byte_order=sig.get("byte_order"),
                    is_signed=sig.get("is_signed"),
                    factor=sig.get("factor"),
                    offset=sig.get("offset"),
                    minimum=sig.get("minimum"),
                    maximum=sig.get("maximum"),
                    unit=sig.get("unit"),
                    comment=sig.get("comment"),
                )
                seed = Seed(
                    message_id=msg["id"],
                    arb_id=msg["id"],
                    signal_name=sig["name"],
                    desc=f"Signal {sig['name']} in {msg['name']}",
                    priority=1,
                    metadata=meta,
                    dlc=int(msg.get("dlc", 8)),
                    is_extended=bool(msg.get("is_extended_frame", False)),
                    payload=bytes([0x00] * int(msg.get("dlc", 8))),
                    depth=0,
                    status="queued",
                    meta={
                        "msg_name": msg.get("name"),
                        "signal_name": sig.get("name"),
                    },
                )
                seed.ensure_candidate_defaults()
                message_group.append(seed)

            grouped_seeds.append(message_group)

        return grouped_seeds