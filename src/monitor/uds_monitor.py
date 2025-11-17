# src/monitor/uds_monitor.py
import time
import os
import can
import isotp
import yaml
from logger.base_logger import log_event


# 모니터링 설정값

CAN_CHANNEL = "can0"
TARGET_UDS_ID = 0x366
UDS_RESPONSE_OFFSET = 0x6A
PADDING_BYTE = 0xAA

# ISO-TP 기본 설정
isotp_params = {
    "stmin": 0,
    "blocksize": 8,
    "wftmax": 0,
    "tx_data_length": 8,
    "tx_padding": PADDING_BYTE,
    "rx_flowcontrol_timeout": 1000,
    "rx_consecutive_frame_timeout": 1000,
}

# 스코어 가중치 설정
SCORE_NO_RESPONSE_0x10 = 1.0   # 세션 진입 실패 (가장 치명적)
SCORE_NO_RESPONSE_0x3E = 0.8   # Tester Present 실패
MAX_DTC_COUNT = 20              # DTC 개수 정규화 기준 (20개 이상이면 1.0)



# NRC config 로드

def load_nrc_config(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"NRC config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "nrc_scores" not in data:
        raise ValueError("Invalid NRC config: missing 'nrc_scores'")

    scores = {}
    for k, v in data["nrc_scores"].items():
        try:
            key = int(k, 16) if isinstance(k, str) and k.startswith("0x") else int(k)
            scores[key] = float(v)
        except Exception as e:
            raise ValueError(f"Invalid NRC key/value {k}:{v} ({e})")
    return scores



# UDS 모니터 클래스

class UDSMonitor:
    def __init__(self, nrc_cfg_path: str = "config/nrc_weights.yaml"):
        # NRC 점수 테이블 로드
        self.NRC_CLASS = load_nrc_config(nrc_cfg_path)

        # CAN & ISO-TP 초기화
        self.bus = can.interface.Bus(channel=CAN_CHANNEL, bustype="socketcan")
        addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            txid=TARGET_UDS_ID,
            rxid=TARGET_UDS_ID + UDS_RESPONSE_OFFSET
        )
        self.stack = isotp.CanStack(bus=self.bus, address=addr, params=isotp_params)
        
        # FAIL 스코어 누적 (0~1 범위)
        self._fail_score = 0.0


    # ISO-TP 송신

    def send_request(self, data):
        
        self.stack.send(bytes(data))
        while self.stack.transmitting():
            self.stack.process()
            time.sleep(0.01)

    # ISO-TP 수신

    def recv_response(self, timeout=1.0):
     
        start = time.time()
        while time.time() - start < timeout:
            self.stack.process()
            if self.stack.available():
                return list(self.stack.recv())
            time.sleep(0.01)
        return None



    def start(self) -> float:
        """
        UDS 모니터링 시작
        
        :return: 최종 FAIL 스코어 (0.0 ~ 1.0+)
        """
        self._fail_score = 0.0  # 스코어 초기화
        
        try:
            # 0x10 세션 진입
            if not self._send_once_or_retry([0x10, 0x02], "session_entry", SCORE_NO_RESPONSE_0x10):
                print("[INFO] UDS Monitor finished - Session entry failed")
                return min(self._fail_score, 1.0)  # 1.0으로 제한

            # 0x3E Tester Present
            if not self._send_once_or_retry([0x3E, 0x00], "tester_present", SCORE_NO_RESPONSE_0x3E):
                print("[INFO] UDS Monitor finished - Tester present failed")
                return min(self._fail_score, 1.0)

            # 0x19 DTC 읽기
            self.send_request([0x19, 0x02])
            total_dtc = self.collect_all_dtc()
            
            # DTC 스코어 계산 (0 ~ 1.0)
            dtc_score = min(total_dtc / MAX_DTC_COUNT, 1.0)
            self._fail_score += dtc_score
            
            log_event("uds", TARGET_UDS_ID, "DTC_count", total_dtc, "OK" if total_dtc == 0 else "FAIL")
            log_event("uds", TARGET_UDS_ID, "DTC_score", dtc_score, "OK" if dtc_score < 0.5 else "FAIL")

        except Exception as e:
            log_event("uds", TARGET_UDS_ID, "exception", str(e), "FAIL")
            self._fail_score = 1.0  # 예외 발생 시 최대 스코어
        
        # 최종 스코어는 1.0을 초과할 수 있음 (여러 FAIL이 누적될 경우)
        print(f"[INFO] UDS Monitor finished - Total FAIL score: {self._fail_score:.3f}")
        return self._fail_score

    # 1회 전송 + 1회 재시도

    def _send_once_or_retry(self, data, step_name, no_response_score):
        """
        요청 보내고 응답 확인, 없으면 한 번만 재시도
        
        :param data: 전송할 UDS 요청 데이터
        :param step_name: 단계 이름 (로깅용)
        :param no_response_score: 응답 없을 때 부여할 스코어 (0~1)
        :return: 성공 여부
        """
        # 1차 요청
        self.send_request(data)
        resp = self.recv_response(timeout=1.0)

        if not resp:
            # 응답이 없으면 한 번만 재시도
            self.send_request(data)
            resp = self.recv_response(timeout=1.0)

            if not resp:
                self._fail_score += no_response_score
                log_event("uds", TARGET_UDS_ID, f"{step_name}_no_response", no_response_score, "FAIL")
                return False

        # NRC 응답
        if resp[0] == 0x7F:
            nrc = resp[2]
            if nrc in self.NRC_CLASS:
                score = self.NRC_CLASS[nrc]
                self._fail_score += score
                log_event("uds", TARGET_UDS_ID, f"NRC_{hex(nrc)}", score, "FAIL")
                return False
            else:
                # 알 수 없는 NRC는 정상으로 처리
                log_event("uds", TARGET_UDS_ID, f"NRC_unknown_{hex(nrc)}", nrc, "WARN")
                return True

        # 정상 응답
        log_event("uds", TARGET_UDS_ID, step_name, "response_ok", "OK")
        return True

 

    #0x59 0x02 DTC 개수 계산
    def collect_all_dtc(self):
        
        accumulated_data = bytearray()
        start_time = time.time()

        while time.time() - start_time < 2.0:
            resp = self.recv_response(timeout=0.5)
            if not resp:
                break

            # 정상 DTC 응답
            if len(resp) >= 4 and resp[0] == 0x59 and resp[1] == 0x02:
                # status mask(resp[2]) 건너뛰고 resp[3:]부터 누적
                accumulated_data.extend(resp[3:])

            # NRC 응답
            elif resp[0] == 0x7F:
                nrc = resp[2]
                if nrc in self.NRC_CLASS:
                    score = self.NRC_CLASS[nrc]
                    self._fail_score += score
                    log_event("uds", TARGET_UDS_ID, f"DTC_NRC_{hex(nrc)}", score, "FAIL")
                else:
                    log_event("uds", TARGET_UDS_ID, "DTC_NRC_Unknown", nrc, "WARN")
                continue

        total_dtc = len(accumulated_data) // 4  # 3바이트 코드 + 1바이트 상태
        return total_dtc
    
    
    def get_fail_score(self) -> float:
        """
        현재까지 누적된 FAIL 스코어 반환
        0~1 범위
        """
        return self._fail_score