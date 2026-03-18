from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .candidate import CandidateSnapshot

JsonDict = Dict[str, Any]


class StorageIOError(RuntimeError):
    pass


class ReproStorage:
    def __init__(self, artifacts_root: Union[str, Path] = "artifacts") -> None:
        self.root = Path(artifacts_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_dir = self._create_run_dir()

    def _create_run_dir(self) -> Path:
        date_str = datetime.now().strftime("%Y%m%d")
        prefix = f"run_{date_str}_"

        max_n = 0
        for p in self.root.iterdir():
            if not p.is_dir():
                continue

            name = p.name
            if not name.startswith(prefix):
                continue

            suffix = name[len(prefix):]
            if suffix.isdigit():
                max_n = max(max_n, int(suffix))

        run_name = f"{prefix}{max_n + 1:03d}"
        run_dir = self.root / run_name
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def candidates_path(self) -> Path:
        return self.run_dir / "candidates.json"

    def reports_path(self) -> Path:
        return self.run_dir / "reports.json"

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

    def _normalize_obj(self, obj: Any) -> Any:
        try:
            if is_dataclass(obj):
                return asdict(obj)
            return obj
        except Exception:
            return {"_raw": str(obj)}

    def _append_json_array(self, path: Path, obj: Any) -> Path:
        normalized = self._normalize_obj(obj)

        if path.exists():
            data = self._load_json(path)
            if not isinstance(data, list):
                raise StorageIOError(f"Expected JSON array: {path}")
        else:
            data = []

        data.append(normalized)
        self._dump_json(path, data)
        return path

    def save_candidate(self, seed_id: Union[str, int], c: CandidateSnapshot) -> Path:
        # seed_id는 기존 호출부 호환용으로만 유지
        return self._append_json_array(self.candidates_path(), c.to_dict())

    def load_candidates(self) -> List[CandidateSnapshot]:
        p = self.candidates_path()
        if not p.exists():
            return []

        data = self._load_json(p)
        if not isinstance(data, list):
            raise StorageIOError(f"candidates.json must be a JSON array: {p}")

        result: List[CandidateSnapshot] = []
        for item in data:
            if not isinstance(item, dict):
                raise StorageIOError(f"Each candidate entry must be a JSON object: {p}")
            result.append(CandidateSnapshot.from_dict(item))
        return result

    def save_report(
        self,
        seed_id: Union[str, int],
        report: Any,
        *,
        name: Optional[str] = None,
    ) -> Path:
        # seed_id, name은 기존 호출부 호환용으로만 유지
        return self._append_json_array(self.reports_path(), report)

    def load_reports(self) -> List[JsonDict]:
        p = self.reports_path()
        if not p.exists():
            return []

        data = self._load_json(p)
        if not isinstance(data, list):
            raise StorageIOError(f"reports.json must be a JSON array: {p}")

        result: List[JsonDict] = []
        for item in data:
            if not isinstance(item, dict):
                raise StorageIOError(f"Each report entry must be a JSON object: {p}")
            result.append(item)
        return result


    def load_candidate(self, seed_id: Union[str, int]) -> CandidateSnapshot:
        sid = str(seed_id)

        for candidate in self.load_candidates():
            cid = getattr(candidate, "id", None)
            rid = getattr(candidate, "root_id", None)

            if str(cid) == sid or str(rid) == sid:
                return candidate

        raise StorageIOError(f"No candidate found for seed_id={seed_id}")

    def load_report(self, seed_id: Union[str, int], name: str = "") -> JsonDict:
        sid = str(seed_id)

        for report in reversed(self.load_reports()):
            if str(report.get("seed_id")) == sid:
                return report

        raise StorageIOError(f"No report found for seed_id={seed_id}")

    def latest_report(self, seed_id: Union[str, int]) -> Optional[Path]:
        sid = str(seed_id)

        for report in reversed(self.load_reports()):
            if str(report.get("seed_id")) == sid:
                return self.reports_path()

        return None