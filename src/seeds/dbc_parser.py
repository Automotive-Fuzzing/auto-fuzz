# ─────────────────────────────────────────────────────────────────────────────
# file: src/seeds/dbc_parser.py 
# ─────────────────────────────────────────────────────────────────────────────
# 헤더부 - 파일의 메타정보 및 문서화 문자열 assignment용 경량 DBC/JSON parser 임을 명시
"""
Lightweight DBC/JSON parser facade for the assignment.

✅ What it supports now
- JSON files (or strings) that describe messages & signals

📄 Expected JSON shape (example):
{
  "messages": [
    {
      "id": "0x366",
      "name": "Blinkmodi_02",
      "tx": "BCM",
      "signals": [
        {"name": "Blinken_re", "start_bit": 0, "length": 8, "factor": 1.0, "offset": 0.0, "unit": "", "rx": ["Gateway"]},
        {"name": "li_Kombi_Takt", "start_bit": 8, "length": 8, "factor": 1.0, "offset": 0.0, "unit": ""}
      ]
    }
  ]
}

🔁 Return value of parse(...): the normalized dict with the same shape (validated/coerced types).

You can replace the file input with `content="{...json...}"` for quick tests.
"""
from __future__ import annotations # type hint 순환 참조를 허용하기 위한 선언

from dataclasses import dataclass # 데이터 전용 클래스 정의
from pathlib import Path # 파일 경로 다루기
from typing import Any, Dict, List, Optional # 타입 힌트를 통해 가독성 및 IDE 지원 강화
import json # JSON 파일을 로드하기 위한 표준 라이브러리


@dataclass
class Signal:
    name: str                           # 신호 이름
    start_bit: int                      # 비트 단위 및 위치 정보
    length: int                         # 비트 단위 및 위치 정보
    factor: float = 1.0                 # 물리값 변환식
    offset: float = 0.0                 # 물리값 변환식
    unit: str = ""                      # 단위
    rx: Optional[List[str]] = None      # 수신 ECU 목록


@dataclass
class Message:
    id: str # CAN ID
    name: str # 메시지 명
    tx: Optional[str] # 송신 ECU
    signals: List[Signal] # 포함된 신호 리스트


@dataclass
class ParsedDBC:
    messages: List[Message] 

    def to_dict(self) -> Dict[str, Any]: # dataclass -> Python dict 변환 함수
        return {                         # 중첩 구조를 풀어 JSON 직렬화 가능하게 변환
            "messages": [
                {
                    "id": m.id,
                    "name": m.name,
                    "tx": m.tx,
                    "signals": [
                        {
                            "name": s.name,
                            "start_bit": s.start_bit,
                            "length": s.length,
                            "factor": s.factor,
                            "offset": s.offset,
                            "unit": s.unit,
                            "rx": s.rx,
                        }
                        for s in m.signals
                    ],
                }
                for m in self.messages
            ]
        }


def _coerce_signal(obj: Dict[str, Any]) -> Signal: # JSON의 signal dict를 Signal 객체로 변환
    return Signal(                                 # int, float, str 형 변환을 통해 정규화 수행
        name=str(obj["name"]),
        start_bit=int(obj.get("start_bit", 0)), # .get()을 사용하여 누락된 값에 대한 기본값을 안전하게 처리
        length=int(obj.get("length", 1)),
        factor=float(obj.get("factor", 1.0)),
        offset=float(obj.get("offset", 0.0)),
        unit=str(obj.get("unit", "")),
        rx=list(obj.get("rx")) if obj.get("rx") is not None else None, # rx가 존재하면 리스트로 변환
    )


def _coerce_message(obj: Dict[str, Any]) -> Message: # 한 메시지 단위를 Message 객체로 변환
    signals_raw = obj.get("signals", [])
    signals = [_coerce_signal(s) for s in signals_raw] # _coerce_signal() 을 호출하여 signal 리스트 변환
    return Message(
        id=str(obj["id"]), # id는 반드시 존재해야 하므로 obj["id"] 직접 사용
        name=str(obj.get("name", "")),
        tx=(str(obj.get("tx")) if obj.get("tx") is not None else None),
        signals=signals,
    )


def parse(filepath: Optional[str] = None, *, content: Optional[str] = None) -> Dict[str, Any]: # JSON 파일 or 문자열 중 하나 입력받음
    """
    Parse a JSON file (or raw JSON string) into a normalized dict.

    Usage
    -----
    parse("./sample.json")
    parse(content=json.dumps({...}))
    """
    if (filepath is None) == (content is None): # XOR 논리 둘 다 None 일 경우와 둘 다 제공되는 경우 X
        raise ValueError("Provide exactly one of: 'filepath' or 'content'.")

    if filepath is not None: # filepath가 있는 경우
        path = Path(filepath) # 파일을 읽음
        if not path.exists():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
    else: # filepath가 없는 경우
        text = content or "{}" # content 그대로 사용

    data = json.loads(text) # JSON 문자열을 dict로 로드
    msgs = data.get("messages", []) 
    messages = [_coerce_message(m) for m in msgs]
    parsed = ParsedDBC(messages=messages) # ParsedDBC 객체 생성
    return parsed.to_dict() # .to_dict() 결과 반환


if __name__ == "__main__": # 테스트용 실행 함수
    # quick self‑test with inline JSON
    sample = {
        "messages": [
            {
                "id": "0x366",
                "name": "Blinkmodi_02",
                "tx": "BCM",
                "signals": [
                    {"name": "Blinken_re", "start_bit": 0, "length": 8},
                    {"name": "li_Kombi_Takt", "start_bit": 8, "length": 8},
                ],
            }
        ]
    }
    print(json.dumps(parse(content=json.dumps(sample)), indent=2, ensure_ascii=False))