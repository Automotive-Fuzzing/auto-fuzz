# src/monitor/monitor_manager.py

import threading
import time
from typing import Dict, Optional
from monitor.timing_monitor import TimingMonitor
from monitor.uds_monitor import UDSMonitor
from monitor.dbc_monitor import DBCMonitor


class MonitorManager:

    
    def __init__(self,
                 timing_monitor: Optional[TimingMonitor] = None,
                 uds_monitor: Optional[UDSMonitor] = None,
                 dbc_monitor: Optional[DBCMonitor] = None):
        """
        :param timing_monitor: TimingMonitor 인스턴스 (None이면 비활성화)
        :param uds_monitor: UDSMonitor 인스턴스 (None이면 비활성화)
        :param dbc_monitor: DBCMonitor 인스턴스 (None이면 비활성화)
        """
        self.timing_monitor = timing_monitor
        self.uds_monitor = uds_monitor
        self.dbc_monitor = dbc_monitor
        
        # 스레드 관리
        self.threads: Dict[str, threading.Thread] = {}
        
        # 스코어 저장
        self.scores: Dict[str, float] = {
            "timing": 0.0,
            "uds": 0.0,
            "dbc": 0.0
        }
        self.scores_lock = threading.Lock()
        
        # 완료 상태 추적
        self.completed: Dict[str, bool] = {
            "timing": False,
            "uds": False,
            "dbc": False
        }
        
        # 실행 상태
        self.running = False
        
    
    def start_monitors(self, 
                   timing_timeout: Optional[float] = 5.0,
                   dbc_timeout: Optional[float] = 5.0):
    """
    각 모니터를 별도 스레드로 시작

    :param timing_timeout: Timing 모니터 실행 시간 제한(초). 기본 5초
    :param dbc_timeout: DBC 모니터 실행 시간 제한(초). 기본 5초
    """
    if self.running:
        print("[WARN] Monitors are already running")
        return
    
    self.running = True
    print("[INFO] Starting monitors...")
    
    # 스코어 및 완료 상태 초기화
    with self.scores_lock:
        self.scores = {"timing": 0.0, "uds": 0.0, "dbc": 0.0}
        self.completed = {"timing": False, "uds": False, "dbc": False}
    
    # Timing Monitor 스레드
    if self.timing_monitor:
        thread = threading.Thread(
            target=self._run_timing_monitor,
            args=(timing_timeout,),
            daemon=True,
            name="TimingMonitor"
        )
        self.threads["timing"] = thread
        thread.start()
        print("[INFO] ✓ Timing Monitor started")
    else:
        self.completed["timing"] = True

    # UDS Monitor 스레드
    if self.uds_monitor:
        thread = threading.Thread(
            target=self._run_uds_monitor,
            daemon=True,
            name="UDSMonitor"
        )
        self.threads["uds"] = thread
        thread.start()
        print("[INFO] ✓ UDS Monitor started")
    else:
        self.completed["uds"] = True

    # DBC Monitor 스레드 (timeout 추가됨)
    if self.dbc_monitor:
        thread = threading.Thread(
            target=self._run_dbc_monitor,
            args=(dbc_timeout,),
            daemon=True,
            name="DBCMonitor"
        )
        self.threads["dbc"] = thread
        thread.start()
        print("[INFO] ✓ DBC Monitor started")
    else:
        self.completed["dbc"] = True

    print(f"[INFO] Total {len(self.threads)} monitor(s) running")
    
    
    def _run_timing_monitor(self, timeout: Optional[float]):
        
        try:
            fail_score = self.timing_monitor.start(timeout=timeout)
            
            with self.scores_lock:
                self.scores["timing"] = fail_score
                self.completed["timing"] = True
            
            print(f"[INFO] Timing Monitor completed - Score: {fail_score}")
                    
        except Exception as e:
            print(f"[ERROR] Timing Monitor crashed: {e}")
            with self.scores_lock:
                self.completed["timing"] = True
    
    
    def _run_uds_monitor(self):
        
        try:
            fail_score = self.uds_monitor.start()
            
            with self.scores_lock:
                self.scores["uds"] = fail_score
                self.completed["uds"] = True
            
            print(f"[INFO] UDS Monitor completed - Score: {fail_score}")
                
        except Exception as e:
            print(f"[ERROR] UDS Monitor crashed: {e}")
            with self.scores_lock:
                self.completed["uds"] = True
    
    
    def _run_dbc_monitor(self, timeout: Optional[float]):
    try:
        fail_score = self.dbc_monitor.start(timeout=timeout)

        with self.scores_lock:
            self.scores["dbc"] = fail_score
            self.completed["dbc"] = True

        print(f"[INFO] DBC Monitor completed - Score: {fail_score}")
                
    except Exception as e:
        print(f"[ERROR] DBC Monitor crashed: {e}")
        with self.scores_lock:
            self.completed["dbc"] = True
    
    
    def wait_for_completion(self, timeout: Optional[float] = None):
        """
        모든 모니터 스레드가 종료될 때까지 대기
        param timeout 대기 시간 제한(초). None이면 무제한
        """
        for name, thread in self.threads.items():
            thread.join(timeout=timeout)
            if thread.is_alive():
                print(f"[WARN] {name} thread is still running")
        
        self.running = False
        print("[INFO] All monitors completed")
    
    
    def is_all_completed(self) -> bool:
        """
        모든 모니터가 완료되었는지 확인
        """
        with self.scores_lock:
            return all(self.completed.values())
    
    
    def get_completion_status(self) -> Dict[str, bool]:
        """
        각 모니터의 완료 상태 반환
        :return: {"timing": bool, "uds": bool, "dbc": bool}
        """
        with self.scores_lock:
            return self.completed.copy()
    
    
    def get_scores(self) -> Dict[str, float]:
        """
        각 모니터별 FAIL 스코어 반환
        :return: {"timing": float, "uds": float, "dbc": float}
        """
        with self.scores_lock:
            return self.scores.copy()
    
    
    def is_running(self) -> bool:
        """
        모니터 실행 상태 반환
        
        :return: 실행 중이면 True
        """
        return self.running