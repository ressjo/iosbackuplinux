"""Short background tasks (e.g. checking a NAS) so the UI never freezes."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.done.emit(result)


_alive: set[_Task] = set()


def run_async(fn, *args, on_done=None, on_error=None, **kwargs) -> None:
    """Run ``fn`` in the thread pool; callbacks run in the GUI thread."""
    task = _Task(fn, args, kwargs)
    task.setAutoDelete(False)
    _alive.add(task)

    def _finish(_=None):
        _alive.discard(task)

    queued = Qt.ConnectionType.QueuedConnection
    if on_done is not None:
        task.signals.done.connect(on_done, queued)
    if on_error is not None:
        task.signals.failed.connect(on_error, queued)
    task.signals.done.connect(_finish, queued)
    task.signals.failed.connect(_finish, queued)
    QThreadPool.globalInstance().start(task)
