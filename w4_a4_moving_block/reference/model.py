"""Independent cycle model for the pinned A4 moving-block RTL contract."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    ready_mask: int
    retire_valid: bool
    retire_source: int
    retire_token: int


class MovingBlockReference:
    def __init__(self, max_advance: int, sources: int = 16):
        if max_advance not in (1, 2):
            raise ValueError("MAX_ADVANCE must be 1 or 2")
        self.sources = sources
        self.max_advance = max_advance
        self.first_leaf = sources - 1
        self.nodes = [0] * (2 * sources - 1)
        self.phases = [0] * (sources - 1)

    def occupancy(self) -> int:
        return sum(token != 0 for token in self.nodes)

    def step(self, pending: list[int], rst_n: bool = True) -> Result:
        if not rst_n:
            self.nodes = [0] * len(self.nodes)
            self.phases = [0] * len(self.phases)
            return Result(0, False, 0, 0)

        work = self.nodes.copy()
        phases = self.phases.copy()
        root = work[0]
        result = Result(0, root != 0, ((root & 0xffffffff) - 1) >> 24 if root else 0, root)
        if root:
            work[0] = 0

        accepted = 0
        for _ in range(self.max_advance):
            for source, token in enumerate(pending):
                leaf = self.first_leaf + source
                if token and not ((accepted >> source) & 1) and work[leaf] == 0:
                    work[leaf] = token
                    accepted |= 1 << source
            for parent in range(self.first_leaf):
                if work[parent]:
                    continue
                left = 2 * parent + 1
                right = left + 1
                if not work[left] and not work[right]:
                    continue
                if work[left] and work[right]:
                    child = right if phases[parent] else left
                else:
                    child = left if work[left] else right
                work[parent] = work[child]
                work[child] = 0
                phases[parent] = 1 if child == left else 0

        self.nodes = work
        self.phases = phases
        return Result(accepted, result.retire_valid, result.retire_source, result.retire_token)
