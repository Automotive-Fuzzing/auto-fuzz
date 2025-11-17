# src/pipeline.py
import time
import json
from typing import Optional, Dict, Any

from seeds.dbc_parser import DbcParser
from seeds.seed_manager import SeedManager, Seed
from seeds.seed_queue import SeedQueue

from monitor.monitor_manager import MonitorManager
from monitor.timing_monitor import TimingMonitor
from monitor.uds_monitor import UDSMonitor
from monitor.dbc_monitor import DBCMonitor


class AutoFuzzPipeline:
    @staticmethod
    def register_seeds(dbc_path: str, db_path: str = "seeds.db") -> int:
        parser = DbcParser(dbc_path)
        parsed = parser.parse()

        manager = SeedManager(db_path)
        seed_groups = manager.from_dbc(parsed)
        queue = SeedQueue(db_path)

        total = 0
        for group in seed_groups:
            queue.push_group(group)
            total += len(group)

        queue.close()
        manager.close()
        return total

    @staticmethod
    def list_seeds(db_path: str = "seeds.db"):
        manager = SeedManager(db_path)
        seeds = manager.get_all()
        manager.close()
        return seeds

    def __init__(self, db_path: str = "seeds.db", can_iface: Optional[object] = None):
        self.queue = SeedQueue(db_path)
        self.can = can_iface
        self.running = False

        self.monitor_manager = MonitorManager(
            timing_monitor=TimingMonitor(),
            uds_monitor=UDSMonitor(),
            dbc_monitor=DBCMonitor()
        )

    def send(self, seed: Seed):
        if not self.can:
            arb = seed.message_id or 0x6A6
            print(f"[Stub:Tx] ID={hex(arb)} | Signal={seed.signal_name:<25}")
            return

        try:
            value = int(seed.metadata.offset or 0)
        except Exception:
            value = 0

        data = value.to_bytes(8, "little", signed=True)
        arb = getattr(seed, "message_id", None) or getattr(self.can, "can_id", 0x6A6)

        try:
            self.can.send_raw(data, arb_id=arb)
        except Exception as e:
            print(f"[!] CAN send error: {e}")

    def run(self) -> Dict[str, Any]:
        """
        퍼징 + 모니터링을 수행하고 최종 결과 딕셔너리로 반환한다.
        CLI 쪽에서 출력/저장하도록 함.
        """
        self.running = True

        # 모니터 실행
        self.monitor_manager.start_monitors(timing_timeout=5.0)

        # CAN Listener 시작
        if self.can:
            try:
                self.can.start_listener()
            except Exception as e:
                print(f"[!] CAN listener start failed: {e}")

        # Fuzzing Loop
        while self.running:
            seed = self.queue.pop()
            if not seed:
                break

            self.send(seed)
            time.sleep(0.2)

        # 종료 처리
        if self.can:
            self.can.stop_listener()

        self.monitor_manager.wait_for_completion()

        scores = self.monitor_manager.get_scores()
        status = self.monitor_manager.get_completion_status()

        # 파이프라인 종료
        self.queue.close()

        # 결과 딕셔너리 리턴
        return {
            "timing": {"score": scores["timing"], "completed": status["timing"]},
            "uds": {"score": scores["uds"], "completed": status["uds"]},
            "dbc": {"score": scores["dbc"], "completed": status["dbc"]},
        }