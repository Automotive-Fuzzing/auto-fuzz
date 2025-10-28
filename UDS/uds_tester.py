# src/monitor/timing_monitor.py

import time
import can
import isotp
from logger.base_logger import log_event   # 팀 공용 로거 사용 가정


# ---------------------------
# UDS 모니터링 설정
# ---------------------------
REQ_ID = 0x366    # Tester -> ECU
RESP_ID = 0x766    # ECU -> Tester

W_SESSION = 0.75
W_TESTER  = 0.25
BASE_SESSION_PENALTY = 0.50
BASE_TESTER_PENALTY  = 0.10

NRC_WEIGHTS = {
    0x10: 0.40,  # general reject
    0x11: 0.38,  # service not supported
    0x73: 0.25,  # wrong block seq
    0x22: 0.22,  # condition not correct
    0x13: 0.18,  # incorrect message length
}
DEFAULT_NRC_WEIGHT = 0.12


def nrc_weight(nrc): return NRC_WEIGHTS.get(nrc, DEFAULT_NRC_WEIGHT)


class UDSMonitor:
    def __init__(self,
                 channel: str = "can0",
                 tx_id: int = REQ_ID,
                 rx_id: int = RESP_ID):
        """
        UDS Fail-Detection 모니터
        - 0x10 (세션 진입) 및 0x3E (Tester Present) 기반 모니터링 수행
        """
        self.channel = channel
        self.tx_id = tx_id
        self.rx_id = rx_id

        # CAN / ISOTP 초기화
        self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")
        addr = isotp.Address(isotp.AddressingMode.Normal_11bits,
                             txid=self.tx_id, rxid=self.rx_id)
        self.stack = isotp.CanStack(bus=self.bus, address=addr)

        # 내부 상태
        self.events = []
        self.session_ok = False
        self.final_score = 0.0

        print(f"[ INFO ] UDSMonitor initialized (TxID=0x{tx_id:X}, RxID=0x{rx_id:X})")


    def _send_and_wait(self, payload: bytes, timeout=1.0):
        """
        ISO-TP 전송 및 응답 수신 헬퍼
        """
        self.stack.send(payload)
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.stack.process()
            if self.stack.available():
                resp = self.stack.recv()
                return resp
            time.sleep(0.01)
        return None


    def _analyze_response(self, resp: bytes, context: str):
        """
        응답을 해석하고 위험 스코어를 계산
        """
        if resp is None:
            log_event("uds", self.tx_id, f"{context}_timeout", 0, "FAIL")
            self.events.append({
                "type": "uds",
                "id": self.tx_id,
                "context": context,
                "resp": None,
                "status": "Timeout",
                "score": 1.0
            })
            return 1.0

        # Positive response
        if len(resp) >= 2 and resp[0] in (0x50, 0x7E):
            log_event("uds", self.tx_id, f"{context}_ok", 0, "OK")
            self.events.append({
                "type": "uds",
                "id": self.tx_id,
                "context": context,
                "resp": resp.hex(" ").upper(),
                "status": "OK",
                "score": 0.0
            })
            return 0.0

        # Negative Response (7F SID NRC)
        if len(resp) >= 3 and resp[0] == 0x7F:
            nrc = resp[2]
            weight = nrc_weight(nrc)
            if context == "session":
                score = min(1.0, BASE_SESSION_PENALTY + weight)
            else:
                score = min(1.0, BASE_TESTER_PENALTY + 0.5 * weight)

            log_event("uds", self.tx_id, f"{context}_nrc", nrc, "FAIL")
            self.events.append({
                "type": "uds",
                "id": self.tx_id,
                "context": context,
                "resp": resp.hex(" ").upper(),
                "nrc": hex(nrc),
                "status": "FAIL",
                "score": round(score, 3)
            })
            return score

        # Unknown response
        log_event("uds", self.tx_id, f"{context}_unknown", 0, "WARN")
        self.events.append({
            "type": "uds",
            "id": self.tx_id,
            "context": context,
            "resp": resp.hex(" ").upper(),
            "status": "Unknown",
            "score": 0.5
        })
        return 0.5


    def start(self):
        """
        UDS 모니터링 시작 (세션 진입 → TesterPresent 반복)
        """
        print("[ STEP 1 ] Diagnostic Session Entry (0x10 03)")
        session_req = bytes([0x10, 0x03])
        resp = self._send_and_wait(session_req, timeout=2.0)
        session_score = self._analyze_response(resp, "session")

        self.session_ok = (session_score == 0.0)
        if not self.session_ok:
            print("⛔ Session entry failed, skipping TesterPresent loop.")
            self.final_score = session_score
            return

        print("[ STEP 2 ] TesterPresent loop start")
        total_tester_score = 0
        count = 0

        try:
            while True:
                tester_req = bytes([0x3E, 0x00])
                resp = self._send_and_wait(tester_req, timeout=0.2)
                tester_score = self._analyze_response(resp, "tester")
                total_tester_score += tester_score
                count += 1
                avg_tester = total_tester_score / count
                self.final_score = round(W_SESSION * session_score + W_TESTER * avg_tester, 3)
                print(f"[Cycle {count}] TesterPresent Score={tester_score:.2f} | Avg={avg_tester:.2f} | Final={self.final_score:.3f}")
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped by user.")


    def fetch_events(self):
        """
        최근 이벤트를 반환하고 내부 버퍼를 비운다.
        """
        events_copy = self.events[:]
        self.events.clear()
        return events_copy
