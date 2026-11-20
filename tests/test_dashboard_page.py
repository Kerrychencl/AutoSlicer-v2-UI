"""Unit tests for DashboardPage (Page 1) of AutoSlicer Desktop Workbench."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

from gui.config import ConfigManager
from gui.models import TaskItem, TaskStatus, VodItem, VodStatus, WatchDirConfig
from gui.pages.dashboard_page import DashboardPage
from gui.queue_manager import QueueManager


class TestDashboardPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.test_root = tempfile.mkdtemp()
        self.watch_dir = Path(self.test_root) / "recordings"
        self.streamer_dir = self.watch_dir / "秋葵"
        self.streamer_dir.mkdir(parents=True)
        self.annotations_dir = Path(self.test_root) / "data" / "annotations"
        self.annotations_dir.mkdir(parents=True)
        self.cache_file = Path(self.test_root) / ".autoslicer_gui_cache.json"

        # Mock config
        self.config_path = Path(self.test_root) / "config.json"
        self.cm = ConfigManager(config_path=self.config_path)
        self.cm.set_watch_dirs([WatchDirConfig(label="測試目錄", path=str(self.watch_dir), enabled=True)])
        self.cm.data["paths"]["annotations_dir"] = str(self.annotations_dir)
        self.cm.save()

        # Dummy best.pt
        self.runs_dir = Path(self.test_root) / "runs" / "秋葵"
        self.runs_dir.mkdir(parents=True)
        self.ckpt_file = self.runs_dir / "best.pt"
        self.ckpt_file.write_bytes(b"dummy_weights")
        self.cm.data["paths"]["runs_dir"] = str(Path(self.test_root) / "runs")
        self.cm.save()

        self.qm = QueueManager()
        self.qm.clear_pending()
        self.qm.pause_queue()  # Keep tasks queued without launching real subprocesses in unit tests

        # Sample VOD file
        self.vod_file = self.streamer_dir / "【秋葵】2026年08月16日163044 歌回.mp4"
        self.vod_file.write_bytes(b"\x00" * (1024 * 1024))

        self.page = DashboardPage(config_manager=self.cm, queue_manager=self.qm)

    def tearDown(self):
        if hasattr(self, "page") and self.page:
            self.page.stop_scan()
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_dashboard_initialization(self):
        """Verify page attributes and components initialize properly."""
        self.assertEqual(self.page.page_id, "dashboard")
        self.assertEqual(self.page.title, "錄播看板")
        self.assertIsNotNone(self.page.table)
        self.assertIsNotNone(self.page.batch_bar)
        self.assertIsNotNone(self.page.advanced_box)
        self.assertFalse(self.page.batch_bar.isVisible())

    def test_table_populated(self):
        """Verify VOD items are rendered into the table."""
        # Manually trigger render with sample item
        item = VodItem(
            file_path=self.vod_file,
            stem=self.vod_file.stem,
            streamer="秋葵",
            watch_dir=self.watch_dir,
            status=VodStatus.WAITING_LLC,
            size_bytes=1024 * 1024,
            mtime=1700000000.0,
        )
        self.page._all_vods = [item]
        self.page._apply_filters()

        self.assertEqual(self.page.table.rowCount(), 1)
        # Check action button text
        action_btn = self.page.table.cellWidget(0, 5)
        self.assertIsInstance(action_btn, QPushButton)
        self.assertEqual(action_btn.text(), "生成 .llc")

    def test_checkbox_selection_and_batch_bar(self):
        """Verify row checking updates selection and toggles batch bar."""
        item1 = VodItem(
            file_path=self.vod_file,
            stem=self.vod_file.stem,
            streamer="秋葵",
            watch_dir=self.watch_dir,
            status=VodStatus.WAITING_LLC,
        )
        self.page._all_vods = [item1]
        self.page._apply_filters()

        self.assertFalse(self.page.batch_bar.isVisible())

        # Check the row
        cb_container = self.page.table.cellWidget(0, 0)
        cb = cb_container.findChild(QCheckBox)
        self.assertIsNotNone(cb)
        cb.setChecked(True)

        self.assertFalse(self.page.batch_bar.isHidden())
        self.assertIn("1 場錄播", self.page.lbl_batch_count.text())

        # Deselect
        cb.setChecked(False)
        self.assertTrue(self.page.batch_bar.isHidden())

    def test_enqueue_generate_llc_action(self):
        """Verify clicking generate .llc builds and enqueues task into QueueManager."""
        item = VodItem(
            file_path=self.vod_file,
            stem=self.vod_file.stem,
            streamer="秋葵",
            watch_dir=self.watch_dir,
            status=VodStatus.WAITING_LLC,
        )
        initial_count = self.qm.pending_count()

        # Execute action
        self.page.action_generate_llc(item, cut_video=False)

        # Check queue
        tasks = self.qm.all_tasks()
        self.assertTrue(len(tasks) > initial_count or self.qm.current_task is not None)
        task = self.qm.current_task or tasks[0]
        self.assertIn("生成打軸 .llc", task.title)
        self.assertIn("infer.py", " ".join(task.command))
        self.assertIn(str(self.vod_file), " ".join(task.command))

    def test_batch_action_enqueuing(self):
        """Verify batch action enqueues tasks for all selected VODs."""
        vod_file2 = self.streamer_dir / "【秋葵】2026年08月17日163044 歌回2.mp4"
        vod_file2.write_bytes(b"\x00" * 1024)

        item1 = VodItem(file_path=self.vod_file, stem=self.vod_file.stem, streamer="秋葵", watch_dir=self.watch_dir)
        item2 = VodItem(file_path=vod_file2, stem=vod_file2.stem, streamer="秋葵", watch_dir=self.watch_dir)

        self.page._all_vods = [item1, item2]
        self.page._selected_paths = {self.vod_file, vod_file2}
        self.page._execute_batch_action("llc")

        # After execution, selection should be cleared
        self.assertEqual(len(self.page._selected_paths), 0)

    def test_advanced_parameters_customization(self):
        """Verify changing advanced spinbox values affects infer command."""
        self.page.spin_on.setValue(0.75)
        self.page.spin_off.setValue(0.35)

        item = VodItem(file_path=self.vod_file, stem=self.vod_file.stem, streamer="秋葵", watch_dir=self.watch_dir)
        self.page.action_generate_llc(item)

        tasks = self.qm.all_tasks()
        last_task = tasks[-1]
        cmd_str = " ".join(last_task.command)
        self.assertIn("--on-threshold 0.75", cmd_str)
        self.assertIn("--off-threshold 0.35", cmd_str)

        # Test reset
        self.page._reset_advanced_params()
        self.assertEqual(self.page.spin_on.value(), 0.6)
        self.assertEqual(self.page.spin_off.value(), 0.4)


if __name__ == "__main__":
    unittest.main()
