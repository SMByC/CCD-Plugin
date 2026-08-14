import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CancellableTask(Protocol):
    def cancel(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingPlotLoad:
    generation: int
    path: Path


class PlotLoadController:
    def __init__(self):
        self._generation = 0
        self.pending: PendingPlotLoad | None = None

    def begin(self, path: Path) -> PendingPlotLoad:
        self._generation += 1
        pending = PendingPlotLoad(self._generation, path)
        self.pending = pending
        return pending

    def resolve(self, generation: int, path: Path, *, succeeded: bool) -> bool | None:
        pending = self.pending
        if pending is None or pending.generation != generation or pending.path != path:
            return None
        self.pending = None
        return succeeded

    def cancel(self) -> None:
        self.pending = None


class PlotFileLifecycle:
    """Own the single self-contained plot file exposed to viewers."""

    def __init__(self, directory: str | Path):
        self._directory = Path(directory)
        self.active_path: Path | None = None
        self.pending_path: Path | None = None

    @property
    def browser_path(self) -> Path | None:
        return self.active_path

    def prepare(self, write: Callable[[Path], None]) -> Path:
        previous_pending = self.pending_path
        self.pending_path = None
        if previous_pending is not None:
            previous_pending.unlink(missing_ok=True)

        descriptor, raw_path = tempfile.mkstemp(suffix=".html", dir=self._directory)
        os.close(descriptor)
        replacement = Path(raw_path)
        try:
            write(replacement)
        except BaseException:
            replacement.unlink(missing_ok=True)
            raise

        self.pending_path = replacement
        return replacement

    def commit(self, pending: Path) -> Path:
        if pending != self.pending_path:
            return self.active_path if self.active_path is not None else pending

        previous = self.active_path
        self.active_path = pending
        self.pending_path = None
        if previous is not None:
            previous.unlink(missing_ok=True)
        return pending

    def rollback(self, pending: Path) -> Path | None:
        if pending == self.pending_path:
            self.pending_path = None
            pending.unlink(missing_ok=True)
        return self.active_path

    def clear(self) -> None:
        active = self.active_path
        pending = self.pending_path
        self.active_path = None
        self.pending_path = None
        if active is not None:
            active.unlink(missing_ok=True)
        if pending is not None:
            pending.unlink(missing_ok=True)


class TaskLifecycle:
    """Track task ownership and reject completions after disposal."""

    def __init__(self):
        self.active_task: CancellableTask | None = None
        self._disposed = False

    def start(self, task: CancellableTask) -> None:
        previous = self.active_task
        if previous is not None:
            previous.cancel()
        self.active_task = task

    def finish(self, task: CancellableTask) -> bool:
        if self._disposed or task is not self.active_task:
            return False
        self.active_task = None
        return True

    def dispose(self) -> None:
        self._disposed = True
        task = self.active_task
        self.active_task = None
        if task is not None:
            task.cancel()
