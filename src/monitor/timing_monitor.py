# src/monitor/timing_monitor.py

import can
import time
from ..logger.base_logger import log_event
from typing import Optional


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
                 tolerance: int = TOLERANCE_MS):
        self.channel = channel
        self.target_id = target_id
        self.expected = expected_cycle
        self.tolerance = tolerance

        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")

        self.prev_time = None
        self.events = []
        self._frame_counter = 0
        self._cycle_list = []
        self._fail_score = 0.0
        self._total_frames = 0

        # 추가
        self._warmup_frames = 1          # 첫 프레임 간격은 평가하지 않음
        self._ignored_burst_count = 0

    def start(self, timeout: Optional[float] = None) -> float:
        print(f"[ INFO ] Monitoring 0x{self.target_id:X} "
              f"(Cycle={self.expected}ms ±{self.tolerance}ms) on {self.channel}")

        if timeout:
            print(f"[ INFO ] Timeout set to {timeout} seconds")

        start_time = time.time()
        self._fail_score = 0.0
        self._total_frames = 0
        self._frame_counter = 0
        self._cycle_list.clear()
        self.prev_time = None
        self._ignored_burst_count = 0

        try:
            while True:
                if timeout and (time.time() - start_time) >= timeout:
                    print(f"[ INFO ] Timing Monitor timeout reached ({timeout}s)")
                    break

                msg = self.bus.recv(timeout=1.0)
                if not msg:
                    continue

                if msg.arbitration_id != self.target_id:
                    continue

                # 가능하면 CAN 프레임의 timestamp 사용
                now = (msg.timestamp * 1000.0) if getattr(msg, "timestamp", None) else (time.time() * 1000.0)

                if self.prev_time is None:
                    self.prev_time = now
                    continue

                cycle = now - self.prev_time
                self.prev_time = now

                # 너무 짧은 간격은 버스트/중복 수신 가능성이 높으니 평가 제외
                # 500ms 기준이면 250ms 미만은 timing anomaly가 아니라 큐/버퍼/중복일 가능성이 큼
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

        normalized_score = self._normalize_fail_score(timeout or MAX_TIMEOUT)
        print(f"[ INFO ] Timing Monitor finished")
        print(f"    └ Total FAIL score: {self._fail_score:.2f} ms")
        print(f"    └ Total frames: {self._total_frames}")
        print(f"    └ Ignored burst frames: {self._ignored_burst_count}")
        print(f"    └ Normalized score (0~1): {normalized_score:.4f}")

        return normalized_score

    def _normalize_fail_score(self, timeout: float) -> float:
        if self._total_frames == 0:
            return 0.0

        # 5초 / 500ms = 약 10프레임
        max_possible_frames = max((timeout * 1000.0) / self.expected, 1.0)
        worst_case_score = max_possible_frames * self.tolerance
        normalized = min(self._fail_score / worst_case_score, 1.0)
        return normalized

    def get_fail_score(self) -> float:
        return self._normalize_fail_score(MAX_TIMEOUT)

    def get_raw_fail_score(self) -> float:
        return self._fail_score

    def fetch_events(self):
        events_copy = self.events[:]
        self.events.clear()
        return events_copy