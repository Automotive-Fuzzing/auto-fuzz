# src/monitor/monitor_manager.py

import threading
import time
from .timing_monitor import TimingMonitor
from .dbc_monitor import DBCMonitor
# from .uds_monitor import UDSMonitor

class MonitorManager:
    def __init__(self, vehicle="audi_a5"):
        self.vehicle = vehicle
        self.monitors = []
        self._threads = []
        self._stop_flag = False

    def init_monitors(self):
        self.monitors = [
            TimingMonitor(channel="can0", target_id=0x366),
            # DBCMonitor(vehicle=self.vehicle),
            # UDSMonitor(vehicle=self.vehicle),
        ]

    def start_all(self):
        self.init_monitors()
        print("[ INFO ] Starting all monitors...")
        for m in self.monitors:
            t = threading.Thread(target=m.start, daemon=True)
            t.start()
            self._threads.append(t)

    def collect_events(self):
        """각 모니터의 이벤트 버퍼에서 결과를 취합"""
        all_events = []
        for m in self.monitors:
            all_events.extend(m.fetch_events())
        return all_events

    def stop_all(self):
        self._stop_flag = True
        print("[ INFO ] Stopping all monitors...")
        for t in self._threads:
            t.join(timeout=1)
