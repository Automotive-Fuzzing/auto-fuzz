# src/monitor/timing_monitor.py

import can
import time
from ..logger.base_logger import log_event
from typing import Optional, Dict, Any


CAN_CHANNEL = "can0"
TARGET_ID = 0x6A6
EXPECTED_CYCLE_MS = 500
TOLERANCE_MS = 50
LOG_INTERVAL = 10
MAX_TIMEOUT = 5.0


class TimingMonitor:
    def __init__(self,
                 channel: str = CAN_CHANNEL,
                 target_id: int = TARGET_ID,
                 expected_cycle: int = EXPECTED_CYCLE_MS,
                 tolerance: int = TOLERANCE_MS,
                 anomaly_threshold: float = 0.3,
                 min_fail_count: int = 2):
        self.channel = channel
        self.target_id = target_id
        self.expected = expected_cycle
        self.tolerance = tolerance
        self.anomaly_threshold = anomaly_threshold
        self.min_fail_count = min_fail_count

        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")

        self.prev_time = None
        self.events = []
        self._frame_counter = 0
        self._cycle_list = []
        self._fail_score = 0.0
        self._total_frames = 0
        self._fail_count = 0
        self._ignored_burst_count = 0

        self._status = "idle"
        self._is_anomalous = False

    def start(self, timeout: Optional[float] = None) -> float:
        print(f"[ INFO ] Monitoring 0x{self.target_id:X} "
              f"(Cycle={self.expected}ms ±{self.tolerance}ms) on {self.channel}")

        if timeout:
            print(f"[ INFO ] Timeout set to {timeout} seconds")

        monitor_timeout = timeout or MAX_TIMEOUT
        start_time = time.monotonic()

        self._fail_score = 0.0
        self._total_frames = 0
        self._fail_count = 0
        self._frame_counter = 0
        self._cycle_list.clear()
        self.prev_time = None
        self._ignored_burst_count = 0
        self._status = "running"
        self._is_anomalous = False

        try:
            while True:
                if (time.monotonic() - start_time) >= monitor_timeout:
                    print(f"[ INFO ] Timing Monitor timeout reached ({monitor_timeout}s)")
                    break

                msg = self.bus.recv(timeout=1.0)
                if not msg:
                    continue

                if msg.arbitration_id != self.target_id:
                    continue

                now = (msg.timestamp * 1000.0) if getattr(msg, "timestamp", None) else (time.time() * 1000.0)

                if self.prev_time is None:
                    self.prev_time = now
                    continue

                cycle = now - self.prev_time
                self.prev_time = now

                if cycle < (self.expected * 0.5):
                    self._ignored_burst_count += 1
                    print(f"[SKIP] Burst cycle ignored: {cycle:.2f} ms")
                    continue

                lower = self.expected - self.tolerance
                upper = self.expected + self.tolerance
                status = "OK" if lower <= cycle <= upper else "FAIL"

                if status == "FAIL":
                    if cycle < lower:
                        error = lower - cycle
                    else:
                        error = cycle - upper
                    self._fail_score += error
                    self._fail_count += 1

                self._total_frames += 1

                event = {
                    "type": "timing",
                    "id": self.target_id,
                    "metric": "cycle_time",
                    "value": round(cycle, 2),
                    "status": status
                }
                self.events.append(event)
                log_event("timing", self.target_id, "cycle_time", cycle, status)

                print(f"[{status}] Cycle: {cycle:.2f} ms")

                self._frame_counter += 1
                self._cycle_list.append(cycle)
                if self._frame_counter % LOG_INTERVAL == 0:
                    avg_cycle = sum(self._cycle_list[-LOG_INTERVAL:]) / LOG_INTERVAL
                    print(f"    └ Average cycle (last {LOG_INTERVAL}): {avg_cycle:.2f} ms")

        finally:
            try:
                self.bus.shutdown()
            except Exception:
                pass

        normalized_score = self._normalize_fail_score(monitor_timeout)

        if self._total_frames == 0:
            self._status = "timeout"
            self._is_anomalous = False
        else:
            self._status = "ok"
            self._is_anomalous = (
                self._fail_count >= self.min_fail_count
                or normalized_score >= self.anomaly_threshold
            )

        print(f"[ INFO ] Timing Monitor finished")
        print(f"    └ Total FAIL score: {self._fail_score:.2f} ms")
        print(f"    └ Total frames: {self._total_frames}")
        print(f"    └ Fail count: {self._fail_count}")
        print(f"    └ Ignored burst frames: {self._ignored_burst_count}")
        print(f"    └ Normalized score (0~1): {normalized_score:.4f}")
        print(f"    └ Status: {self._status}, anomalous={self._is_anomalous}")

        return normalized_score

    def _normalize_fail_score(self, timeout: float) -> float:
        if self._total_frames == 0:
            return 0.0

        max_possible_frames = max((timeout * 1000.0) / self.expected, 1.0)
        worst_case_score = max_possible_frames * self.tolerance
        normalized = min(self._fail_score / worst_case_score, 1.0)
        return normalized

    def get_fail_score(self) -> float:
        return self._normalize_fail_score(MAX_TIMEOUT)

    def get_raw_fail_score(self) -> float:
        return self._fail_score

    def get_status(self) -> str:
        return self._status

    def is_anomalous(self) -> bool:
        return self._is_anomalous

    def get_summary(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "score": self._normalize_fail_score(MAX_TIMEOUT),
            "is_anomalous": self._is_anomalous,
            "total_frames": self._total_frames,
            "fail_count": self._fail_count,
            "ignored_burst_count": self._ignored_burst_count,
            "raw_fail_score": self._fail_score,
        }

    def fetch_events(self):
        events_copy = self.events[:]
        self.events.clear()
        return events_copy