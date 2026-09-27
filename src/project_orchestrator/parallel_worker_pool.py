from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

PRODUCTS = ("knowledge-engine", "rocksmith", "everward", "voicestudio")


@dataclass(frozen=True)
class WorkerSlot:
    product: str
    index: int

    @property
    def worker_id(self) -> str:
        return f"{self.product}-worker-{self.index}"


@dataclass
class RunningWork(Generic[T]):
    slot: WorkerSlot
    future: Future[T]


class ParallelWorkerPool(Generic[T]):
    """Bounded product-aware worker pool.

    The pool owns scheduling capacity only. Repository leases, exact-head
    authority, preflight, CI, review, merge, and Product Reality remain with
    their existing authoritative components.
    """

    def __init__(
        self,
        *,
        max_workers_per_product: int = 2,
        max_total_workers: int = 6,
        executor_factory: Callable[..., ThreadPoolExecutor] = ThreadPoolExecutor,
    ) -> None:
        if max_workers_per_product < 1:
            raise ValueError("max_workers_per_product must be positive")
        if max_total_workers < 1:
            raise ValueError("max_total_workers must be positive")
        self.max_workers_per_product = max_workers_per_product
        self.max_total_workers = max_total_workers
        self._executor = executor_factory(
            max_workers=max_total_workers,
            thread_name_prefix="exponential-engine",
        )
        self._running: dict[str, RunningWork[T]] = {}

    def _running_for(self, product: str) -> int:
        return sum(1 for work in self._running.values() if work.slot.product == product)

    def available_slots(self, products: list[str]) -> list[WorkerSlot]:
        """Return fair slots: first worker across products before second workers."""
        allowed = [product for product in PRODUCTS if product in set(products)]
        remaining = self.max_total_workers - len(self._running)
        slots: list[WorkerSlot] = []
        if remaining <= 0:
            return slots
        for index in range(1, self.max_workers_per_product + 1):
            for product in allowed:
                if len(slots) >= remaining:
                    return slots
                if self._running_for(product) >= index:
                    continue
                slots.append(WorkerSlot(product, index))
        return slots

    def submit(self, slot: WorkerSlot, fn: Callable[..., T], /, *args, **kwargs) -> str:
        if slot.product not in PRODUCTS:
            raise ValueError("unknown product")
        if len(self._running) >= self.max_total_workers:
            raise RuntimeError("worker pool saturated")
        if self._running_for(slot.product) >= self.max_workers_per_product:
            raise RuntimeError("product worker limit reached")
        if slot.worker_id in self._running:
            raise RuntimeError("worker slot already active")
        future = self._executor.submit(fn, *args, **kwargs)
        self._running[slot.worker_id] = RunningWork(slot=slot, future=future)
        return slot.worker_id

    def harvest(self) -> dict[str, T | BaseException]:
        """Collect only completed workers; never block portfolio scheduling."""
        completed: dict[str, T | BaseException] = {}
        for worker_id, work in list(self._running.items()):
            if not work.future.done():
                continue
            try:
                completed[worker_id] = work.future.result()
            except BaseException as exc:  # surfaced to supervisor, never swallowed
                completed[worker_id] = exc
            del self._running[worker_id]
        return completed

    def snapshot(self) -> dict:
        by_product = {
            product: self._running_for(product)
            for product in PRODUCTS
        }
        return {
            "running_total": len(self._running),
            "running_by_product": by_product,
            "max_total_workers": self.max_total_workers,
            "max_workers_per_product": self.max_workers_per_product,
            "workers": [
                {
                    "worker_id": worker_id,
                    "product": work.slot.product,
                    "slot": work.slot.index,
                    "done": work.future.done(),
                }
                for worker_id, work in sorted(self._running.items())
            ],
        }

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
