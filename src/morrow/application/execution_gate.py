"""Small root-conflict queue shared by Chat and Workflow supervisors."""

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(eq=False)
class Admission:
    root: Path
    confined: bool

    def conflicts(self, other):
        return (
            not self.confined
            or not other.confined
            or self.root.is_relative_to(other.root)
            or other.root.is_relative_to(self.root)
        )


class ExecutionCoordinator:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = []
        self.waiting = []

    def gate(self, root, *, confined=lambda: False):
        return WorkspaceExecutionGate(self, Path(root).resolve(), confined)


class WorkspaceExecutionGate:
    def __init__(self, coordinator, root, confined):
        self.coordinator = coordinator
        self.root = root
        self.confined = confined
        self.holders = {}

    def locked(self):
        admission = Admission(self.root, self.confined())
        return any(admission.conflicts(active) for active in self.coordinator.active)

    async def __aenter__(self):
        coordinator = self.coordinator
        admission = Admission(self.root, self.confined())
        async with coordinator.condition:
            coordinator.waiting.append(admission)
            try:
                while True:
                    # Recheck confinement after waiting; new MCP desired state
                    # can only make the admission more conservative.
                    admission.confined = admission.confined and self.confined()
                    earlier = coordinator.waiting[: coordinator.waiting.index(admission)]
                    if not any(admission.conflicts(a) for a in coordinator.active + earlier):
                        coordinator.waiting.remove(admission)
                        coordinator.active.append(admission)
                        self.holders[asyncio.current_task()] = admission
                        return self
                    await coordinator.condition.wait()
            except BaseException:
                coordinator.waiting.remove(admission)
                coordinator.condition.notify_all()
                raise

    async def __aexit__(self, *_):
        async with self.coordinator.condition:
            self.coordinator.active.remove(self.holders.pop(asyncio.current_task()))
            self.coordinator.condition.notify_all()
