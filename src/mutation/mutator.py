import random
from typing import Dict
from logger.base_logger import log_event  

<<<<<<< Updated upstream
=======
DEFAULT_BYTE_K_PROB   = 0.3  
DEFAULT_BYTE_WEIGHT = 1.0  
DEFAULT_EDGE_BIAS     = 0.0  

DEFAULT_BIT_K_PROB    = 0.4  
DEFAULT_BIT_WEIGHT  = 1.0  
DEFAULT_MSB_BIAS      = 0.0  
DEFAULT_LSB_BIAS      = 0.0  
DEFAULT_BUDGET        = 256
DEFAULT_MAX_OPS       = 3
>>>>>>> Stashed changes

class Mutator:
    def __init__(self, data: bytes, weights: Dict[str, float], min_length: int = 1):  
        """
        data: 변이 대상 원본 데이터 (bytes)
        weights: monitor로부터 전달받은 가중치 딕셔너리
        min_length: 최소 데이터 길이 (예외 방어용)
        """
        self.data = bytearray(data)
        self.weights = weights
        self.min_length = min_length  

    def mutate_manager(self) -> list[bytes]:
        budget        = int(self.weights.get("manager.budget", 256))
        max_ops       = int(self.weights.get("manager.max_ops", 3))
        enable_struct = bool(self.weights.get("manager.structural", True))
        include_orig  = bool(self.weights.get("manager.include_original", False))
        
        rng = random

        out: list[bytes] = []
        seen: set[bytes] = set()

        def add_snapshot_from_current_buf():
            b = bytes(self.data)
            if b not in seen:
                seen.add(b)
                out.append(b)

        base = bytes(self.data)

        if include_orig:
            self.data = bytearray(base)
            add_snapshot_from_current_buf()

        attempt_cap = budget * 20
        attempts = 0

        try:
            while len(out) < budget and attempts < attempt_cap:
                attempts += 1

                saved = self.data
                self.data = bytearray(base)
                try:
                    k = rng.randint(1, max_ops)

                    for _ in range(k):
                        op_kind = rng.choice([
                            "flip_bit",
                            "increment_bit",
                            "decrement_bit",
                            "increment_byte",
                            "decrement_byte",
                            "insert_byte" if enable_struct else "increment_byte",
                            "delete_byte" if enable_struct else "decrement_byte",
                        ])
                        try:
                            getattr(self, op_kind)()
                        except Exception as e:
                            print(f"[!] Mutation 실패: {op_kind} ({type(e).__name__})")
                            log_event(
                                source="mutator",
                                msg_id=0x00,
                                metric=op_kind,
                                value=str(type(e).__name__),
                                status="FAIL"
                            )
                            continue

                    add_snapshot_from_current_buf()
                finally:
                    self.data = saved

        except Exception as e:  
            print(f"[!] mutate_manager 전체 예외 발생: {type(e).__name__} - {e}")
            log_event("mutator", 0x00, "mutate_manager", str(type(e).__name__), "FAIL")
            self.data = bytearray(base)

        self.generated = out
        return out

    #  Bit-level mutation
    def flip_bit(self) -> None:
        """랜덤 바이트 내 특정 비트를 flip"""
        try: 
            byte_idxs = self.select_random_byte()
            if isinstance(byte_idxs, int):
                byte_idxs = [byte_idxs]
            if not byte_idxs:
                return
            byte_idx = random.choice(byte_idxs)
            if not (0 <= byte_idx < len(self.data)):
                return

            bit_idxs = self.select_random_bit()
            if isinstance(bit_idxs, int):
                bit_idxs = [bit_idxs]
            if not bit_idxs:
                return
            bit_idx = random.choice(bit_idxs)
            self.data[byte_idx] ^= (1 << bit_idx)
        except Exception as e:  
            print(f"[!] flip_bit 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "flip_bit", str(type(e).__name__), "FAIL")

    def increment_bit(self) -> None:
        try:
            byte_idxs = self.select_random_byte()
            if isinstance(byte_idxs, int):
                byte_idxs = [byte_idxs]
            if not byte_idxs:
                return
            byte_idx = random.choice(byte_idxs)
            if not (0 <= byte_idx < len(self.data)):
                return

            bit_idxs = self.select_random_bit()
            if isinstance(bit_idxs, int):
                bit_idxs = [bit_idxs]
            if not bit_idxs:
                return
            bit_idx = random.choice(bit_idxs)

            mask = (1 << bit_idx)
            if (self.data[byte_idx] & mask) == 0:
                self.data[byte_idx] |= mask
        except Exception as e:  
            print(f"[!] increment_bit 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "increment_bit", str(type(e).__name__), "FAIL")

    def decrement_bit(self) -> None:
        try:
            byte_idxs = self.select_random_byte()
            if isinstance(byte_idxs, int):
                byte_idxs = [byte_idxs]
            if not byte_idxs:
                return
            byte_idx = random.choice(byte_idxs)
            if not (0 <= byte_idx < len(self.data)):
                return

            bit_idxs = self.select_random_bit()
            if isinstance(bit_idxs, int):
                bit_idxs = [bit_idxs]
            if not bit_idxs:
                return
            bit_idx = random.choice(bit_idxs)

            mask = (1 << bit_idx)
            if (self.data[byte_idx] & mask) != 0:
                self.data[byte_idx] &= ~mask
        except Exception as e: 
            print(f"[!] decrement_bit 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "decrement_bit", str(type(e).__name__), "FAIL")

    def increment_byte(self) -> None:
        try:
            byte_idxs = self.select_random_byte()
            if isinstance(byte_idxs, int):
                byte_idxs = [byte_idxs]
            if not byte_idxs:
                return
            byte_idx = random.choice(byte_idxs)
            if 0 <= byte_idx < len(self.data):
                self.data[byte_idx] = (self.data[byte_idx] + 1) & 0xFF
        except Exception as e: 
            print(f"[!] increment_byte 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "increment_byte", str(type(e).__name__), "FAIL")

    def decrement_byte(self) -> None:
        try:
            byte_idxs = self.select_random_byte()
            if isinstance(byte_idxs, int):
                byte_idxs = [byte_idxs]
            if not byte_idxs:
                return
            byte_idx = random.choice(byte_idxs)
            if 0 <= byte_idx < len(self.data):
                self.data[byte_idx] = (self.data[byte_idx] - 1) & 0xFF
        except Exception as e:  
            print(f"[!] decrement_byte 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "decrement_byte", str(type(e).__name__), "FAIL")

    def insert_byte(self) -> None:
        try:
            insert_pos = random.randint(0, len(self.data))
            new_val = random.randint(0, 255)
            self.data.insert(insert_pos, new_val)
        except Exception as e:  
            print(f"[!] insert_byte 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "insert_byte", str(type(e).__name__), "FAIL")

    def delete_byte(self) -> None:
        try:
            if len(self.data) <= self.min_length:  
                return
            del_idxs = self.select_random_byte()
            if isinstance(del_idxs, int):
                del_idxs = [del_idxs]
            if not del_idxs:
                return
            del_pos = random.choice(del_idxs)
            if 0 <= del_pos < len(self.data):
                del self.data[del_pos]
        except Exception as e:  
            print(f"[!] delete_byte 예외 발생: {type(e).__name__}")
            log_event("mutator", 0x00, "delete_byte", str(type(e).__name__), "FAIL")

    #  Utility selectors
    def select_random_byte(self) -> list[int]:
        n = len(self.data)
        if n == 0:
            raise ValueError("빈 데이터에서는 바이트를 선택할 수 없습니다.")

        p = float(self.weights.get("byte_k_p", 0.3))
        k = 1
        while random.random() > p and k < n:
            k += 1
        k = min(k, max(1, n // 3))

        ws = [float(self.weights.get(f"byte:{i}", 1.0)) for i in range(n)]
        edge = float(self.weights.get("edge_bias", 0.0))
        if edge > 0 and n >= 2:
            ws[0] += edge
            ws[-1] += edge

        total = sum(ws)
        if total <= 0:
            return random.sample(range(n), k)
        choices = random.choices(range(n), weights=ws, k=k)
        return list(sorted(set(choices)))

    def select_random_bit(self) -> list[int]:
        p = float(self.weights.get("bit_k_p", 0.4))
        k = 1
        while random.random() > p and k < 8:
            k += 1
        k = min(k, 8)

        w = [float(self.weights.get(f"bit:{b}", 1.0)) for b in range(8)]
        msb = float(self.weights.get("msb_bias", 0.0))
        lsb = float(self.weights.get("lsb_bias", 0.0))
        if msb > 0:
            w = [wb + msb * (b / 7.0) for b, wb in enumerate(w)]
        if lsb > 0:
            w = [wb + lsb * ((7 - b) / 7.0) for b, wb in enumerate(w)]

        total = sum(w)
        if total <= 0:
            return random.sample(range(8), k)
        choices = random.choices(range(8), weights=w, k=k)
        return list(sorted(set(choices)))
