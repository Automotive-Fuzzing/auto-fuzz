import heapq
import itertools
from typing import List, Optional, Union

from .seed_manager import Seed, SeedManager


class SeedQueue:
    def __init__(self, db_path: str = "seeds.db"):
        self.manager = SeedManager(db_path)
        self._counter = itertools.count()
        self.queue: List[tuple[int, int, int]] = []
        self.load_from_db()

    def load_from_db(self):
        self.queue.clear()
        self._counter = itertools.count()

        for seed in self.manager.get_all():
            if seed.id is None:
                continue
            if seed.status != "queued":
                continue
            tiebreaker = next(self._counter)
            heapq.heappush(self.queue, (-seed.priority, tiebreaker, seed.id))
            
    def push(self, seed_or_id: Union[Seed, int]):
        if isinstance(seed_or_id, Seed):
            seed_id = self.manager.add_seed(seed_or_id)
            seed_or_id.id = seed_id
            priority = seed_or_id.priority
        else:
            seed_id = int(seed_or_id)
            seed = self.manager.get_seed(seed_id)
            if seed is None:
                return
            priority = seed.priority

        tiebreaker = next(self._counter)
        heapq.heappush(self.queue, (-priority, tiebreaker, seed_id))

    def push_group(self, seeds: List[Seed]):
        for seed in seeds:
            self.push(seed)

    def pop(self) -> Optional[int]:
        if not self.queue:
            return None

        _, _, seed_id = heapq.heappop(self.queue)
        return seed_id

    def update_priority(self, seed_id: int, delta: int):
        updated = False

        for i, (priority, tiebreaker, queued_seed_id) in enumerate(self.queue):
            if queued_seed_id == seed_id:
                seed = self.manager.get_seed(seed_id)
                if seed is None:
                    return

                new_priority = max(1, min(seed.priority + delta, 10))
                self.manager.update_priority(seed_id, new_priority)
                self.queue[i] = (-new_priority, tiebreaker, seed_id)
                updated = True
                break

        if updated:
            heapq.heapify(self.queue)

    def list(self) -> List[Seed]:
        sorted_queue = sorted(self.queue, key=lambda x: (x[0], x[1]))
        out: List[Seed] = []

        for _, _, seed_id in sorted_queue:
            seed = self.manager.get_seed(seed_id)
            if seed is not None:
                out.append(seed)

        return out

    def close(self):
        self.manager.close()