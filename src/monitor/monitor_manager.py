# src/monitor/monitor_manager.py

from __future__ import annotations

import threading
from typing import Dict, Optional, Any

from .timing_monitor import TimingMonitor
from .uds_monitor import UDSMonitor
from .dbc_monitor import DBCMonitor


class MonitorManager:
    def __init__(
        self,
        timing_monitor: Optional[TimingMonitor] = None,
        uds_monitor: Optional[UDSMonitor] = None,
        dbc_monitor: Optional[DBCMonitor] = None,
        thread_timeout_as_anomaly: bool = False,
        crash_as_anomaly: bool = True,
    ):
        self.timing_monitor = timing_monitor
        self.uds_monitor = uds_monitor
        self.dbc_monitor = dbc_monitor

        self.thread_timeout_as_anomaly = thread_timeout_as_anomaly
        self.crash_as_anomaly = crash_as_anomaly

        self.threads: Dict[str, threading.Thread] = {}

        self.scores: Dict[str, float] = {
            "timing": 0.0,
            "uds": 0.0,
            "dbc": 0.0,
        }
        self.status: Dict[str, str] = {
            "timing": "pending",
            "uds": "pending",
            "dbc": "pending",
        }
        self.completed: Dict[str, bool] = {
            "timing": False,
            "uds": False,
            "dbc": False,
        }
        self.anomalies: Dict[str, bool] = {
            "timing": False,
            "uds": False,
            "dbc": False,
        }
        self.details: Dict[str, Dict[str, Any]] = {
            "timing": {},
            "uds": {},
            "dbc": {},
        }

        self.state_lock = threading.Lock()
        self.running = False

    def start_monitors(
        self,
        timing_timeout: Optional[float] = 5.0,
        dbc_timeout: Optional[float] = 5.0,
    ):
        if self.running:
            print("[WARN] Monitors are already running")
            return

        self.running = True
        print("[INFO] Starting monitors...")

        self.threads = {}

        with self.state_lock:
            self.scores = {"timing": 0.0, "uds": 0.0, "dbc": 0.0}
            self.completed = {"timing": False, "uds": False, "dbc": False}
            self.status = {"timing": "pending", "uds": "pending", "dbc": "pending"}
            self.anomalies = {"timing": False, "uds": False, "dbc": False}
            self.details = {"timing": {}, "uds": {}, "dbc": {}}

        if self.timing_monitor:
            with self.state_lock:
                self.status["timing"] = "running"

            thread = threading.Thread(
                target=self._run_timing_monitor,
                args=(timing_timeout,),
                daemon=True,
                name="TimingMonitor",
            )
            self.threads["timing"] = thread
            thread.start()
            print("[INFO] ✓ Timing Monitor started")
        else:
            with self.state_lock:
                self.completed["timing"] = True
                self.status["timing"] = "skipped"

        if self.uds_monitor:
            with self.state_lock:
                self.status["uds"] = "running"

            thread = threading.Thread(
                target=self._run_uds_monitor,
                daemon=True,
                name="UDSMonitor",
            )
            self.threads["uds"] = thread
            thread.start()
            print("[INFO] ✓ UDS Monitor started")
        else:
            with self.state_lock:
                self.completed["uds"] = True
                self.status["uds"] = "skipped"

        if self.dbc_monitor:
            with self.state_lock:
                self.status["dbc"] = "running"

            thread = threading.Thread(
                target=self._run_dbc_monitor,
                args=(dbc_timeout,),
                daemon=True,
                name="DBCMonitor",
            )
            self.threads["dbc"] = thread
            thread.start()
            print("[INFO] ✓ DBC Monitor started")
        else:
            with self.state_lock:
                self.completed["dbc"] = True
                self.status["dbc"] = "skipped"

        print(f"[INFO] Total {len(self.threads)} monitor(s) running")

    def _run_timing_monitor(self, timeout: Optional[float]):
        try:
            fail_score = self.timing_monitor.start(timeout=timeout)
            timing_status = self.timing_monitor.get_status()
            timing_is_anomalous = self.timing_monitor.is_anomalous()
            timing_summary = self.timing_monitor.get_summary()

            with self.state_lock:
                if self.status["timing"] == "timeout":
                    return

                self.scores["timing"] = fail_score
                self.completed["timing"] = True
                self.status["timing"] = timing_status
                self.anomalies["timing"] = timing_is_anomalous
                self.details["timing"] = {
                    "score": fail_score,
                    "status": timing_status,
                    "is_anomalous": timing_is_anomalous,
                    "summary": timing_summary,
                }

            print(
                f"[INFO] Timing Monitor completed - "
                f"Score: {fail_score}, status={timing_status}, anomalous={timing_is_anomalous}"
            )

        except Exception as e:
            print(f"[ERROR] Timing Monitor crashed: {e}")
            with self.state_lock:
                if self.status["timing"] == "timeout":
                    return

                self.completed["timing"] = True
                self.status["timing"] = "crashed"
                self.anomalies["timing"] = self.crash_as_anomaly
                self.details["timing"] = {
                    "score": 0.0,
                    "status": "crashed",
                    "is_anomalous": self.crash_as_anomaly,
                    "summary": {"last_reason": str(e)},
                }

    def _run_uds_monitor(self):
        try:
            fail_score = self.uds_monitor.start()
            uds_status = self.uds_monitor.get_status()
            uds_is_anomalous = self.uds_monitor.is_anomalous()
            uds_summary = self.uds_monitor.get_summary()

            with self.state_lock:
                if self.status["uds"] == "timeout":
                    return

                self.scores["uds"] = fail_score
                self.completed["uds"] = True
                self.status["uds"] = uds_status
                self.anomalies["uds"] = uds_is_anomalous
                self.details["uds"] = {
                    "score": fail_score,
                    "status": uds_status,
                    "is_anomalous": uds_is_anomalous,
                    "summary": uds_summary,
                }

            print(
                f"[INFO] UDS Monitor completed - "
                f"Score: {fail_score}, status={uds_status}, anomalous={uds_is_anomalous}"
            )

        except Exception as e:
            print(f"[ERROR] UDS Monitor crashed: {e}")
            with self.state_lock:
                if self.status["uds"] == "timeout":
                    return

                self.completed["uds"] = True
                self.status["uds"] = "crashed"
                self.anomalies["uds"] = self.crash_as_anomaly
                self.details["uds"] = {
                    "score": 0.0,
                    "status": "crashed",
                    "is_anomalous": self.crash_as_anomaly,
                    "summary": {"last_reason": str(e)},
                }

    def _run_dbc_monitor(self, timeout: Optional[float]):
        try:
            fail_score = self.dbc_monitor.start(timeout=timeout)
            dbc_status = self.dbc_monitor.get_status()
            dbc_is_anomalous = self.dbc_monitor.is_anomalous()
            dbc_summary = self.dbc_monitor.get_summary()

            with self.state_lock:
                if self.status["dbc"] == "timeout":
                    return

                self.scores["dbc"] = fail_score
                self.completed["dbc"] = True
                self.status["dbc"] = dbc_status
                self.anomalies["dbc"] = dbc_is_anomalous
                self.details["dbc"] = {
                    "score": fail_score,
                    "status": dbc_status,
                    "is_anomalous": dbc_is_anomalous,
                    "summary": dbc_summary,
                }

            print(
                f"[INFO] DBC Monitor completed - "
                f"Score: {fail_score}, status={dbc_status}, anomalous={dbc_is_anomalous}"
            )

        except Exception as e:
            print(f"[ERROR] DBC Monitor crashed: {e}")
            with self.state_lock:
                if self.status["dbc"] == "timeout":
                    return

                self.completed["dbc"] = True
                self.status["dbc"] = "crashed"
                self.anomalies["dbc"] = self.crash_as_anomaly
                self.details["dbc"] = {
                    "score": 0.0,
                    "status": "crashed",
                    "is_anomalous": self.crash_as_anomaly,
                    "summary": {"last_reason": str(e)},
                }

    def wait_for_completion(self, timeout: Optional[float] = None):
        for name, thread in self.threads.items():
            if not thread.is_alive():
                continue

            thread.join(timeout=timeout)

            if thread.is_alive() and timeout is not None:
                print(f"[WARN] {name} thread is still running (timeout reached)")
                with self.state_lock:
                    self.completed[name] = True
                    self.status[name] = "timeout"
                    self.anomalies[name] = self.thread_timeout_as_anomaly
                    self.details[name] = {
                        "score": self.scores.get(name, 0.0),
                        "status": "timeout",
                        "is_anomalous": self.thread_timeout_as_anomaly,
                    }

        self.running = False
        print("[INFO] All monitors completed (or timed out)")

    def is_all_completed(self) -> bool:
        with self.state_lock:
            return all(self.completed.values())

    def get_completion_status(self) -> Dict[str, bool]:
        with self.state_lock:
            return self.completed.copy()

    def get_status(self) -> Dict[str, str]:
        with self.state_lock:
            return self.status.copy()

    def get_scores(self) -> Dict[str, float]:
        with self.state_lock:
            return self.scores.copy()

    def get_anomalies(self) -> Dict[str, bool]:
        with self.state_lock:
            return self.anomalies.copy()

    def get_details(self) -> Dict[str, Dict[str, Any]]:
        with self.state_lock:
            return {k: dict(v) for k, v in self.details.items()}

    def is_running(self) -> bool:
        return self.running