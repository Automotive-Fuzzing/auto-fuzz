# src/monitor/dbc_monitor.py

import time
import can
import cantools
from typing import Dict, Any, Optional, List, Union
from ..logger.base_logger import log_event
from ..seeds.seed_manager import SeedManager

Number = Union[int, float]


class DBCMonitor:
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

            length  = m("length")
            factor  = m("factor", 1)
            offset  = m("offset", 0)
            minimum = m("minimum")
            maximum = m("maximum")
            enum    = m("enum")
            mono    = m("monotonic")

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

            # enum 값이 지나치게 적고, 실제 연속값 신호일 가능성이 큰 경우 enum 검사 완화
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
                "monotonic": mono if mono in ("nondecreasing", "nonincreasing") else None,
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
    ):
        self.channel = channel
        self.dbc_path = dbc_path
        self.target_id = target_id
        self.rules = rules or {}

        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")
        self.db  = cantools.database.load_file(self.dbc_path)
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

        self._prev_values: Dict[str, Number] = {}
        self.events: List[Dict[str, Any]] = []
        self._fail_score = 0.0
        self._fail_count = 0
        self._total_checks = 0
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3   # 3회 연속 이상일 때만 심각한 오류로 간주

    def start(self, timeout: Optional[float] = None) -> float:
        print(f"[ INFO ] DBCMonitor: 0x{self.target_id:X} on {self.channel}")
        print(f"         DBC={self.dbc_path}, signals={list(self.rules.keys()) or '—'}")

        self._fail_score = 0.0
        self._fail_count = 0
        self._total_checks = 0
        self._consecutive_failures = 0
        self._prev_values.clear()

        start_time = time.time()

        try:
            while True:
                if timeout is not None and (time.time() - start_time) >= timeout:
                    print(f"[INFO] DBC Monitor timeout reached ({timeout}s)")
                    break

                msg = self.bus.recv(timeout=3.0)
                if not msg or msg.arbitration_id != self.target_id:
                    continue

                try:
                    decoded = self.msg_def.decode(
                        bytes(msg.data),
                        decode_choices=False,
                        scaling=True
                    )
                except Exception as e:
                    self._emit("decode_error", "_frame", str(e), "FAIL")
                    self._fail_count += 1
                    self._total_checks += 1
                    self._update_fail_score()
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        print(f"[WARN] DBC decode 연속 실패: {e}")
                    continue

                frame_failed = False

                for sig, rule in self.rules.items():
                    if sig not in decoded:
                        self._emit("missing_signal", sig, None, "FAIL")
                        self._fail_count += 1
                        self._total_checks += 1
                        frame_failed = True
                        continue

                    raw = decoded[sig]

                    try:
                        val = self._normalize_value(raw, rule)
                    except Exception:
                        self._emit("type_cast", sig, raw, "FAIL")
                        self._fail_count += 1
                        self._total_checks += 1
                        frame_failed = True
                        continue

                    ok = self._check_rules(sig, val, rule)
                    if not ok:
                        frame_failed = True

                if frame_failed:
                    self._consecutive_failures += 1
                else:
                    self._consecutive_failures = 0

                self._update_fail_score()

        finally:
            try:
                self.bus.shutdown()
            except Exception:
                pass

        print(f"[INFO] DBC Monitor finished - FAIL score: {self._fail_score:.3f}")
        return self._fail_score

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
            self._total_checks += 1
            if rule.get("kind") == "float":
                enum_ok = any(float(val) == float(a) for a in enum_vals)
            else:
                enum_ok = val in set(int(a) for a in enum_vals)

            self._emit("enum", sig, val, "OK" if enum_ok else "FAIL")
            if not enum_ok:
                self._fail_count += 1
                ok = False

        sigmn, sigmx = rule.get("min"), rule.get("max")
        factor = rule.get("factor", 1) or 1
        offset = rule.get("offset", 0) or 0

        mn = sigmn if sigmn is None else (sigmn - offset) / factor
        mx = sigmx if sigmx is None else (sigmx - offset) / factor

        if mn is not None:
            self._total_checks += 1
            if float(val) < float(mn):
                self._fail_count += 1
                self._emit("range", sig, val, "FAIL")
                ok = False

        if mx is not None:
            self._total_checks += 1
            if float(val) > float(mx):
                self._fail_count += 1
                self._emit("range", sig, val, "FAIL")
                ok = False

        if (mn is not None or mx is not None) and ok:
            self._emit("range", sig, val, "OK")

        mode = rule.get("monotonic")
        if mode:
            prev = self._prev_values.get(sig)
            if prev is not None:
                self._total_checks += 1
                pv = float(prev)
                cv = float(val)

                if mode == "nondecreasing" and cv < pv:
                    self._fail_count += 1
                    self._emit("monotonic", sig, {"prev": prev, "cur": val, "mode": mode}, "FAIL")
                    ok = False

                elif mode == "nonincreasing" and cv > pv:
                    self._fail_count += 1
                    self._emit("monotonic", sig, {"prev": prev, "cur": val, "mode": mode}, "FAIL")
                    ok = False
                else:
                    self._emit("monotonic", sig, {"prev": prev, "cur": val, "mode": mode}, "OK")

        self._prev_values[sig] = val
        return ok

    def _update_fail_score(self):
        if self._total_checks == 0:
            self._fail_score = 0.0
            return
        self._fail_score = min(self._fail_count / self._total_checks, 1.0)

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