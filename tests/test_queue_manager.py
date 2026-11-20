"""Unit tests for QueueManager single-thread FIFO execution, failure skip, and cancel."""
import sys
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from gui.models import TaskItem, TaskStatus
from gui.queue_manager import QueueManager


class TestQueueManager(unittest.TestCase):
    def setUp(self):
        self.qm = QueueManager()

    def tearDown(self):
        if self.qm.is_running:
            self.qm.cancel_current_task()
        self.qm.clear_pending()
        self.qm.clear_finished()

    def _wait_for(self, condition_func, timeout_s: float = 5.0) -> bool:
        """Process Qt events until condition_func() is True or timeout."""
        start = time.time()
        while time.time() - start < timeout_s:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if condition_func():
                return True
            time.sleep(0.02)
        return False

    def test_single_thread_fifo_and_advance_on_failure(self):
        """Verify 3 tasks execute sequentially, one fails, queue automatically advances."""
        # Task 1: quick success
        t1 = TaskItem(
            title="Task 1 Success",
            command=[sys.executable, "-c", "import sys; print('Task1 OK'); sys.exit(0)"],
        )
        # Task 2: deliberate failure (exit code 1)
        t2 = TaskItem(
            title="Task 2 Fail",
            command=[sys.executable, "-c", "import sys; print('Task2 Err'); sys.exit(1)"],
        )
        # Task 3: quick success
        t3 = TaskItem(
            title="Task 3 Success",
            command=[sys.executable, "-c", "import sys; print('Task3 OK'); sys.exit(0)"],
        )

        completed_ids = []
        failed_ids = []

        self.qm.task_completed.connect(lambda t: completed_ids.append(t.id))
        self.qm.task_failed.connect(lambda t, err: failed_ids.append(t.id))

        self.qm.add_tasks([t1, t2, t3])

        # Wait until all 3 are finished
        success = self._wait_for(lambda: len(self.qm.finished_tasks()) == 3, timeout_s=8.0)
        self.assertTrue(success, "Tasks did not complete in time")

        # Verify states
        self.assertEqual(t1.status, TaskStatus.COMPLETED)
        self.assertEqual(t2.status, TaskStatus.FAILED)
        self.assertEqual(t3.status, TaskStatus.COMPLETED)

        self.assertIn(t1.id, completed_ids)
        self.assertIn(t2.id, failed_ids)
        self.assertIn(t3.id, completed_ids)

    def test_cancel_current_task(self):
        """Verify cancelling current long-running task halts it properly."""
        long_task = TaskItem(
            title="Long Task",
            command=[sys.executable, "-c", "import time; time.sleep(10)"],
        )

        cancelled_events = []
        self.qm.task_cancelled.connect(lambda t: cancelled_events.append(t.id))

        self.qm.add_task(long_task)

        # Wait until task starts running
        self._wait_for(lambda: self.qm.is_running, timeout_s=3.0)
        self.assertTrue(self.qm.is_running)

        # Cancel it
        self.qm.cancel_current_task()

        # Wait until it is finished/cancelled
        self._wait_for(lambda: not self.qm.is_running, timeout_s=5.0)
        self.assertEqual(long_task.status, TaskStatus.CANCELLED)
        self.assertTrue(self.qm.is_paused)

    def test_failed_to_start_deadlock_prevention(self):
        """Verify task with invalid non-existent executable emits FailedToStart, marks FAILED, and queue advances."""
        t_bad = TaskItem(
            title="Non-existent executable",
            command=["this_program_does_not_exist_xyz_123.exe", "--arg"],
        )
        t_good = TaskItem(
            title="Normal follow-up",
            command=[sys.executable, "-c", "import sys; print('Good OK'); sys.exit(0)"],
        )

        failed_tasks = []
        completed_tasks = []
        self.qm.task_failed.connect(lambda t, err: failed_tasks.append(t.id))
        self.qm.task_completed.connect(lambda t: completed_tasks.append(t.id))

        self.qm.add_tasks([t_bad, t_good])

        # Wait until both tasks are processed
        success = self._wait_for(lambda: len(self.qm.finished_tasks()) == 2, timeout_s=8.0)
        self.assertTrue(success, "Queue deadlocked or timed out on FailedToStart")

        self.assertEqual(t_bad.status, TaskStatus.FAILED)
        self.assertEqual(t_good.status, TaskStatus.COMPLETED)
        self.assertIn(t_bad.id, failed_tasks)
        self.assertIn(t_good.id, completed_tasks)

    def test_queue_pause_resume_and_auto_unpause(self):
        """Verify pause/resume behavior and auto-unpause on new task when queue is idle."""
        self.qm.pause_queue()
        self.assertTrue(self.qm.is_paused)

        t_queued = TaskItem(
            title="Queued while paused",
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        # Adding task to idle queue automatically unpauses as per Section 6 / P0.5 fix
        self.qm.add_task(t_queued)
        self.assertFalse(self.qm.is_paused)

        success = self._wait_for(lambda: t_queued.status == TaskStatus.COMPLETED, timeout_s=5.0)
        self.assertTrue(success)

        # Explicit pause during execution
        self.qm.pause_queue()
        self.assertTrue(self.qm.is_paused)
        self.qm.resume_queue()
        self.assertFalse(self.qm.is_paused)


if __name__ == "__main__":
    unittest.main()
