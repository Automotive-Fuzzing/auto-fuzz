from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional

class CandidateParseError(ValueError):
    pass

def parse_can_id(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        try:
            return int(s)
        except ValueError as e:
            raise CandidateParseError(f"Invalid CAN ID string: {v}") from e
    raise CandidateParseError(f"Invalid type for arb_id: {type(v)}")

def parse_payload(s: Any) -> bytes:
    if not isinstance(s, str):
        raise CandidateParseError("payload must be hex string")
    v = s.strip().lower().replace(" ", "").replace(":", "").replace("-", "")
    if v.startswith("0x"):
        v = v[2:]
    if not v:
        raise CandidateParseError("payload hex string is empty")
    try:
        return bytes.fromhex(v)
    except ValueError as e:
        raise CandidateParseError(f"Invalid payload hex string: {s}") from e

def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

@dataclass
class CandidateSnapshot:
    arb_id: int
    payload: bytes
    dlc: int
    id: int
    priority: Optional[int] = None
    parent_id: Optional[int] = None
    root_id: Optional[int] = None
    depth: Optional[int] = None
    monitor_json: Optional[Dict[str, Any]] = None
    repro_verdict: Optional[str] = None
    repro_rate: Optional[float] = None
    last_evidence: Optional[Any] = None
    repro_json: Optional[Dict[str, Any]] = None
    last_send_idx: Optional[int] = None
    fingerprint: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CandidateSnapshot":
        if not isinstance(d, dict):
            raise CandidateParseError("Candidate JSON root must be an object(dict)")

        cid = d.get("id")
        if cid is None:
            raise CandidateParseError("Missing required field: id")

        arb_id = parse_can_id(d.get("arb_id"))
        payload = parse_payload(d.get("payload"))
        dlc = d.get("dlc", len(payload))
        last_send_idx = d.get("last_send_idx")
        priority = d.get("priority")
        depth = d.get("depth")
        parent_id = d.get("parent_id")
        root_id = d.get("root_id")
        monitor_json = d.get("monitor_json")
        repro_verdict = d.get("repro_verdict")
        repro_rate = d.get("repro_rate")
        last_evidence = d.get("last_evidence")
        repro_json = d.get("repro_json")

        # fingerprint
        fp = d.get("fingerprint")
        if fp is None:
            fp = sha256_hex(payload)
        else:
            fp = str(fp).strip().lower()

        return CandidateSnapshot(
            arb_id=arb_id,
            payload=payload,
            dlc=dlc,
            id=cid,
            priority=priority,
            parent_id=parent_id,
            root_id=root_id,
            depth=depth,
            monitor_json=monitor_json,
            repro_verdict=str(repro_verdict) if repro_verdict is not None else None,
            repro_rate=float(repro_rate) if repro_rate is not None else None,
            last_evidence=last_evidence,
            repro_json=repro_json,
            last_send_idx=last_send_idx,
            fingerprint=fp,
        )

    def to_dict(self) -> Dict[str, Any]:
        fp = self.fingerprint or sha256_hex(self.payload)

        return {
            "id": self.id,
            "arb_id": self.arb_id,
            "payload": self.payload.hex(),
            "dlc": self.dlc,
            "priority": self.priority,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
            "depth": self.depth,
            "monitor_json": self.monitor_json,
            "repro_verdict": self.repro_verdict,
            "repro_rate": self.repro_rate,
            "last_evidence": self.last_evidence,
            "repro_json": self.repro_json,
            "last_send_idx": self.last_send_idx,
            "fingerprint": fp,
        }