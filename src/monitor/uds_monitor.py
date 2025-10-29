import can
import isotp
import time


class UDSMonitor:
    def __init__(self,
                 channel: str = "can0",
                 ecu_tx_id: int = None,
                 ecu_rx_id: int = None):
        """
        UDSMonitor 초기화 (socketcan 기반)
        :param channel: CAN 인터페이스 (기본값: can0)
        :param ecu_tx_id: 송신 ID (Tester → ECU)
        :param ecu_rx_id: 수신 ID (ECU → Tester)
        """
        self.channel = channel
        self.ecu_tx_id = ecu_tx_id
        self.ecu_rx_id = ecu_rx_id

        # NRC 코드 매핑 (리뷰 반영)
        self.NRC_TABLE = {
            0x10: "General Reject",
            0x11: "Service Not Supported",
            0x12: "Sub-function Not Supported",
            0x13: "Incorrect Message Length",
            0x22: "Conditions Not Correct",
            0x31: "Request Out Of Range",
            0x33: "Security Access Denied",
            0x78: "Response Pending",
            0x73: "Wrong Block Sequence Counter",
            # 필요하면 여기에 추가
        }
        self.DEFAULT_NRC_DESC = "Unknown NRC"

        # CAN 버스 초기화
        try:
            self.bus = can.interface.Bus(channel=self.channel, bustype="socketcan")
            print(f"[INFO] ✅ CAN 인터페이스 연결 성공 ({self.channel})")
        except Exception as e:
            print(f"[ERROR] ❌ CAN 인터페이스 초기화 실패: {e}")
            raise

        # ECU ID가 아직 없을 경우 안내
        if self.ecu_tx_id is None or self.ecu_rx_id is None:
            print("[WARN] ⚠️ ECU ID가 아직 설정되지 않았습니다. "
                  "start() 실행 시 인자로 지정해야 합니다.")

        self.stack = None


    def start(self, ecu_tx_id: int = None, ecu_rx_id: int = None):
        """
        Diagnostic Session Control (0x10 03) 요청을 보내고
        ECU 응답(0x50 03 or 0x7F xx)을 수신.
        :param ecu_tx_id: 송신 ID (선택)
        :param ecu_rx_id: 수신 ID (선택)
        """
        tx_id = ecu_tx_id or self.ecu_tx_id
        rx_id = ecu_rx_id or self.ecu_rx_id

        if tx_id is None or rx_id is None:
            print("⛔ ECU ID가 지정되지 않았습니다.")
            print("예시: monitor.start(ecu_tx_id=0x366, ecu_rx_id=0x766)")
            return

        # ISO-TP 스택 설정
        addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=tx_id, rxid=rx_id)
        self.stack = isotp.CanStack(bus=self.bus, address=addr)
        print(f"[INFO] 🧩 UDS Monitor 시작 (TxID=0x{tx_id:X}, RxID=0x{rx_id:X})")

        # Step 1. Diagnostic Session Control (0x10 03)
        request = bytes([0x10, 0x03])
        self.stack.send(request)
        print("[TX] 10 03  (Diagnostic Session Control - Extended Session)")

        # ECU 응답 대기 (2초 타임아웃)
        start_time = time.time()
        response = None
        while time.time() - start_time < 2.0:
            self.stack.process()
            if self.stack.available():
                response = self.stack.recv()
                print(f"[RX] {response.hex(' ').upper()}")
                break
            time.sleep(0.01)

        # 결과 처리
        if response is None:
            print("⛔ ECU 응답 없음 (Timeout)")
        elif len(response) >= 2 and response[0] == 0x50 and response[1] == 0x03:
            print("✅ ECU 진단 세션 진입 성공!")
        elif len(response) >= 3 and response[0] == 0x7F:
            # Negative Response 처리: NRC 값 매핑
            nrc = int(response[2])
            desc = self.NRC_TABLE.get(nrc, self.DEFAULT_NRC_DESC)
            print(f"⚠️ Negative Response (NRC: 0x{nrc:02X}) - {desc}")
        else:
            print("⚠️ 알 수 없는 응답:", response.hex())

        print("[INFO] 🚗 UDS 세션 진입 시퀀스 종료.")
