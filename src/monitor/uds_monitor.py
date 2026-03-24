# src/monitor/uds_monitor.py

from __future__ import annotations

import os
import time
from typing import Dict, Any, Optional

import can
import isotp
import yaml

from ..logger.base_logger import log_event


CAN_CHANNEL = "can0"
TARGET_UDS_ID = 0x70E
UDS_RESPONSE_OFFSET = 0x6A
PADDING_BYTE = 0xAA

DEFAULT_ISOTP_PARAMS = {
    "stmin": 0,
    "blocksize": 8,
    "wftmax": 0,
    "tx_data_length": 8,
    "tx_padding": PADDING_BYTE,
    "rx_flowcontrol_timeout": 1000,
    "rx_consecutive_frame_timeout": 1000,
}

SCORE_FAIL = 1.0
SCORE_SUCCESS = 0.0
DTC_THRESHOLD = 10


def load_nrc_fail_list(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"NRC config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "nrc_fail_list" not in data:
        raise ValueError("Invalid NRC config: missing 'nrc_fail_list'")

    fail_set = set()
    for item in data["nrc_fail_list"]:
        try:
            nrc = int(item, 16) if isinstance(item, str) else int(item)
            fail_set.add(nrc)
        except Exception as e:
            raise ValueError(f"Invalid NRC value {item} ({e})")

    return fail_set


class UDSMonitor:
    def __init__(
        self,
        nrc_cfg_path: str = "config/nrc_weights.yaml",
        can_channel: str = CAN_CHANNEL,
        target_uds_id: int = TARGET_UDS_ID,
        response_offset: int = UDS_RESPONSE_OFFSET,
        padding_byte: int = PADDING_BYTE,
        isotp_params: Optional[Dict[str, Any]] = None,
        request_timeout: float = 1.0,
        retry_count: int = 1,
        dtc_threshold: int = DTC_THRESHOLD,
        dtc_collect_timeout: float = 2.0,
        dtc_response_timeout: float = 0.5,
        score_fail: float = SCORE_FAIL,
        score_success: float = SCORE_SUCCESS,
        crash_as_anomaly: bool = True,
    ):
        self.NRC_FAIL_SET = load_nrc_fail_list(nrc_cfg_path)

        self.can_channel = can_channel
        self.target_uds_id = target_uds_id
        self.response_offset = response_offset
        self.padding_byte = padding_byte

        self.isotp_params = dict(DEFAULT_ISOTP_PARAMS)
        if isotp_params:
            self.isotp_params.update(isotp_params)

        self.request_timeout = request_timeout
        self.retry_count = retry_count
        self.dtc_threshold = dtc_threshold
        self.dtc_collect_timeout = dtc_collect_timeout
        self.dtc_response_timeout = dtc_response_timeout
        self.score_fail = score_fail
        self.score_success = score_success
        self.crash_as_anomaly = crash_as_anomaly

        self.bus: Optional[can.BusABC] = None
        self.stack: Optional[isotp.CanStack] = None

        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False
        self._last_reason: Optional[str] = None
        self._total_dtc = 0

    @classmethod
    def from_config(
        cls,
        cfg: Optional[Dict[str, Any]],
        *,
        nrc_cfg_path: str = "config/nrc_weights.yaml",
    ) -> "UDSMonitor":
        cfg = cfg or {}
        return cls(
            nrc_cfg_path=nrc_cfg_path,
            can_channel=cfg.get("can_channel", CAN_CHANNEL),
            target_uds_id=cfg.get("target_uds_id", TARGET_UDS_ID),
            response_offset=cfg.get("response_offset", UDS_RESPONSE_OFFSET),
            padding_byte=cfg.get("padding_byte", PADDING_BYTE),
            isotp_params=cfg.get("isotp_params"),
            request_timeout=cfg.get("request_timeout", 1.0),
            retry_count=cfg.get("retry_count", 1),
            dtc_threshold=cfg.get("dtc_threshold", DTC_THRESHOLD),
            dtc_collect_timeout=cfg.get("dtc_collect_timeout", 2.0),
            dtc_response_timeout=cfg.get("dtc_response_timeout", 0.5),
            score_fail=cfg.get("score_fail", SCORE_FAIL),
            score_success=cfg.get("score_success", SCORE_SUCCESS),
            crash_as_anomaly=cfg.get("crash_as_anomaly", True),
        )

    def _reset_state(self):
        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False
        self._last_reason = None
        self._total_dtc = 0

    def _open_stack(self) -> None:
        self.bus = can.interface.Bus(channel=self.can_channel, bustype="socketcan")
        addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            txid=self.target_uds_id,
            rxid=self.target_uds_id + self.response_offset,
        )
        self.stack = isotp.CanStack(bus=self.bus, address=addr, params=self.isotp_params)

    def _close_stack(self) -> None:
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass
        self.bus = None
        self.stack = None

    def send_request(self, data):
        if self.stack is None:
            raise RuntimeError("UDS stack is not initialized")

        self.stack.send(bytes(data))
        while self.stack.transmitting():
            self.stack.process()
            time.sleep(0.01)

    def recv_response(self, timeout: Optional[float] = None):
        if self.stack is None:
            raise RuntimeError("UDS stack is not initialized")

        timeout = timeout if timeout is not None else self.request_timeout
        start = time.monotonic()

        while time.monotonic() - start < timeout:
            self.stack.process()
            if self.stack.available():
                resp = list(self.stack.recv())
                if len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78:
                    log_event("uds", self.target_uds_id, "NRC_0x78_pending", "wait_more", "INFO")
                    continue
                return resp
            time.sleep(0.01)
        return None

    def start(self) -> float:
        self._reset_state()
        self._status = "running"

        try:
            self._open_stack()

            print("[INFO] UDS Monitor - Checking session entry (0x10)...")
            if not self._send_once_or_retry([0x10, 0x01], "session_entry"):
                self._mark_fail("session_entry_fail")
                return self._fail_score

            print("[INFO] UDS Monitor - Checking tester present (0x3E)...")
            if not self._send_once_or_retry([0x3E, 0x00], "tester_present"):
                self._mark_fail("tester_present_fail")
                return self._fail_score

            print("[INFO] UDS Monitor - Reading DTC (0x19)...")
            self.send_request([0x19, 0x02, 0x20])
            total_dtc, dtc_failed = self.collect_all_dtc()
            self._total_dtc = total_dtc

            if dtc_failed:
                self._mark_fail("dtc_collection_fail")
                return self._fail_score

            if total_dtc > self.dtc_threshold:
                self._mark_fail(f"dtc_found_{total_dtc}")
                return self._fail_score

            print(f"[OK] UDS Monitor - All checks passed (DTC count: {total_dtc})")
            log_event("uds", self.target_uds_id, "monitor_result", "all_passed", "OK")
            self._fail_score = self.score_success
            self._is_anomalous = False
            self._status = "ok"
            self._last_reason = "all_passed"

        except Exception as e:
            print(f"[FAIL] UDS Monitor - Exception occurred: {e}")
            log_event("uds", self.target_uds_id, "monitor_result", f"exception_{str(e)}", "FAIL")
            self._fail_score = self.score_fail
            self._is_anomalous = self.crash_as_anomaly
            self._status = "crashed"
            self._last_reason = f"exception: {e}"

        finally:
            self._close_stack()

        print(
            f"[INFO] UDS Monitor finished - "
            f"score={self._fail_score:.1f}, status={self._status}, anomalous={self._is_anomalous}"
        )
        return self._fail_score

    def _mark_fail(self, reason: str) -> None:
        print(f"[FAIL] UDS Monitor - {reason}")
        log_event("uds", self.target_uds_id, "monitor_result", reason, "FAIL")
        self._fail_score = self.score_fail
        self._is_anomalous = True
        self._status = "failed"
        self._last_reason = reason

    def _send_once_or_retry(self, data, step_name):
        max_attempts = self.retry_count + 1

        for attempt in range(1, max_attempts + 1):
            self.send_request(data)
            resp = self.recv_response(timeout=self.request_timeout)

            if not resp:
                if attempt < max_attempts:
                    print(f"[WARN] {step_name} - No response, retrying... ({attempt}/{max_attempts})")
                    continue

                print(f"[FAIL] {step_name} - No response after retry")
                log_event("uds", self.target_uds_id, f"{step_name}_no_response", "fail", "FAIL")
                return False

            if resp[0] == 0x7F:
                nrc = resp[2]
                print(f"[NRC] {step_name} - Received NRC: {hex(nrc)}")

                if nrc in self.NRC_FAIL_SET:
                    print(f"[FAIL] {step_name} - NRC {hex(nrc)} is in fail list")
                    log_event("uds", self.target_uds_id, f"{step_name}_NRC_{hex(nrc)}", "fail_list_hit", "FAIL")
                    return False

                print(f"[WARN] {step_name} - NRC {hex(nrc)} not in fail list (continuing)")
                log_event("uds", self.target_uds_id, f"{step_name}_NRC_unknown_{hex(nrc)}", nrc, "WARN")
                return True

            print(f"[OK] {step_name} - Response OK")
            log_event("uds", self.target_uds_id, step_name, "response_ok", "OK")
            return True

        return False

    def collect_all_dtc(self):
        accumulated_data = bytearray()
        start_time = time.monotonic()
        failed = False

        while time.monotonic() - start_time < self.dtc_collect_timeout:
            resp = self.recv_response(timeout=self.dtc_response_timeout)
            if not resp:
                break

            if len(resp) >= 4 and resp[0] == 0x59 and resp[1] == 0x02:
                accumulated_data.extend(resp[3:])
                print(f"[DTC] Received {len(resp[3:])} bytes of DTC data")

            elif resp[0] == 0x7F:
                nrc = resp[2]
                print(f"[NRC] DTC read - Received NRC: {hex(nrc)}")

                if nrc in self.NRC_FAIL_SET:
                    print(f"[FAIL] DTC - NRC {hex(nrc)} is in fail list")
                    log_event("uds", self.target_uds_id, f"DTC_NRC_{hex(nrc)}", "fail_list_hit", "FAIL")
                    failed = True
                    break

                print(f"[WARN] DTC - NRC {hex(nrc)} not in fail list (continuing)")
                log_event("uds", self.target_uds_id, f"DTC_NRC_unknown_{hex(nrc)}", nrc, "WARN")
                continue

        total_dtc = len(accumulated_data) // 4
        print(f"[DTC] Total DTC count: {total_dtc}")
        log_event("uds", self.target_uds_id, "DTC_count", total_dtc, "OK" if total_dtc == 0 else "INFO")

        return total_dtc, failed

    def get_fail_score(self) -> float:
        return self._fail_score

    def get_status(self) -> str:
        return self._status

    def is_anomalous(self) -> bool:
        return self._is_anomalous

    def get_summary(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "score": self._fail_score,
            "is_anomalous": self._is_anomalous,
            "last_reason": self._last_reason,
            "total_dtc": self._total_dtc,
            "dtc_threshold": self.dtc_threshold,
        }
