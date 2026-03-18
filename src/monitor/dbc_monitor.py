# src/monitor/dbc_monitor.py

import time
import can
import cantools
from typing import Dict, Any, Optional, List, Union
from ..logger.base_logger import log_event
from ..seeds.seed_manager import SeedManager

Number = Union[int, float]


class DBCMonitor:
    DEFAULT_WEIGHTS: Dict[str, float] = {
        "decode_error": 3.0,
        "missing_signal": 2.5,
        "type_cast": 2.0,
        "enum": 1.5,
        "range": 1.0,
    }

    def _infer_rules_from_seeds(self, seeds, target_id):
        rules: Dict[str, Dict[str, Any]] = {}

        for sd in seeds:
            if sd.message_id != target_id:
                continue

            meta = getattr(sd, "metadata", {}) or {}

            def m(key, default=None):
                if isinstance(meta, dict):
                    return meta.get(key, default)
                return getattr(meta, key, default)

            length = m("length")
            factor = m("factor", 1)
            offset = m("offset", 0)
            minimum = m("minimum")
            maximum = m("maximum")
            enum = m("enum")

            if length == 1:
                kind = "bool"
                coerce_int = True
                default_enum = [0, 1]
                default_min, default_max = 0, 1
            else:
                if factor not in (None, 0, 1):
                    kind = "float"
                    coerce_int = False
                else:
                    kind = "int"
                    coerce_int = True
                default_enum = None
                default_min, default_max = minimum, maximum

            # enum 값이 지나치게 적고 실제 연속값 신호일 가능성이 큰 경우 enum 검사 완화
            if enum is not None and len(enum) <= 3 and kind in ("int", "float") and length not in (1, None):
                relaxed_enum = None
            else:
                relaxed_enum = enum if enum is not None else default_enum

            rules[sd.signal_name] = {
                "kind": kind,
                "enum": relaxed_enum,
                "min": default_min,
                "max": default_max,
                "factor": factor if factor not in (None, 0) else 1,
                "offset": offset if offset is not None else 0,
                "coerce_int": coerce_int,
            }

        return rules

    def __init__(
        self,
        channel: str,
        dbc_path: str,
        target_id: int,
        seed_db_path: str,
        rules: Optional[Dict[str, Dict[str, Any]]] = None,
        anomaly_threshold: float = 0.30,
        max_frames: int = 3,
        default_window_sec: float = 0.5,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.channel = channel
        self.dbc_path = dbc_path
        self.target_id = target_id
        self.rules = rules or {}

        self.anomaly_threshold = anomaly_threshold
        self.max_frames = max_frames
        self.default_window_sec = default_window_sec
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")
        self.db = cantools.database.load_file(self.dbc_path)
        self.msg_def = self.db.get_message_by_frame_id(self.target_id)

        if self.msg_def is None:
            raise ValueError(f"DBC에 ID 0x{self.target_id:X} 메시지 정의가 없습니다.")

        if not self.rules:
            manager = SeedManager(seed_db_path)
            seeds = manager.get_all()
            self.rules = self._infer_rules_from_seeds(seeds, self.target_id)
            print(f"[INFO] Seed DB로부터 {len(self.rules)}개의 신호 규칙을 불러왔습니다.")
            if not self.rules:
                print("[WARN] Seed DB에서 규칙을 찾지 못했습니다. 검증 없이 모니터링을 진행합니다.")

        self.events: List[Dict[str, Any]] = []

        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False

        self._weighted_fail_sum = 0.0
        self._weighted_total = 0.0
        self._critical_failure_count = 0
        self._observed_frames = 0
        self._last_reason = None

    def _reset_state(self):
        self.events.clear()
        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False

        self._weighted_fail_sum = 0.0
        self._weighted_total = 0.0
        self._critical_failure_count = 0
        self._observed_frames = 0
        self._last_reason = None

    def start(self, timeout: Optional[float] = None, max_frames: Optional[int] = None) -> float:
        """
        기존 호출 호환용.
        timeout은 이제 '관측창(window)' 의미로 사용하고,
        max_frames개까지만 target_id 프레임을 평가한다.
        """
        window_sec = timeout if timeout is not None else self.default_window_sec
        frame_limit = max_frames if max_frames is not None else self.max_frames

        print(f"[ INFO ] DBCMonitor: 0x{self.target_id:X} on {self.channel}")
        print(f"         DBC={self.dbc_path}, signals={list(self.rules.keys()) or '—'}")
        print(f"         window={window_sec}s, max_frames={frame_limit}, threshold={self.anomaly_threshold}")

        self._reset_state()
        self._status = "running"

        deadline = time.monotonic() + window_sec

        try:
            while time.monotonic() < deadline and self._observed_frames < frame_limit:
                remaining = max(0.0, deadline - time.monotonic())
                recv_timeout = min(0.1, remaining) if remaining > 0 else 0.0
                msg = self.bus.recv(timeout=recv_timeout)

                if not msg:
                    continue

                if msg.arbitration_id != self.target_id:
                    continue

                self._observed_frames += 1
                self._evaluate_frame(msg)

            self._finalize_verdict()

        finally:
            try:
                self.bus.shutdown()
            except Exception:
                pass

        print(
            f"[INFO] DBC Monitor finished - "
            f"status={self._status}, frames={self._observed_frames}, "
            f"score={self._fail_score:.3f}, anomalous={self._is_anomalous}"
        )
        return self._fail_score

    def _evaluate_frame(self, msg) -> None:
        try:
            decoded = self.msg_def.decode(
                bytes(msg.data),
                decode_choices=False,
                scaling=True,
            )
        except Exception as e:
            self._register_failure(
                metric="decode_error",
                sig="_frame",
                value=str(e),
                weight=self.weights["decode_error"],
                critical=True,
            )
            self._last_reason = f"decode_error: {e}"
            return

        for sig, rule in self.rules.items():
            if sig not in decoded:
                self._register_failure(
                    metric="missing_signal",
                    sig=sig,
                    value=None,
                    weight=self.weights["missing_signal"],
                    critical=True,
                )
                self._last_reason = f"missing_signal: {sig}"
                continue

            raw = decoded[sig]

            try:
                val = self._normalize_value(raw, rule)
            except Exception:
                self._register_failure(
                    metric="type_cast",
                    sig=sig,
                    value=raw,
                    weight=self.weights["type_cast"],
                    critical=False,
                )
                self._last_reason = f"type_cast: {sig}"
                continue

            self._check_rules(sig, val, rule)

    def _normalize_value(self, raw: Any, rule: Dict[str, Any]) -> Number:
        kind = rule.get("kind", "int")

        if kind == "bool":
            v = int(float(raw))
            return 1 if v != 0 else 0

        if kind == "int":
            if rule.get("coerce_int", True):
                return int(float(raw))
            if isinstance(raw, int):
                return raw
            return int(raw) if (isinstance(raw, float) and raw.is_integer()) else raw

        return float(raw)

    def _check_rules(self, sig: str, val: Number, rule: Dict[str, Any]) -> bool:
        ok = True

        enum_vals = rule.get("enum")
        if enum_vals is not None:
            enum_weight = self.weights["enum"]
            if rule.get("kind") == "float":
                enum_ok = any(float(val) == float(a) for a in enum_vals)
            else:
                enum_ok = val in set(int(a) for a in enum_vals)

            self._register_check(
                metric="enum",
                sig=sig,
                value=val,
                weight=enum_weight,
                ok=enum_ok,
            )
            if not enum_ok:
                ok = False
                self._last_reason = f"enum_violation: {sig}"

        sigmn, sigmx = rule.get("min"), rule.get("max")
        factor = rule.get("factor", 1) or 1
        offset = rule.get("offset", 0) or 0

        mn = sigmn if sigmn is None else (sigmn - offset) / factor
        mx = sigmx if sigmx is None else (sigmx - offset) / factor

        # range 하한
        if mn is not None:
            range_min_ok = float(val) >= float(mn)
            self._register_check(
                metric="range",
                sig=sig,
                value={"value": val, "bound": "min", "expected": mn},
                weight=self.weights["range"],
                ok=range_min_ok,
            )
            if not range_min_ok:
                ok = False
                self._last_reason = f"range_min_violation: {sig}"

        # range 상한
        if mx is not None:
            range_max_ok = float(val) <= float(mx)
            self._register_check(
                metric="range",
                sig=sig,
                value={"value": val, "bound": "max", "expected": mx},
                weight=self.weights["range"],
                ok=range_max_ok,
            )
            if not range_max_ok:
                ok = False
                self._last_reason = f"range_max_violation: {sig}"

        return ok

    def _register_check(self, metric: str, sig: str, value: Any, weight: float, ok: bool) -> None:
        self._weighted_total += weight
        if ok:
            self._emit(metric, sig, value, "OK")
        else:
            self._weighted_fail_sum += weight
            self._emit(metric, sig, value, "FAIL")

    def _register_failure(
        self,
        metric: str,
        sig: str,
        value: Any,
        weight: float,
        critical: bool = False,
    ) -> None:
        self._weighted_total += weight
        self._weighted_fail_sum += weight
        self._emit(metric, sig, value, "FAIL")

        if critical:
            self._critical_failure_count += 1

    def _finalize_verdict(self):
        if self._observed_frames == 0:
            self._status = "timeout"
            self._fail_score = 0.0
            self._is_anomalous = False
            return

        if self._weighted_total <= 0:
            self._fail_score = 0.0
        else:
            self._fail_score = min(self._weighted_fail_sum / self._weighted_total, 1.0)

        self._is_anomalous = (
            self._critical_failure_count >= 1
            or self._fail_score >= self.anomaly_threshold
        )
        self._status = "ok"

    def _emit(self, metric: str, sig: str, value: Any, status: str):
        ev = {
            "type": "dbc",
            "id": self.target_id,
            "metric": metric,
            "signal": sig,
            "value": value,
            "status": status,
            "ts_ms": int(time.time() * 1000),
        }
        self.events.append(ev)
        log_event("dbc", self.target_id, f"{sig}:{metric}", value, status)

    def fetch_events(self):
        out = self.events[:]
        self.events.clear()
        return out

    def get_fail_score(self) -> float:
        return self._fail_score

    def is_anomalous(self) -> bool:
        return self._is_anomalous

    def get_status(self) -> str:
        return self._status

    def get_summary(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "score": self._fail_score,
            "is_anomalous": self._is_anomalous,
            "observed_frames": self._observed_frames,
            "critical_failure_count": self._critical_failure_count,
            "weighted_fail_sum": self._weighted_fail_sum,
            "weighted_total": self._weighted_total,
            "last_reason": self._last_reason,
        }