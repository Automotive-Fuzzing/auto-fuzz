# src/pipeline.py

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .monitor.dbc_monitor import DBCMonitor
from .monitor.monitor_manager import MonitorManager
from .monitor.timing_monitor import TimingMonitor
from .monitor.uds_monitor import UDSMonitor
from .mutation.mutator import Mutator
from .repro.candidate import CandidateSnapshot
from .repro.evidence_fusion import HARD_FAIL, SOFT_FAIL, EvidenceFusion
from .repro.report import MONITOR_NAMES, ResultFrame
from .repro.storage import ReproStorage
from .seeds.dbc_parser import DbcParser
from .seeds.factory import build_initial_seeds_from_parsed_dbc
from .seeds.mutation_engine import MutationEngine
from .seeds.seed_manager import Seed, SeedManager
from .seeds.seed_queue import SeedQueue
from .seeds.tx_log import TxFrame, TxLog

FAIL_STATUSES = frozenset({"crashed", "timeout"})


class AutoFuzzPipeline:
    @staticmethod
    def _initial_mutation_weights(
        initial_budget: int = 2,
        initial_max_ops: int = 1,
    ) -> Dict[str, Any]:
        return {
            "manager.budget": max(0, int(initial_budget)),
            "manager.max_ops": max(1, int(initial_max_ops)),
            "manager.structural": False,
            "manager.include_original": False,
        }

    @staticmethod
    def _generate_initial_mutation_payloads(
        seed: Seed,
        initial_budget: int = 2,
        initial_max_ops: int = 1,
    ) -> List[bytes]:
        if initial_budget <= 0:
            return []

        base_payload = bytes(seed.payload or b"\x00" * max(1, int(seed.dlc or 8)))
        if not base_payload:
            return []

        mutator = Mutator(
            data=base_payload,
            weights=AutoFuzzPipeline._initial_mutation_weights(
                initial_budget=initial_budget,
                initial_max_ops=initial_max_ops,
            ),
            min_length=max(1, min(len(base_payload), int(seed.dlc or len(base_payload) or 8))),
        )

        mutated = mutator.mutate_manager()

        out: List[bytes] = []
        seen: set[bytes] = set()
        for payload in mutated:
            payload = bytes(payload)
            if payload == base_payload:
                continue
            if payload in seen:
                continue
            seen.add(payload)
            out.append(payload)

        return out[: max(0, int(initial_budget))]

    @staticmethod
    def register_seeds(
        dbc_path: str,
        db_path: str = "seeds.db",
        enable_initial_mutation: bool = True,
        initial_budget: int = 2,
        initial_max_ops: int = 1,
    ) -> int:
        parsed = DbcParser(dbc_path).parse()
        seeds = build_initial_seeds_from_parsed_dbc(parsed)

        manager = SeedManager(db_path)
        queue = SeedQueue(db_path)
        total = 0

        try:
            for seed in seeds:
                root_seed_id = manager.insert_seed(seed)
                if root_seed_id is None:
                    continue

                queue.push(root_seed_id)
                total += 1

                if not enable_initial_mutation:
                    continue

                root_seed = manager.get_seed(root_seed_id)
                if root_seed is None:
                    continue

                initial_payloads = AutoFuzzPipeline._generate_initial_mutation_payloads(
                    root_seed,
                    initial_budget=initial_budget,
                    initial_max_ops=initial_max_ops,
                )

                for idx, payload in enumerate(initial_payloads, start=1):
                    child_id = manager.create_child_seed(
                        parent=root_seed,
                        payload=payload,
                        priority_delta=0,
                        status="queued",
                        extra_meta={
                            "source": "initial_mutation",
                            "initial_mutation": True,
                            "initial_mutation_order": idx,
                            "initial_budget": int(initial_budget),
                            "initial_max_ops": int(initial_max_ops),
                        },
                    )
                    if child_id is None:
                        continue

                    queue.push(child_id)
                    total += 1
        finally:
            queue.close()
            manager.close()

        return total

    @staticmethod
    def list_seeds(db_path: str = "seeds.db") -> List[Seed]:
        manager = SeedManager(db_path)
        try:
            return manager.get_all()
        finally:
            manager.close()

    def __init__(self, cfg: Dict[str, Any], can_iface: Optional[object] = None):
        self.cfg = cfg
        self.can = can_iface
        self.running = False

        self.db_path = cfg["paths"]["seed_db"]
        self.dbc_path = cfg["paths"]["dbc"]
        self.artifacts_root = cfg.get("paths", {}).get("artifacts", "artifacts")

        self.seed_manager = SeedManager(self.db_path)
        self.queue = SeedQueue(self.db_path)
        self.tx_log = TxLog(self.seed_manager)
        self.storage = ReproStorage(self.artifacts_root)
        self.mutation_engine = MutationEngine(self.seed_manager)

        self.parsed_dbc = DbcParser(self.dbc_path).parse()

        fuzz_cfg = cfg.get("fuzz", {})
        self.repro_trials = int(fuzz_cfg.get("repro_trials", 5))
        self.restore_pre = int(fuzz_cfg.get("restore_pre_frames", 5))
        self.restore_post = int(fuzz_cfg.get("restore_post_frames", 2))
        self.inter_frame_delay = float(fuzz_cfg.get("inter_frame_delay", 0.01))
        self.seed_interval = float(fuzz_cfg.get("seed_interval", 0.05))
        self.monitor_grace = float(fuzz_cfg.get("monitor_grace", 0.5))

        self.monitor_cfg = cfg.get("monitor", {})
        self.monitor_manager_cfg = self.monitor_cfg.get("manager", {})
        self.timing_monitor_cfg = self.monitor_cfg.get("timing", {})
        self.dbc_monitor_cfg = self.monitor_cfg.get("dbc", {})
        self.uds_monitor_cfg = self.monitor_cfg.get("uds", {})

    def close(self) -> None:
        self.queue.close()
        self.seed_manager.close()

    def stop(self) -> None:
        self.running = False

    def _target_arb_id(self, seed: Seed) -> int:
        if self.cfg["can"].get("force_default_id", False):
            return int(self.cfg["can"]["default_id"], 16)
        if seed.arb_id is not None:
            return int(seed.arb_id)
        return int(seed.message_id)

    def _uds_target_id(self) -> int:
        uds_id = self.uds_monitor_cfg.get("target_uds_id")
        if uds_id is None:
            return 0x70E
        return int(uds_id)

    def _can_channel(self) -> str:
        return str(self.cfg["can"]["channel"])

    def _build_dbc_rules(self, arb_id: int) -> Dict[str, Dict[str, Any]]:
        enum_relax_threshold = int(self.dbc_monitor_cfg.get("enum_relax_threshold", 3))

        for msg in self.parsed_dbc.get("messages", []):
            if int(msg.get("id")) != int(arb_id):
                continue

            rules: Dict[str, Dict[str, Any]] = {}

            for sig in msg.get("signals", []):
                factor = sig.get("factor", 1)
                offset = sig.get("offset", 0)
                length = sig.get("length")
                minimum = sig.get("minimum")
                maximum = sig.get("maximum")
                choices = sig.get("choices") or {}

                if length == 1:
                    kind = "bool"
                    enum_vals = [0, 1]
                    coerce_int = True
                elif factor not in (None, 0, 1):
                    kind = "float"
                    enum_vals = None
                    coerce_int = False
                else:
                    kind = "int"
                    enum_vals = list(choices.keys()) if isinstance(choices, dict) else None
                    coerce_int = True

                if (
                    enum_vals is not None
                    and len(enum_vals) <= enum_relax_threshold
                    and kind in ("int", "float")
                    and length not in (1, None)
                ):
                    enum_vals = None

                rules[sig["name"]] = {
                    "kind": kind,
                    "enum": enum_vals,
                    "min": minimum,
                    "max": maximum,
                    "factor": factor if factor not in (None, 0) else 1,
                    "offset": offset if offset is not None else 0,
                    "monotonic": None,
                    "coerce_int": coerce_int,
                }

            return rules

        return {}

    def _build_monitor_manager(self, arb_id: int) -> MonitorManager:
        timing_monitor = TimingMonitor.from_config(
            self.timing_monitor_cfg,
            channel=self._can_channel(),
            target_id=arb_id,
        )

        dbc_monitor = DBCMonitor.from_config(
            self.dbc_monitor_cfg,
            channel=self._can_channel(),
            dbc_path=self.dbc_path,
            target_id=arb_id,
            seed_db_path=self.db_path,
            rules=self._build_dbc_rules(arb_id),
        )

        uds_monitor = UDSMonitor.from_config(
            self.uds_monitor_cfg,
            nrc_cfg_path="config/nrc_weights.yaml",
        )

        return MonitorManager(
            timing_monitor=timing_monitor,
            uds_monitor=uds_monitor,
            dbc_monitor=dbc_monitor,
            thread_timeout_as_anomaly=bool(
                self.monitor_manager_cfg.get("thread_timeout_as_anomaly", False)
            ),
            crash_as_anomaly=bool(
                self.monitor_manager_cfg.get("crash_as_anomaly", True)
            ),
        )

    def _collect_monitor_results(self, arb_id: int) -> Dict[str, Dict[str, Any]]:
        timing_timeout = float(
            self.cfg.get("fuzz", {}).get(
                "timing_timeout",
                self.timing_monitor_cfg.get("default_timeout_sec", 5.0),
            )
        )
        dbc_timeout = float(
            self.cfg.get("fuzz", {}).get(
                "dbc_timeout",
                self.dbc_monitor_cfg.get("default_window_sec", 1.5),
            )
        )

        uds_timeout = float(
            self.uds_monitor_cfg.get("request_timeout", 1.0)
        )

        wait_timeout = max(timing_timeout, dbc_timeout, uds_timeout) + self.monitor_grace

        manager = self._build_monitor_manager(arb_id)
        manager.start_monitors(
            timing_timeout=timing_timeout,
            dbc_timeout=dbc_timeout,
        )
        manager.wait_for_completion(timeout=wait_timeout)

        scores = manager.get_scores()
        completed = manager.get_completion_status()
        status = manager.get_status()
        anomalies = manager.get_anomalies()
        details = manager.get_details()

        out: Dict[str, Dict[str, Any]] = {}
        for name in MONITOR_NAMES:
            detail = details.get(name, {})
            out[name] = {
                "score": float(scores.get(name, detail.get("score", 0.0))),
                "completed": bool(completed.get(name, False)),
                "status": str(status.get(name, detail.get("status", "unknown"))).lower(),
                "is_anomalous": bool(anomalies.get(name, detail.get("is_anomalous", False))),
            }

            if "summary" in detail:
                out[name]["summary"] = detail["summary"]

        return out

    def _save_monitor_result(self, seed_id: int, monitor_result: Dict[str, Any]) -> None:
        seed = self.seed_manager.get_seed(seed_id)
        if seed is None:
            return

        meta = dict(seed.meta or {})
        meta["monitor_result"] = monitor_result

        self.seed_manager.conn.execute(
            """
            UPDATE seeds
            SET meta_json = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (json.dumps(meta, ensure_ascii=False), seed_id),
        )
        self.seed_manager.conn.commit()

    def _send_raw_payload(self, payload: bytes, arb_id: int, is_extended: bool = False) -> None:
        if self.can is None:
            print(f"[Stub:Tx] ID={hex(arb_id)} | Data={payload.hex()}")
            return
        self.can.send_raw(payload, arb_id=arb_id, is_extended=is_extended)

    def _send_one(self, seed: Seed) -> int:
        arb_id = self._target_arb_id(seed)
        payload = bytes(seed.payload or b"\x00" * int(seed.dlc or 8))
        dlc = int(seed.dlc or len(payload) or 8)

        self._send_raw_payload(payload, arb_id, bool(seed.is_extended))

        send_idx = self.tx_log.append(
            TxFrame(
                arb_id=arb_id,
                payload=payload,
                dlc=dlc,
            ),
            seed_id=seed.id,
        )

        self.seed_manager.set_last_send_idx(int(seed.id), send_idx)
        self.seed_manager.update_status(int(seed.id), "sent")
        return send_idx

    def _is_fail(self, monitor_result: Dict[str, Dict[str, Any]]) -> bool:
        for result in monitor_result.values():
            status = str(result.get("status", "ok")).lower()
            if status in FAIL_STATUSES:
                return True

            if bool(result.get("is_anomalous", False)):
                return True

            summary = result.get("summary", {})
            if isinstance(summary, dict):
                summary_status = str(summary.get("status", "")).lower()
                if summary_status in FAIL_STATUSES:
                    return True

        return False

    def _snapshot_from_seed(self, seed: Seed) -> CandidateSnapshot:
        monitor_json = (seed.meta or {}).get("monitor_result")
        return CandidateSnapshot(
            id=int(seed.id),
            arb_id=self._target_arb_id(seed),
            payload=bytes(seed.payload or b""),
            dlc=int(seed.dlc or len(seed.payload or b"")),
            priority=seed.priority,
            parent_id=seed.parent_id,
            root_id=seed.root_id,
            depth=seed.depth,
            monitor_json=monitor_json,
            repro_verdict=seed.repro_verdict,
            repro_rate=seed.repro_rate,
            last_evidence=seed.last_evidence,
            repro_json=seed.repro_json,
            last_send_idx=seed.last_send_idx,
            fingerprint=seed.fingerprint,
        )

    def _restore_frames(self, snap: CandidateSnapshot) -> List[TxFrame]:
        return [
            TxFrame(
                arb_id=snap.arb_id,
                payload=bytes(snap.payload or b"\x00" * max(1, int(snap.dlc or 8))),
                dlc=int(snap.dlc or len(snap.payload or b"") or 8),
            )
        ]

    def _run_reproduction(
        self,
        snap: CandidateSnapshot,
        n: int,
        is_extended: bool = False,
    ) -> List[Dict[str, Any]]:
        frames = self._restore_frames(snap)
        trial_results: List[Dict[str, Any]] = []

        for trial_idx in range(1, max(1, n) + 1):
            for frame in frames:
                self._send_raw_payload(
                    frame.payload,
                    frame.arb_id,
                    is_extended=is_extended,
                )
                time.sleep(self.inter_frame_delay)

            result = self._collect_monitor_results(snap.arb_id)
            result["trial"] = trial_idx
            trial_results.append(result)

        return trial_results

    def _mutation_weights(self) -> Dict[str, float]:
        mutation_cfg = dict(self.cfg.get("mutation", {}))
        mutation_cfg.setdefault(
            "manager.budget",
            self.cfg.get("fuzz", {}).get("child_budget", 16),
        )
        mutation_cfg.setdefault("manager.max_ops", 3)
        return mutation_cfg

    def run(self, max_seeds: Optional[int] = None) -> Dict[str, Any]:
        self.running = True
        processed = 0
        findings: List[Dict[str, Any]] = []
        last_result = {
            name: {"score": 0.0, "completed": False, "status": "skipped"}
            for name in MONITOR_NAMES
        }

        try:
            while self.running:
                if max_seeds is not None and processed >= max_seeds:
                    break

                seed_id = self.queue.pop()
                if seed_id is None:
                    break

                seed = self.seed_manager.get_seed(int(seed_id))
                if seed is None or seed.id is None:
                    continue

                send_idx = self._send_one(seed)
                time.sleep(self.seed_interval)

                arb_id = self._target_arb_id(seed)
                monitor_result = self._collect_monitor_results(arb_id)
                self._save_monitor_result(seed.id, monitor_result)

                processed += 1
                last_result = monitor_result

                if not self._is_fail(monitor_result):
                    self.seed_manager.update_status(seed.id, "ok")
                    continue

                reloaded = self.seed_manager.get_seed(seed.id)
                if reloaded is None:
                    continue

                snap = self._snapshot_from_seed(reloaded)
                snap.last_send_idx = send_idx
                self.storage.save_candidate(seed.id, snap)

                trial_results = self._run_reproduction(
                    snap,
                    self.repro_trials,
                    is_extended=bool(reloaded.is_extended),
                )
                frame = ResultFrame.from_trial_results(seed.id, trial_results)

                fusion = EvidenceFusion.from_config(
                    self.cfg.get("fusion", {})
                ).evaluate(frame)

                self.seed_manager.save_repro_summary(
                    seed.id,
                    fusion.verdict,
                    fusion.repro_rate,
                    last_evidence=json.dumps(fusion.detail, ensure_ascii=False),
                )
                self.seed_manager.save_repro_report(seed.id, frame.to_dict())
                self.storage.save_report(seed.id, frame.to_dict())

                confirmed = fusion.verdict in {SOFT_FAIL, HARD_FAIL}
                if confirmed:
                    self.seed_manager.update_status(seed.id, "confirmed_fail")
                    child_ids = self.mutation_engine.mutate_and_store_children(
                        reloaded,
                        weights=self._mutation_weights(),
                        min_length=max(1, int(reloaded.dlc or len(reloaded.payload or b""))),
                        priority_delta=0,
                        extra_meta={
                            "mutation_parent": reloaded.id,
                            "source": "confirmed_fail",
                        },
                    )
                    for child_id in child_ids:
                        self.queue.push(child_id)
                else:
                    self.seed_manager.update_status(seed.id, "reproduced_pass")
                    child_ids = []

                findings.append(
                    {
                        "seed_id": seed.id,
                        "arb_id": arb_id,
                        "last_send_idx": send_idx,
                        "monitor_result": monitor_result,
                        "reproduction": frame.to_dict(),
                        "fusion": {
                            "verdict": fusion.verdict,
                            "fusion_score": fusion.fusion_score,
                            "repro_rate": fusion.repro_rate,
                            "detail": fusion.detail,
                        },
                        "confirmed": confirmed,
                        "child_ids": child_ids,
                    }
                )

            return {
                "processed": processed,
                "remaining_queue": len(self.queue.queue),
                "last_result": last_result,
                "findings": findings,
            }
        finally:
            self.running = False
            self.close()