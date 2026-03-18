# src/monitor/uds_monitor.py
import time
import os
import can
import isotp
import yaml
from typing import Dict, Any, Optional
from ..logger.base_logger import log_event

CAN_CHANNEL = "can0"
TARGET_UDS_ID = 0x70E
UDS_RESPONSE_OFFSET = 0x6A
PADDING_BYTE = 0xAA

isotp_params = {
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
    def __init__(self, nrc_cfg_path: str = "config/nrc_weights.yaml"):
        self.NRC_FAIL_SET = load_nrc_fail_list(nrc_cfg_path)

        self.bus = can.interface.Bus(channel=CAN_CHANNEL, bustype="socketcan")
        addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
            txid=TARGET_UDS_ID,
            rxid=TARGET_UDS_ID + UDS_RESPONSE_OFFSET
        )
        self.stack = isotp.CanStack(bus=self.bus, address=addr, params=isotp_params)

        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False
        self._last_reason: Optional[str] = None
        self._total_dtc = 0

    def _reset_state(self):
        self._fail_score = 0.0
        self._status = "idle"
        self._is_anomalous = False
        self._last_reason = None
        self._total_dtc = 0

    def send_request(self, data):
        self.stack.send(bytes(data))
        while self.stack.transmitting():
            self.stack.process()
            time.sleep(0.01)

    def recv_response(self, timeout=1.0):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            self.stack.process()
            if self.stack.available():
                resp = list(self.stack.recv())
                if len(resp) >= 3 and resp[0] == 0x7F and resp[2] == 0x78:
                    log_event("uds", TARGET_UDS_ID, "NRC_0x78_pending", "wait_more", "INFO")
                    continue
                return resp
            time.sleep(0.01)
        return None

    def start(self) -> float:
        self._reset_state()
        self._status = "running"

        try:
            print("[INFO] UDS Monitor - Checking session entry (0x10)...")
            if not self._send_once_or_retry([0x10, 0x01], "session_entry"):
                print("[FAIL] UDS Monitor - Session entry failed")
                log_event("uds", TARGET_UDS_ID, "monitor_result", "session_entry_fail", "FAIL")
                self._fail_score = SCORE_FAIL
                self._is_anomalous = True
                self._status = "ok"
                self._last_reason = "session_entry_fail"
                return self._fail_score

            print("[INFO] UDS Monitor - Checking tester present (0x3E)...")
            if not self._send_once_or_retry([0x3E, 0x00], "tester_present"):
                print("[FAIL] UDS Monitor - Tester present failed")
                log_event("uds", TARGET_UDS_ID, "monitor_result", "tester_present_fail", "FAIL")
                self._fail_score = SCORE_FAIL
                self._is_anomalous = True
                self._status = "ok"
                self._last_reason = "tester_present_fail"
                return self._fail_score

            print("[INFO] UDS Monitor - Reading DTC (0x19)...")
            self.send_request([0x19, 0x02, 0x20])
            total_dtc, dtc_failed = self.collect_all_dtc()
            self._total_dtc = total_dtc

            if dtc_failed:
                print("[FAIL] UDS Monitor - DTC collection failed")
                log_event("uds", TARGET_UDS_ID, "monitor_result", "dtc_collection_fail", "FAIL")
                self._fail_score = SCORE_FAIL
                self._is_anomalous = True
                self._status = "ok"
                self._last_reason = "dtc_collection_fail"
                return self._fail_score

            if total_dtc > DTC_THRESHOLD:
                print(f"[FAIL] UDS Monitor - DTC found: {total_dtc} DTC(s)")
                log_event("uds", TARGET_UDS_ID, "monitor_result", f"dtc_found_{total_dtc}", "FAIL")
                self._fail_score = SCORE_FAIL
                self._is_anomalous = True
                self._status = "ok"
                self._last_reason = f"dtc_found_{total_dtc}"
                return self._fail_score

            print(f"[OK] UDS Monitor - All checks passed (DTC count: {total_dtc})")
            log_event("uds", TARGET_UDS_ID, "monitor_result", "all_passed", "OK")
            self._fail_score = SCORE_SUCCESS
            self._is_anomalous = False
            self._status = "ok"
            self._last_reason = "all_passed"

        except Exception as e:
            print(f"[FAIL] UDS Monitor - Exception occurred: {e}")
            log_event("uds", TARGET_UDS_ID, "monitor_result", f"exception_{str(e)}", "FAIL")
            self._fail_score = SCORE_FAIL
            self._is_anomalous = False
            self._status = "crashed"
            self._last_reason = f"exception: {e}"

        finally:
            try:
                self.bus.shutdown()
            except Exception:
                pass

        print(
            f"[INFO] UDS Monitor finished - "
            f"score={self._fail_score:.1f}, status={self._status}, anomalous={self._is_anomalous}"
        )
        return self._fail_score

    def _send_once_or_retry(self, data, step_name):
        self.send_request(data)
        resp = self.recv_response(timeout=1.0)

        if not resp:
            print(f"[WARN] {step_name} - No response, retrying...")
            self.send_request(data)
            resp = self.recv_response(timeout=1.0)

            if not resp:
                print(f"[FAIL] {step_name} - No response after retry")
                log_event("uds", TARGET_UDS_ID, f"{step_name}_no_response", "fail", "FAIL")
                return False

        if resp[0] == 0x7F:
            nrc = resp[2]
            print(f"[NRC] {step_name} - Received NRC: {hex(nrc)}")

            if nrc in self.NRC_FAIL_SET:
                print(f"[FAIL] {step_name} - NRC {hex(nrc)} is in fail list")
                log_event("uds", TARGET_UDS_ID, f"{step_name}_NRC_{hex(nrc)}", "fail_list_hit", "FAIL")
                return False
            else:
                print(f"[WARN] {step_name} - NRC {hex(nrc)} not in fail list (continuing)")
                log_event("uds", TARGET_UDS_ID, f"{step_name}_NRC_unknown_{hex(nrc)}", nrc, "WARN")
                return True

        print(f"[OK] {step_name} - Response OK")
        log_event("uds", TARGET_UDS_ID, step_name, "response_ok", "OK")
        return True

    def collect_all_dtc(self):
        accumulated_data = bytearray()
        start_time = time.monotonic()
        failed = False

        while time.monotonic() - start_time < 2.0:
            resp = self.recv_response(timeout=0.5)
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
                    log_event("uds", TARGET_UDS_ID, f"DTC_NRC_{hex(nrc)}", "fail_list_hit", "FAIL")
                    failed = True
                    break
                else:
                    print(f"[WARN] DTC - NRC {hex(nrc)} not in fail list (continuing)")
                    log_event("uds", TARGET_UDS_ID, f"DTC_NRC_unknown_{hex(nrc)}", nrc, "WARN")
                    continue

        total_dtc = len(accumulated_data) // 4
        print(f"[DTC] Total DTC count: {total_dtc}")
        log_event("uds", TARGET_UDS_ID, "DTC_count", total_dtc, "OK" if total_dtc == 0 else "INFO")

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
        }