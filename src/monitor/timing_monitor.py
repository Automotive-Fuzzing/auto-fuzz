# src/monitor/timing_monitor.py

from __future__ import annotations

import time
from typing import Optional, Dict, Any

import can

from ..logger.base_logger import log_event


CAN_CHANNEL = "can0"
TARGET_ID = 0x6A6
EXPECTED_CYCLE_MS = 500
TOLERANCE_MS = 50
LOG_INTERVAL = 10
MAX_TIMEOUT = 5.0


class TimingMonitor:
    def __init__(
        self,
        channel: str = CAN_CHANNEL,
        target_id: int = TARGET_ID,
        expected_cycle: int = EXPECTED_CYCLE_MS,
        tolerance: int = TOLERANCE_MS,
        anomaly_threshold: float = 0.25,
        min_fail_count: int = 2,
        min_observed_frames: int = 3,
        ignore_first_fail: bool = True,
        burst_ratio: float = 0.35,
        burst_escalation_count: int = 3,
        severe_deviation_multiplier: float = 3.0,
        raw_fail_score_threshold: float = 150.0,
        no_frame_status: str = "timeout",
    ):
        self.channel = channel
        self.target_id = target_id
        self.expected = expected_cycle
        self.tolerance = tolerance

        self.anomaly_threshold = anomaly_threshold
        self.min_fail_count = min_fail_count
        self.min_observed_frames = min_observed_frames
        self.ignore_first_fail = ignore_first_fail

        self.burst_ratio = burst_ratio
        self.burst_escalation_count = burst_escalation_count
        self.severe_deviation_multiplier = severe_deviation_multiplier
        self.raw_fail_score_threshold = raw_fail_score_threshold
        self.no_frame_status = no_frame_status

        self.bus: Optional[can.BusABC] = None

        self.prev_time: Optional[float] = None
        self.events = []
        self._frame_counter = 0
        self._cycle_list = []

        self._fail_score = 0.0
        self._raw_fail_score = 0.0
        self._total_frames = 0
        self._fail_count = 0
        self._ignored_burst_count = 0
        self._burst_streak = 0
        self._max_burst_streak = 0
        self._max_deviation = 0.0
        self._last_reason: Optional[str] = None

        self._status = "idle"
        self._is_anomalous = False

    @classmethod
    def from_config(
        cls,
        cfg: Optional[Dict[str, Any]],
        *,
        channel: str = CAN_CHANNEL,
        target_id: int = TARGET_ID,
    ) -> "TimingMonitor":
        cfg = cfg or {}
        return cls(
            channel=channel,
            target_id=target_id,
            expected_cycle=cfg.get("expected_cycle_ms", EXPECTED_CYCLE_MS),
            tolerance=cfg.get("tolerance_ms", TOLERANCE_MS),
            anomaly_threshold=cfg.get("anomaly_threshold", 0.25),
            min_fail_count=cfg.get("min_fail_count", 2),
            min_observed_frames=cfg.get("min_observed_frames", 3),
            ignore_first_fail=cfg.get("ignore_first_fail", True),
            burst_ratio=cfg.get("burst_ratio", 0.35),
            burst_escalation_count=cfg.get("burst_escalation_count", 3),
            severe_deviation_multiplier=cfg.get("severe_deviation_multiplier", 3.0),
            raw_fail_score_threshold=cfg.get("raw_fail_score_threshold", 150.0),
            no_frame_status=cfg.get("no_frame_status", "timeout"),
        )

    def _reset_state(self) -> None:
        self.prev_time = None
        self.events.clear()
        self._frame_counter = 0
        self._cycle_list.clear()

        self._fail_score = 0.0
        self._raw_fail_score = 0.0
        self._total_frames = 0
        self._fail_count = 0
        self._ignored_burst_count = 0
        self._burst_streak = 0
        self._max_burst_streak = 0
        self._max_deviation = 0.0
        self._last_reason = None

        self._status = "idle"
        self._is_anomalous = False

    def start(self, timeout: Optional[float] = None) -> float:
        print(
            f"[ INFO ] Monitoring 0x{self.target_id:X} "
            f"(Cycle={self.expected}ms ±{self.tolerance}ms) on {self.channel}"
        )

        if timeout:
            print(f"[ INFO ] Timeout set to {timeout} seconds")

        monitor_timeout = timeout or MAX_TIMEOUT
        start_time = time.monotonic()

        self._reset_state()
        self._status = "running"

        try:
            self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")

            while True:
                if (time.monotonic() - start_time) >= monitor_timeout:
                    print(f"[ INFO ] Timing Monitor timeout reached ({monitor_timeout}s)")
                    break

                msg = self.bus.recv(timeout=1.0)
                if not msg:
                    continue

                if msg.arbitration_id != self.target_id:
                    continue

                now = (
                    msg.timestamp * 1000.0
                    if getattr(msg, "timestamp", None) is not None
                    else time.time() * 1000.0
                )

                if self.prev_time is None:
                    self.prev_time = now
                    continue

                cycle = now - self.prev_time
                self.prev_time = now

                self._evaluate_cycle(cycle)

        finally:
            if self.bus is not None:
                try:
                    self.bus.shutdown()
                except Exception:
                    pass
                self.bus = None

        self._fail_score = self._normalize_fail_score()
        self._finalize_verdict()

        print("[ INFO ] Timing Monitor finished")
        print(f"    └ Raw FAIL score: {self._raw_fail_score:.2f} ms")
        print(f"    └ Normalized score (fail ratio): {self._fail_score:.4f}")
        print(f"    └ Total frames: {self._total_frames}")
        print(f"    └ Fail count: {self._fail_count}")
        print(f"    └ Ignored burst frames: {self._ignored_burst_count}")
        print(f"    └ Max deviation: {self._max_deviation:.2f} ms")
        print(f"    └ Max burst streak: {self._max_burst_streak}")
        print(f"    └ Status: {self._status}, anomalous={self._is_anomalous}")

        return self._fail_score

    def _evaluate_cycle(self, cycle: float) -> None:
        lower = self.expected - self.tolerance
        upper = self.expected + self.tolerance
        status = "OK"
        error = 0.0
        reason = None

        if cycle < (self.expected * self.burst_ratio):
            self._ignored_burst_count += 1
            self._burst_streak += 1
            self._max_burst_streak = max(self._max_burst_streak, self._burst_streak)

            error = max(lower - cycle, 0.0)
            reason = f"burst_cycle:{cycle:.2f}"
            status = "FAIL"

            self._raw_fail_score += error * 0.5
            self._fail_count += 1
            self._last_reason = reason

            print(f"[WARN] Burst-like cycle detected: {cycle:.2f} ms")
        else:
            self._burst_streak = 0

            if lower <= cycle <= upper:
                status = "OK"
            else:
                status = "FAIL"
                if cycle < lower:
                    error = lower - cycle
                    reason = f"cycle_too_fast:{cycle:.2f}"
                else:
                    error = cycle - upper
                    reason = f"cycle_too_slow:{cycle:.2f}"

                self._raw_fail_score += error
                self._fail_count += 1
                self._last_reason = reason

        deviation = abs(cycle - self.expected)
        self._max_deviation = max(self._max_deviation, deviation)
        self._total_frames += 1

        event = {
            "type": "timing",
            "id": self.target_id,
            "metric": "cycle_time",
            "value": round(cycle, 2),
            "status": status,
        }
        self.events.append(event)
        log_event("timing", self.target_id, "cycle_time", cycle, status)

        print(f"[{status}] Cycle: {cycle:.2f} ms")

        self._frame_counter += 1
        self._cycle_list.append(cycle)
        if self._frame_counter % LOG_INTERVAL == 0:
            avg_cycle = sum(self._cycle_list[-LOG_INTERVAL:]) / LOG_INTERVAL
            print(f"    └ Average cycle (last {LOG_INTERVAL}): {avg_cycle:.2f} ms")

    def _finalize_verdict(self) -> None:
        if self._total_frames == 0:
            self._status = self.no_frame_status
            self._is_anomalous = False
            return

        if self._total_frames < self.min_observed_frames:
            self._status = "insufficient_observation"
            self._is_anomalous = False
            return

        self._status = "ok"

        effective_fail_count = self._fail_count
        if self.ignore_first_fail and effective_fail_count > 0:
            effective_fail_count -= 1

        severe_deviation_threshold = self.tolerance * self.severe_deviation_multiplier

        self._is_anomalous = (
            effective_fail_count >= self.min_fail_count
            or self._fail_score >= self.anomaly_threshold
            or self._raw_fail_score >= self.raw_fail_score_threshold
            or self._max_deviation >= severe_deviation_threshold
            or self._max_burst_streak >= self.burst_escalation_count
        )

    def _normalize_fail_score(self) -> float:
        if self._total_frames == 0:
            return 0.0

        effective_fail_count = self._fail_count
        if self.ignore_first_fail and effective_fail_count > 0:
            effective_fail_count -= 1

        normalized = effective_fail_count / max(self._total_frames, 1)
        return min(max(normalized, 0.0), 1.0)

    def get_fail_score(self) -> float:
        return self._fail_score

    def get_raw_fail_score(self) -> float:
        return self._raw_fail_score

    def get_status(self) -> str:
        return self._status

    def is_anomalous(self) -> bool:
        return self._is_anomalous

    def get_summary(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "score": self._fail_score,
            "is_anomalous": self._is_anomalous,
            "total_frames": self._total_frames,
            "fail_count": self._fail_count,
            "ignored_burst_count": self._ignored_burst_count,
            "raw_fail_score": self._raw_fail_score,
            "max_deviation": self._max_deviation,
            "max_burst_streak": self._max_burst_streak,
            "last_reason": self._last_reason,
        }

    def fetch_events(self):
        events_copy = self.events[:]
        self.events.clear()
        return events_copy