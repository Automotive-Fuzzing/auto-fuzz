# src/monitor/timing_monitor.py

import can
import time
from logger.base_logger import log_event
from typing import Optional


# 모니터링 설정값 
CAN_CHANNEL = "can0"           # 사용할 CAN 인터페이스 
TARGET_ID = 0x366              # Blinkmodi_02
EXPECTED_CYCLE_MS = 1000       # 기대 주기 
TOLERANCE_MS = 50              # 허용 오차
LOG_INTERVAL = 10              # 평균 주기 출력 주기 (10프레임마다 평균 계산)


class TimingMonitor:
    def __init__(self,
                 channel: str = CAN_CHANNEL,
                 target_id: int = TARGET_ID,
                 expected_cycle: int = EXPECTED_CYCLE_MS,
                 tolerance: int = TOLERANCE_MS):
        """
        :param channel: CAN 인터페이스 이름
        :param target_id: 모니터링 대상 CAN ID
        :param expected_cycle: 기대 주기(ms)
        :param tolerance: 허용 오차(ms)
        """
        self.channel = channel
        self.target_id = target_id
        self.expected = expected_cycle
        self.tolerance = tolerance

        # pycan 인터페이스 초기화
        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")

        # 내부 상태 관리
        self.prev_time = None     # 이전 메시지 수신 시각
        self.events = []          # 발생한 이벤트 버퍼
        self._frame_counter = 0   # 평균 주기 계산용 카운터
        self._cycle_list = []     # 최근 N개의 주기 기록
        self._fail_score = 0.0    # FAIL 스코어 누적


    def start(self, timeout: Optional[float] = None) -> float:
        """
        모니터링 루프 시작.
        지정된 CAN ID의 메시지를 수신하며, 주기 위배 여부를 검사한다.
        
        :param timeout: 모니터링 시간 제한(초). None이면 무제한
        :return: 최종 FAIL 스코어
        """
        print(f"[ INFO ] Monitoring 0x{self.target_id:X} "
              f"(Cycle={self.expected}ms ±{self.tolerance}ms) on {self.channel}")
        
        if timeout:
            print(f"[ INFO ] Timeout set to {timeout} seconds")
        
        start_time = time.time()
        self._fail_score = 0.0  # 스코어 초기화

        while True:
            # timeout 체크
            if timeout and (time.time() - start_time) >= timeout:
                print(f"[ INFO ] Timing Monitor timeout reached ({timeout}s)")
                break
            
            msg = self.bus.recv(timeout=1)
            if not msg:
                continue

            # 지정한 CAN ID만 필터링
            if msg.arbitration_id != self.target_id:
                continue

            now = time.time() * 1000  # 현재 시각(ms)
            if self.prev_time:
                cycle = now - self.prev_time
                status = "OK" if (self.expected - self.tolerance <= cycle <= self.expected + self.tolerance) else "FAIL"

                # FAIL인 경우 스코어 누적 (오차의 절댓값)
                if status == "FAIL":
                    if cycle < self.expected - self.tolerance:
                        # 너무 빠름
                        error = (self.expected - self.tolerance) - cycle
                    else:
                        # 너무 느림
                        error = cycle - (self.expected + self.tolerance)
                    self._fail_score += error

                # 이벤트 생성 및 저장
                event = {
                    "type": "timing",
                    "id": self.target_id,
                    "metric": "cycle_time",
                    "value": round(cycle, 2),
                    "status": status
                }
                self.events.append(event)

                # 공통 로거에 기록
                log_event("timing", self.target_id, "cycle_time", cycle, status)

                # CLI 출력
                print(f"[{status}] Cycle: {cycle:.2f} ms")

                # 평균 주기 계산 (optional)
                self._frame_counter += 1
                self._cycle_list.append(cycle)
                if self._frame_counter % LOG_INTERVAL == 0:
                    avg_cycle = sum(self._cycle_list[-LOG_INTERVAL:]) / LOG_INTERVAL
                    print(f"    └ Average cycle (last {LOG_INTERVAL}): {avg_cycle:.2f} ms")

            self.prev_time = now
        
        print(f"[ INFO ] Timing Monitor finished - Total FAIL score: {self._fail_score:.2f}")
        return self._fail_score


    def get_fail_score(self) -> float:
        """
        현재까지 누적된 FAIL 스코어 반환
        
        :return: FAIL 스코어 (허용 범위를 벗어난 오차의 누적합)
        """
        return self._fail_score


    def fetch_events(self):
        """
        Manager 또는 Pipeline에서 호출하여, 새로 수집된 이벤트를 반환한다.
        반환 후 내부 버퍼(self.events)는 초기화된다.
        """
        events_copy = self.events[:]
        self.events.clear()
        return events_copy