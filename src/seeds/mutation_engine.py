# src/seeds/mutation_engine.py
from __future__ import annotations

from typing import Dict, List, Optional

from .seed_manager import Seed, SeedManager
from ..mutation.mutator import Mutator

class MutationEngine:
    """
    parent Seed -> mutated payloads -> child Seed insert -> new child ids 반환
    """

    def __init__(self, seed_manager: SeedManager):
        self.sm = seed_manager

    def mutate_and_store_children(
        self,
        parent: Seed,
        *,
        weights: Optional[Dict[str, float]] = None,
        min_length: int = 1,
        priority_delta: int = 0,
        extra_meta: Optional[dict] = None,
    ) -> List[int]:
        weights = weights or {}

        base_payload = bytes(parent.payload or b"")[: int(parent.dlc)]
        mut = Mutator(data=base_payload, weights=weights, min_length=min_length)
        mutated_payloads = mut.mutate_manager()

        new_ids: List[int] = []
        for p in mutated_payloads:
            new_id = self.sm.create_child_seed(
                parent,
                payload=p,
                priority_delta=priority_delta,
                status="queued",
                extra_meta=extra_meta,
            )
            if new_id is not None:
                new_ids.append(int(new_id))

        return new_ids