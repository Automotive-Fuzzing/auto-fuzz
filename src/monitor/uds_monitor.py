from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Callable
import time

@dataclass
class UDSEvent:
    service: str               
    response_type: str           
    code: Optional[int]
    raw: bytes
    timestamp: float

class ISOTPAssembler:
    def __init__(self):
        self.buffer = bytearray()
        self.expected_len = 0
        self.next_sn = 1
        self.in_progress = False

    def feed(self, data: bytes) -> Optional[bytes]:
        if not data:
            return None

        pci_type = (data[0] & 0xF0) >> 4
        pci_low  =  data[0] & 0x0F

        # SF: Single Frame (<=7 data bytes)
        if pci_type == 0x0:
            length = pci_low
            return data[1:1 + length]

        # FF: First Frame
        elif pci_type == 0x1:
            self.expected_len = ((pci_low << 8) | data[1])
            self.buffer = bytearray(data[2:])
            self.in_progress = True
            self.next_sn = 1
            return None  # wait for CFs

        # CF: Consecutive Frame
        elif pci_type == 0x2 and self.in_progress:
            sn = pci_low
            # sequence check
            if sn != (self.next_sn & 0x0F):
                self.reset()
                return None
            self.next_sn += 1
            self.buffer.extend(data[1:])
            if len(self.buffer) >= self.expected_len:
                pdu = bytes(self.buffer[:self.expected_len])
                self.reset()
                return pdu
            return None

        # FC: Flow Control (0x3) → ignore (receiver side)
        else:
            return None

    def reset(self):
        self.buffer = bytearray()
        self.expected_len = 0
        self.next_sn = 1
        self.in_progress = False


class UDSMonitor:
    """
    - send_0x10 / send_0x3e: 단일프레임 송신 + 응답 대기/분류
    - probe_session_then_tester_present: 0x10 긍정이면 0x3E 연속 수행
    """
    def __init__(
        self,
        tx_id: int = 0x7E0,
        rx_id: int = 0x7E8,
        sender: Optional[Callable[[int, bytes], None]] = None,
        collector: Optional[Callable[[float], List[Tuple[int, bytes, float]]]] = None,
    ):
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.sender = sender          
        self.collector = collector    
        self.assembler = ISOTPAssembler()

    # -------------------------
    # Parse UDS response (positive/negative만 생성)
    # -------------------------
    @staticmethod
    def parse_uds_pdu(pdu: bytes) -> Optional["UDSEvent"]:
        # Negative Response (0x7F <req_service> <NRC>)
        if len(pdu) >= 3 and pdu[0] == 0x7F:
            req_srv, nrc = pdu[1], pdu[2]
            return UDSEvent(f"0x{req_srv:02X}", "negative", nrc, pdu, time.time())

        # Positive Response (0x40 + <req_service>)
        if len(pdu) >= 1 and pdu[0] >= 0x40:
            service = pdu[0] - 0x40
            return UDSEvent(f"0x{service:02X}", "positive", None, pdu, time.time())

        # 그 외는 이벤트 생성 안 함
        return None

    @staticmethod
    def _build_sf_from_uds_pdu(uds_pdu: bytes) -> bytes:
        """
        UDS PDU (<=7 bytes)를 ISO-TP Single Frame로 캡슐화해 8바이트 반환.
        """
        if len(uds_pdu) > 7:
            raise ValueError("PDU too long for Single Frame")
        pad_len = 7 - len(uds_pdu)
        return bytes([len(uds_pdu)]) + uds_pdu + bytes(pad_len)

    def _wait_one_uds_event(
        self,
        expected_service: int,
        timeout: float = 1.0,
        poll_interval: float = 0.02,
    ) -> Optional[UDSEvent]:
        """
        collector를 폴링하여 응답 하나를 조립 완료될 때까지 기다리고 반환.
        expected_service와 매칭되는 positive/negative만 리턴.
        """
        if not self.collector:
            return None

        t0 = time.time()
        last_ts = t0
        while time.time() - t0 < timeout:
            frames = self.collector(last_ts)  
            if frames:
                last_ts = max(last_ts, max(ts for _, _, ts in frames))
            for arbid, data, ts in frames:
                if arbid != self.rx_id:
                    continue
                pdu = self.assembler.feed(data)
                if pdu is None:
                    continue
                evt = self.parse_uds_pdu(pdu)
                if evt is not None:
                    evt.timestamp = ts
                    # positive: 0x40+expected_service
                    # negative: 0x7F,<expected_service>,<NRC>
                    if evt.service.lower() == f"0x{expected_service:02x}":
                        return evt
            time.sleep(poll_interval)

        return None  # timeout

    # -------------------------
    # Send 0x10 & wait response
    # -------------------------
    def send_0x10(self, subfunc: int = 0x01, timeout: float = 1.0) -> Optional[UDSEvent]:
        if not self.sender:
            raise RuntimeError("sender is not set")
        uds_pdu = bytes([0x10, subfunc])
        can_data = self._build_sf_from_uds_pdu(uds_pdu)
        self.sender(self.tx_id, can_data)
        return self._wait_one_uds_event(expected_service=0x10, timeout=timeout)

    # -------------------------
    # Send 0x3E & wait response
    # -------------------------
    def send_0x3e(self, subfunc: int = 0x00, timeout: float = 1.0) -> Optional[UDSEvent]:
        if not self.sender:
            raise RuntimeError("sender is not set")
        uds_pdu = bytes([0x3E, subfunc])
        can_data = self._build_sf_from_uds_pdu(uds_pdu)
        self.sender(self.tx_id, can_data)
        return self._wait_one_uds_event(expected_service=0x3E, timeout=timeout)

    # -------------------------
    # Sequence: 0x10 → (positive) 0x3E
    # -------------------------
    def probe_session_then_tester_present(
        self,
        session_sub: int = 0x01,
        tester_sub: int = 0x00,
        t1: float = 1.0,
        t2: float = 1.0,
    ) -> Dict[str, Optional[UDSEvent]]:
        """
        1) 0x10 세션 전환 요청 → 응답 분류
        2) positive면 0x3E tester present → 응답 분류
        """
        result: Dict[str, Optional[UDSEvent]] = {"0x10": None, "0x3E": None}
        evt10 = self.send_0x10(session_sub, timeout=t1)
        result["0x10"] = evt10

        if evt10 and evt10.response_type == "positive":
            evt3e = self.send_0x3e(tester_sub, timeout=t2)
            result["0x3E"] = evt3e

        return result

    # -------------------------
    # Simple print helper (optional)
    # -------------------------
    def interpret(self, events: List[UDSEvent]) -> None:
        print("UDS Monitor Events:")
        for e in events:
            print(f"  [{e.timestamp:.3f}] {e.service:<6} {e.response_type:<9} {e.code or '-'} {e.raw.hex().upper()}")
