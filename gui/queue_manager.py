"""Strict single-thread FIFO Task Queue for AutoSlicer Desktop Workbench.

Features:
- Single-thread execution to prevent RTX 5060 8GB VRAM contention and I/O thrashing
- PySide6 signal emissions for task lifecycle (started, progress, log, completed, failed, cancelled)
- Automatic advance to next task on failure without halting the entire batch
- Clean cancellation and pause/resume support
- Thread-safe singleton access via QueueManager.get_instance() or get_queue_manager()
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import TaskItem, TaskStatus
from .runner import ProcessRunner


class QueueManager(QObject):
    """Strict FIFO single-thread Task Queue manager."""

    task_added = Signal(TaskItem)
    task_started = Signal(TaskItem)
    task_progress = Signal(TaskItem, int)       # TaskItem, progress percentage (0..100 or -1)
    task_log = Signal(TaskItem, str)            # TaskItem, sanitized log line
    task_completed = Signal(TaskItem)
    task_failed = Signal(TaskItem, str)         # TaskItem, error message
    task_cancelled = Signal(TaskItem)
    queue_updated = Signal()                    # Emitted on any queue modification
    queue_cleared = Signal()

    _instance: QueueManager | None = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending_tasks: list[TaskItem] = []
        self._finished_tasks: list[TaskItem] = []
        self._current_task: TaskItem | None = None
        self._is_paused: bool = False

        self._runner = ProcessRunner(self)
        self._runner.output_received.connect(self._on_runner_output)
        self._runner.progress_changed.connect(self._on_runner_progress)
        self._runner.process_finished.connect(self._on_runner_finished)
        self._runner.process_error.connect(self._on_runner_error)
        self._runner.process_cancelled.connect(self._on_runner_cancelled)

    @classmethod
    def get_instance(cls) -> QueueManager:
        if cls._instance is None:
            cls._instance = QueueManager()
        return cls._instance

    @property
    def current_task(self) -> TaskItem | None:
        return self._current_task

    @property
    def is_running(self) -> bool:
        return self._current_task is not None and self._runner.is_running()

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def pending_count(self) -> int:
        return len(self._pending_tasks)

    def pending_tasks(self) -> list[TaskItem]:
        return list(self._pending_tasks)

    def finished_tasks(self) -> list[TaskItem]:
        return list(self._finished_tasks)

    def all_tasks(self) -> list[TaskItem]:
        res: list[TaskItem] = []
        if self._current_task:
            res.append(self._current_task)
        res.extend(self._pending_tasks)
        res.extend(self._finished_tasks)
        return res

    def get_task_by_id(self, task_id: str) -> TaskItem | None:
        if self._current_task and self._current_task.id == task_id:
            return self._current_task
        for t in self._pending_tasks:
            if t.id == task_id:
                return t
        for t in self._finished_tasks:
            if t.id == task_id:
                return t
        return None

    def add_task(self, task: TaskItem) -> str:
        """Enqueue a new task and trigger processing if idle."""
        task.status = TaskStatus.PENDING
        self._pending_tasks.append(task)
        self.task_added.emit(task)
        self.queue_updated.emit()
        self._process_next()
        return task.id

    def add_tasks(self, tasks: list[TaskItem]) -> list[str]:
        """Enqueue multiple tasks in order."""
        ids: list[str] = []
        for t in tasks:
            t.status = TaskStatus.PENDING
            self._pending_tasks.append(t)
            self.task_added.emit(t)
            ids.append(t.id)
        if tasks:
            self.queue_updated.emit()
            self._process_next()
        return ids

    def cancel_current_task(self) -> None:
        """Cancel currently active task."""
        if self._current_task and self._runner.is_running():
            self._current_task.status = TaskStatus.CANCELLED
            self._is_paused = True  # Pause queue after user cancel as specified in Section 6.4
            self._runner.cancel()

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task by ID."""
        if self._current_task and self._current_task.id == task_id:
            self.cancel_current_task()
            return

        # If it is pending, remove it and mark as cancelled
        for i, t in enumerate(self._pending_tasks):
            if t.id == task_id:
                t.status = TaskStatus.CANCELLED
                t.finished_at = time.time()
                self._pending_tasks.pop(i)
                self._finished_tasks.insert(0, t)
                self.task_cancelled.emit(t)
                self.queue_updated.emit()
                break

    def remove_pending_task(self, task_id: str) -> bool:
        """Remove a pending task from the queue."""
        for i, t in enumerate(self._pending_tasks):
            if t.id == task_id:
                self._pending_tasks.pop(i)
                self.queue_updated.emit()
                return True
        return False

    def clear_pending(self) -> None:
        """Clear all pending tasks in the queue."""
        self._pending_tasks.clear()
        self.queue_cleared.emit()
        self.queue_updated.emit()

    def clear_finished(self) -> None:
        """Clear history of finished tasks."""
        self._finished_tasks.clear()
        self.queue_updated.emit()

    def pause_queue(self) -> None:
        """Pause advancing to next tasks."""
        self._is_paused = True
        self.queue_updated.emit()

    def resume_queue(self) -> None:
        """Resume advancing to next tasks."""
        self._is_paused = False
        self.queue_updated.emit()
        self._process_next()

    def retry_task(self, task_id: str) -> bool:
        """Re-enqueue a failed or cancelled task."""
        target: TaskItem | None = None
        for i, t in enumerate(self._finished_tasks):
            if t.id == task_id:
                target = self._finished_tasks.pop(i)
                break

        if target is None:
            return False

        # Reset task state
        target.status = TaskStatus.PENDING
        target.progress = -1
        target.exit_code = None
        target.error_message = ""
        target.started_at = None
        target.finished_at = None
        target.log_lines = []

        self._pending_tasks.append(target)
        self.task_added.emit(target)
        self.queue_updated.emit()
        self._process_next()
        return True

    def _process_next(self) -> None:
        """Advance to next task in FIFO queue if idle and not paused."""
        if self._is_paused or self._current_task is not None:
            return

        if not self._pending_tasks:
            return

        self._current_task = self._pending_tasks.pop(0)
        self._current_task.status = TaskStatus.RUNNING
        self._current_task.started_at = time.time()
        self._current_task.progress = -1

        self.task_started.emit(self._current_task)
        self.queue_updated.emit()

        started = self._runner.start(
            command=self._current_task.command,
            cwd=self._current_task.cwd,
            env_extra=self._current_task.env_extra,
        )

        if not started:
            self._current_task.status = TaskStatus.FAILED
            self._current_task.finished_at = time.time()
            self._current_task.error_message = "無法啟動外部子行程"
            failed_task = self._current_task
            self._current_task = None
            self._finished_tasks.insert(0, failed_task)
            self.task_failed.emit(failed_task, failed_task.error_message)
            self.queue_updated.emit()
            self._process_next()

    # --- Runner Callbacks ---

    def _on_runner_output(self, line: str) -> None:
        if self._current_task:
            self._current_task.log_lines.append(line)
            self.task_log.emit(self._current_task, line)

    def _on_runner_progress(self, progress: int) -> None:
        if self._current_task:
            self._current_task.progress = progress
            self.task_progress.emit(self._current_task, progress)

    def _on_runner_finished(self, exit_code: int, exit_status: int) -> None:
        if not self._current_task:
            return

        task = self._current_task
        task.exit_code = exit_code
        task.finished_at = time.time()
        self._current_task = None

        if task.status == TaskStatus.CANCELLED or getattr(self._runner, "_is_cancelled", False):
            task.status = TaskStatus.CANCELLED
            self._finished_tasks.insert(0, task)
            self.task_cancelled.emit(task)
        elif exit_code == 0:
            task.status = TaskStatus.COMPLETED
            task.progress = 100
            self._finished_tasks.insert(0, task)
            self.task_completed.emit(task)
        else:
            task.status = TaskStatus.FAILED
            err = f"任務執行失敗，返回代碼 {exit_code}"
            task.error_message = err
            self._finished_tasks.insert(0, task)
            self.task_failed.emit(task, err)

        self.queue_updated.emit()

        # Automatic advance to next task even on failure
        self._process_next()

    def _on_runner_error(self, error_desc: str) -> None:
        if self._current_task:
            self._current_task.error_message = error_desc
            self._current_task.log_lines.append(f"[錯誤] {error_desc}")
            self.task_log.emit(self._current_task, f"[錯誤] {error_desc}")

    def _on_runner_cancelled(self) -> None:
        if self._current_task:
            self._current_task.status = TaskStatus.CANCELLED


def get_queue_manager() -> QueueManager:
    """Helper to access global QueueManager instance."""
    return QueueManager.get_instance()
