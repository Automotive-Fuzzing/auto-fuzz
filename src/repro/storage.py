from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .candidate import CandidateSnapshot

JsonDict = Dict[str, Any]

class StorageIOError(RuntimeError):
    pass

class ReproStorage:
    def __init__(self, artifacts_root: Union[str, Path] = "artifacts") -> None:
        self.root = Path(artifacts_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _sid(self, seed_id: Union[str, int]) -> str:
        s = str(seed_id).strip()
        if not s:
            raise ValueError("seed_id must be non-empty")
        return s

    def artifact_dir(self, seed_id: Union[str, int]) -> Path:
        p = self.root / "repro" / self._sid(seed_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def candidate_path(self, seed_id: Union[str, int]) -> Path:
        return self.artifact_dir(seed_id) / "candidate.json"

    def report_path(self, seed_id: Union[str, int], name: str) -> Path:
        return self.artifact_dir(seed_id) / name

    def _dump_json(self, path: Path, obj: Any) -> None:
        try:
            if is_dataclass(obj):
                obj = asdict(obj)
            text = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            text = json.dumps({"_raw": str(obj)}, ensure_ascii=False, indent=2)

        try:
            path.write_text(text, encoding="utf-8")
        except Exception as e:
            raise StorageIOError(f"Failed to write: {path}") from e

    def _load_json(self, path: Path) -> Any:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            raise StorageIOError(f"Failed to read: {path}") from e

        try:
            return json.loads(text)
        except Exception as e:
            raise StorageIOError(f"Invalid JSON: {path}") from e

    def save_candidate(self, seed_id: Union[str, int], c: CandidateSnapshot) -> Path:
        p = self.candidate_path(seed_id)
        self._dump_json(p, c.to_dict())
        return p

    def load_candidate(self, seed_id: Union[str, int]) -> CandidateSnapshot:
        p = self.candidate_path(seed_id)
        d = self._load_json(p)
        if not isinstance(d, dict):
            raise StorageIOError(f"candidate.json must be a JSON object: {p}")
        return CandidateSnapshot.from_dict(d)

    def _report_name(self, seed_id: Union[str, int]) -> str:
        d = self.artifact_dir(seed_id)
        n = 1

        for f in d.glob("report_*.json"):
            stem = f.stem
            try:
                suffix = stem.split("_", 1)[1]
                if suffix.isdigit():
                    n = max(n, int(suffix) + 1)
            except (IndexError, ValueError):
                pass

        return f"report_{n:03d}.json"

    def save_report(
        self,
        seed_id: Union[str, int],
        report: Any,
        *,
        name: Optional[str] = None,
    ) -> Path:
        fname = name or self._report_name(seed_id)
        p = self.report_path(seed_id, fname)
        self._dump_json(p, report)
        return p

    def load_report(self, seed_id: Union[str, int], name: str) -> JsonDict:
        p = self.report_path(seed_id, name)
        d = self._load_json(p)
        if not isinstance(d, dict):
            raise StorageIOError(f"report must be a JSON object: {p}")
        return d

    def latest_report(self, seed_id: Union[str, int]) -> Optional[Path]:
        d = self.artifact_dir(seed_id)
        latest: Optional[Path] = None
        max_n = 0

        for f in d.glob("report_*.json"):
            stem = f.stem
            try:
                suffix = stem.split("_", 1)[1]
                if suffix.isdigit():
                    n = int(suffix)
                    if n > max_n:
                        max_n = n
                        latest = f
            except (IndexError, ValueError):
                pass

        return latest