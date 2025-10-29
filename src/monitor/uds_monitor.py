from dataclasses import dataclass
from typing import Optional, Dict, Tuple
import time, os
import can
import isotp
import yaml

CAN_CHANNEL         = "can0"
TARGET_UDS_ID       = 0x366
UDS_RESPONSE_OFFSET = 0x6A                 # 응답 ID = TARGET_UDS_ID + 0x6A
PADDING_BYTE        = 0xAA                 # ISO-TP TX 패딩 바이트

_ISOTP_PARAMS = {
    "stmin": 0,
    "blocksize": 8,
    "wftmax": 0,
    "tx_data_length": 8,
    "tx_padding": PADDING_BYTE,
    "rx_flowcontrol_timeout": 1000,
    "rx_consecutive_frame_timeout": 1000,
}

def load_nrc_config_strict(path: str) -> Tuple[Dict[int, float], Dict[str, float]]:
    """
    엄격 모드:
      - 파일/필드/형식이 틀리면 즉시 예외
      - nrc_scores: 키는 반드시 '0x..' 16진 문자열 → int로 변환
      - context_multipliers: 키는 반드시 '0x..' 소문자 16진 문자열
    """
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"UDSMonitor config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Config YAML root must be a mapping")

    if "nrc_scores" not in data or "context_multipliers" not in data:
        raise KeyError("Config must contain both 'nrc_scores' and 'context_multipliers'")

    raw_scores = data["nrc_scores"]
    raw_ctx    = data["context_multipliers"]

    if not isinstance(raw_scores, dict) or not isinstance(raw_ctx, dict):
        raise ValueError("'nrc_scores' and 'context_multipliers' must be mappings")

    # nrc_scores: '0x..' 문자열 → int 키
    nrc_scores: Dict[int, float] = {}
    for k, v in raw_scores.items():
        if not isinstance(k, str) or not k.strip().lower().startswith("0x"):
            raise ValueError(f"NRC key must be hex string like '0x10', got {k!r}")
        try:
            ki = int(k.strip().lower(), 16)
            vf = float(v)
        except Exception as e:
            raise ValueError(f"Invalid nrc_scores entry {k!r}:{v!r} → {e}")
        nrc_scores[ki] = vf

    # context_multipliers: '0x..' 소문자 16진 문자열
    ctx_mul: Dict[str, float] = {}
    for k, v in raw_ctx.items():
        if not isinstance(k, str):
            raise ValueError(f"Context key must be string like '0x10', got {type(k)}")
        ks = k.strip().lower()
        if not ks.startswith("0x"):
            raise ValueError(f"Context key must start with '0x': {k!r}")
        try:
            int(ks, 16)  # 유효성 확인
            vf = float(v)
        except Exception as e:
            raise ValueError(f"Invalid context_multipliers entry {k!r}:{v!r} → {e}")
        ctx_mul[ks] = vf

    if not nrc_scores:
        raise ValueError("'nrc_scores' must not be empty")
    if not ctx_mul:
        raise ValueError("'context_multipliers' must not be empty")

    return nrc_scores, ctx_mul


@dataclass
class UDSEvent:
    service: str                 # "0x10", "0x3E", ...
    response_type: str           # "positive" | "negative"
    code: Optional[int]          # NRC (긍정은 None)
    raw: bytes                   # 수신 UDS PDU
    timestamp: float             # 수신 시각
    score: float = 0.0           # 부정응답 위험 점수


class UDSMonitor:
    """
    - 디폴트 제거: YAML 설정 필수
    - __init__() : 버스/스택 준비 + 설정 로드
    - start()    : 0x10 → 응답 처리 → positive면 0x3E까지
    """

    def __init__(self,
                 bus: Optional[can.Bus] = None,
                 tx_id: int = TARGET_UDS_ID,
                 rx_id: int = TARGET_UDS_ID + UDS_RESPONSE_OFFSET,
                 nrc_cfg_path: str = "config/nrc_weights.yaml"):

        self.bus = bus or can.interface.Bus(bustype="socketcan", channel=CAN_CHANNEL)
        self.addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=tx_id, rxid=rx_id)
        self.stack = isotp.CanStack(bus=self.bus, address=self.addr, params=_ISOTP_PARAMS)

        # 설정 필수 로드
        self._nrc_score, self._ctx_mul = load_nrc_config_strict(nrc_cfg_path)

    def start(self,
              session_sub: int = 0x01,
              tester_sub: int = 0x00,
              t1: float = 1.0,
              t2: float = 1.0) -> Dict[str, Optional[UDSEvent]]:

        result: Dict[str, Optional[UDSEvent]] = {"0x10": None, "0x3E": None}

        # Step 1: 0x10
        self._send_uds(bytes([0x10, session_sub]))
        evt10 = self._recv_one(expected_sid=0x10, timeout=t1)
        result["0x10"] = evt10

        # Step 2: 0x3E (only if positive 0x10)
        if evt10 and evt10.response_type == "positive":
            self._send_uds(bytes([0x3E, tester_sub]))
            evt3e = self._recv_one(expected_sid=0x3E, timeout=t2)
            result["0x3E"] = evt3e

        return result

    def _send_uds(self, uds_pdu: bytes) -> None:
        self.stack.send(uds_pdu)
        while self.stack.transmitting():
            self.stack.process()
            time.sleep(0.002)

    def _recv_one(self, expected_sid: int, timeout: float) -> Optional[UDSEvent]:
        """
        기대 서비스(예: 0x10/0x3E)의 응답 1건 동기 수신.
        - 0x7F: 설정된 NRC만 스코어링(미정의 NRC는 continue)
        - positive: 그대로 반환
        - timeout: None
        """
        deadline = time.time() + timeout
        expected_tag = f"0x{expected_sid:02X}".lower()

        while time.time() < deadline:
            self.stack.process()
            if self.stack.available():
                pdu = self.stack.recv()
                ts = time.time()

                evt = self._parse_uds_pdu(pdu, ts)
                if not evt:
                    continue
                if evt.service.lower() != expected_tag:
                    continue
                if evt.response_type == "negative":
                    if not self._score(evt):   # 미정의 NRC면 continue
                        continue
                return evt

            time.sleep(self.stack.sleep_time())

        return None

    @staticmethod
    def _parse_uds_pdu(pdu: bytes, ts: float) -> Optional[UDSEvent]:
        if not pdu:
            return None
        # Negative: 0x7F <req_sid> <nrc>
        if len(pdu) >= 3 and pdu[0] == 0x7F:
            return UDSEvent(service=f"0x{pdu[1]:02X}", response_type="negative",
                            code=pdu[2], raw=pdu, timestamp=ts)
        # Positive: 0x40 + <req_sid>
        if pdu[0] >= 0x40:
            sid = pdu[0] - 0x40
            return UDSEvent(service=f"0x{sid:02X}", response_type="positive",
                            code=None, raw=pdu, timestamp=ts)
        return None

    def _score(self, evt: UDSEvent) -> bool:
        """부정응답만 점수화. 설정 파일에 없는 NRC면 False(=무시)."""
        if evt.response_type != "negative" or evt.code is None:
            return True
        base = self._nrc_score.get(evt.code)        # evt.code는 int
        if base is None:
            return False
        ctx = self._ctx_mul.get(evt.service.lower(), 1.0)  # service는 '0x..' 소문자
        evt.score = float(base) * float(ctx)
        return True