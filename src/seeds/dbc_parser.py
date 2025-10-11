from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Union
import cantools

def _normalize_byte_order_from_cantools(byte_order: str) -> str:
    
    b = (byte_order or "").lower()
    # None 방지, 소문자 통일

    if b == "little_endian":
        return "little_endian"
    if b == "big_endian":
        return "big_endian"
    return "little_endian"

def parse(path: Union[str, Path]) -> Dict[str, Any]:
    """
    입력: .dbc 텍스트 파일 경로
    출력: 표준화된 dict 스키마
      {
        "messages": {
          "<MessageName>": {
            "name": str,
            "frame_id": int,
            "dlc": int,
            "signals": {
              "<SignalName>": {
                "name": str,
                "start_bit": int,
                "length": int,
                "byte_order": "little_endian"|"big_endian",
                "is_signed": bool,
                "scale": float,
                "offset": float,
                "minimum": float|null,
                "maximum": float|null,
                "unit": str|null,
                "choices": {int: str}|null
              }
            }
          }
        }
      }
    """
    p = Path(path)     #문자열 경로를 Path 객체로 변환

    if not p.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {p}")
    if p.suffix.lower() != ".dbc":
        raise ValueError("이 파서는 .dbc 파일만 지원합니다.")

    text = p.read_text(encoding="utf-8-sig")

    db = cantools.database.load_string(text)

    out: Dict[str, Any] = {"messages": {}}

    # cantools의 모델을 표준 스키마로 매핑
    for msg in db.messages:
        m_name = msg.name
        frame_id = int(msg.frame_id)   
        dlc = int(msg.length)          

        signals_out: Dict[str, Any] = {}
        for sig in msg.signals:
            start_bit = int(sig.start)                  
            length = int(sig.length)                    
            byte_order = _normalize_byte_order_from_cantools(sig.byte_order)
            is_signed = bool(sig.is_signed)
            scale = float(sig.scale if sig.scale is not None else 1.0)
            offset = float(sig.offset if sig.offset is not None else 0.0)
            minimum = None if sig.minimum is None else float(sig.minimum)
            maximum = None if sig.maximum is None else float(sig.maximum)
            unit = None if not sig.unit else str(sig.unit)

            choices = None
            if sig.choices:
                choices = {int(k): str(v) for k, v in sig.choices.items()}

            # 표준 신호 스키마로 한 신호를 완성해서, 신호 이름을 키로 signals_out에 저장
            signals_out[sig.name] = {
                "name": sig.name,
                "start_bit": start_bit,
                "length": length,
                "byte_order": byte_order,             
                "is_signed": is_signed,
                "scale": scale,
                "offset": offset,
                "minimum": minimum,
                "maximum": maximum,
                "unit": unit,
                "choices": choices,
            }

        out["messages"][m_name] = {
            "name": m_name,
            "frame_id": frame_id,
            "dlc": dlc,
            "signals": signals_out,
        }

    return out