"""Headless UI smoke tests for AutoSlicer Desktop Workbench.
Verifies all widgets, pages, theme stylesheets, and MainWindow instantiate without crash.
"""
import os
import sys
import unittest
from pathlib import Path

# Enforce offscreen QPA platform for headless testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

# Ensure QApplication singleton exists in offscreen mode
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from gui.config import ConfigManager, get_config
from gui.page_registry import PageRegistry, get_page_registry
from gui.queue_manager import QueueManager, get_queue_manager
from gui.widgets.collapsible import CollapsibleBox
from gui.widgets.drop_zone import DropZone
from gui.widgets.queue_drawer import QueueDrawer
from gui.main_window import MainWindow
from gui.pages.dashboard_page import DashboardPage
from gui.pages.download_page import DownloadPage
from gui.pages.tools_page import ToolsPage
from gui.pages.settings_page import SettingsPage
from app import _register_pages


class TestUISmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load theme stylesheet
        theme_path = PROJECT_ROOT / "gui" / "theme.qss"
        if theme_path.exists():
            qss = theme_path.read_text(encoding="utf-8")
            app.setStyleSheet(qss)

    def setUp(self):
        self.config_mgr = ConfigManager()
        self.queue_mgr = QueueManager()
        self.page_registry = PageRegistry()

    def test_widgets_instantiation(self):
        """Test standalone custom widgets instantiate without crash."""
        # 1. DropZone
        drop_zone = DropZone(title="測試拖曳區", hint="支援檔案拖曳")
        self.assertIsNotNone(drop_zone)

        # 2. CollapsibleBox
        collapsible = CollapsibleBox(title="進階參數")
        self.assertIsNotNone(collapsible)
        self.assertFalse(collapsible.is_expanded())
        collapsible.toggle()
        self.assertTrue(collapsible.is_expanded())

        # 3. QueueDrawer
        drawer = QueueDrawer(queue_manager=self.queue_mgr, expanded=False)
        self.assertIsNotNone(drawer)
        self.assertFalse(drawer.is_expanded())
        drawer.toggle_expanded()
        self.assertTrue(drawer.is_expanded())

    def test_pages_instantiation(self):
        """Test all 4 functional pages instantiate without crash."""
        # Page 1: DashboardPage
        p1 = DashboardPage(config_manager=self.config_mgr, queue_manager=self.queue_mgr)
        self.assertEqual(p1.page_id, "dashboard")
        self.assertIsNotNone(p1.table)
        if hasattr(p1, "stop_scan"):
            p1.stop_scan()
        elif hasattr(p1, "_scan_thread") and p1._scan_thread and p1._scan_thread.isRunning():
            p1._scan_thread.quit()
            p1._scan_thread.wait(2000)

        # Page 2: DownloadPage
        p2 = DownloadPage(config_manager=self.config_mgr, queue_manager=self.queue_mgr)
        self.assertEqual(p2.page_id, "download")
        self.assertIsNotNone(p2.url_text_edit)

        # Page 3: ToolsPage
        p3 = ToolsPage(config_manager=self.config_mgr, queue_manager=self.queue_mgr)
        self.assertEqual(p3.page_id, "tools")
        self.assertIsNotNone(p3.clip_drop_zone)
        self.assertIsNotNone(p3.llc_drop_zone)

        # Page 4: SettingsPage
        p4 = SettingsPage(config_manager=self.config_mgr, queue_manager=self.queue_mgr)
        self.assertEqual(p4.page_id, "settings")
        self.assertIsNotNone(p4.save_btn)

    def test_main_window_and_navigation(self):
        """Test MainWindow layout, page switching, and queue drawer integration."""
        _register_pages(self.page_registry)
        registered_pages = self.page_registry.get_pages()
        self.assertEqual(len(registered_pages), 4)

        win = MainWindow(
            config_manager=self.config_mgr,
            queue_manager=self.queue_mgr,
            page_registry=self.page_registry,
        )
        self.assertIsNotNone(win)
        self.assertEqual(win.windowTitle(), "AutoSlicer 桌面工作台")

        # Verify all 4 pages are mounted
        for page_id in ["dashboard", "download", "tools", "settings"]:
            self.assertIn(page_id, win._page_instances)
            win.switch_page(page_id)
            self.assertEqual(win._active_page_id, page_id)
            active_widget = win.stacked_widget.currentWidget()
            self.assertEqual(getattr(active_widget, "page_id", None), page_id)

        # Verify drawer state toggle
        initial_drawer_state = win.queue_drawer.is_expanded()
        win.queue_drawer.toggle_expanded()
        self.assertNotEqual(win.queue_drawer.is_expanded(), initial_drawer_state)

        # Clean close
        win.close()


if __name__ == "__main__":
    unittest.main()
