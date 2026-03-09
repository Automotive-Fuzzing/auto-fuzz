from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List

from .seed_manager import SeedManager


@dataclass(frozen=True)
class TxFrame:
    arb_id: int
    payload: bytes
    dlc: int = 8


class TxLog:
    def __init__(self, seed_manager: SeedManager) -> None:
        self.sm = seed_manager
        self._create_table()

    def _create_table(self) -> None:
        self.sm.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tx_log (
                send_idx INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                arb_id INTEGER NOT NULL,
                payload BLOB NOT NULL,
                dlc INTEGER NOT NULL,
                seed_id INTEGER NULL,
                FOREIGN KEY(seed_id) REFERENCES seeds(id)
            );
            """
        )
        self.sm.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_txlog_seed ON tx_log(seed_id, send_idx);"
        )
        self.sm.conn.commit()

    def append(self, frame: TxFrame, seed_id: Optional[int] = None) -> int:
        cur = self.sm.conn.cursor()
        cur.execute(
            """
            INSERT INTO tx_log (arb_id, payload, dlc, seed_id)
            VALUES (?, ?, ?, ?);
            """,
            (
                frame.arb_id,
                frame.payload,
                frame.dlc,
                seed_id,
            ),
        )
        self.sm.conn.commit()
        return int(cur.lastrowid)

    def get_window(self, anchor_send_idx: int, pre: int, post: int) -> List[TxFrame]:
        start = max(1, anchor_send_idx - max(0, pre))
        end = anchor_send_idx + max(0, post)

        rows = self.sm.conn.execute(
            """
            SELECT arb_id, payload, dlc
            FROM tx_log
            WHERE send_idx BETWEEN ? AND ?
            ORDER BY send_idx ASC;
            """,
            (start, end),
        ).fetchall()

        frames: List[TxFrame] = []
        for r in rows:
            frames.append(
                TxFrame(
                    arb_id=int(r["arb_id"]),
                    payload=bytes(r["payload"]),
                    dlc=int(r["dlc"]),
                )
            )
        return frames