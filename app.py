"""
AutoSlicer Desktop Workbench - Application Entry Point.

Features:
- High DPI awareness configuration
- UTF-8 and unbuffered environment setup
- Modern centralized dark theme loading (gui/theme.qss)
- Configuration & Task Queue singleton initialization
- Page registry discovery & dynamic page mounting
- Main window initialization with state restoration
- Global exception hook for error trapping
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback

# Force UTF-8 and High DPI environment before Qt initialization
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from gui.config import ConfigManager, get_config
from gui.main_window import MainWindow
from gui.page_registry import PageRegistry, get_page_registry
from gui.queue_manager import QueueManager, get_queue_manager


def _setup_exception_hook():
    """Global exception hook to prevent silent crashes and log error traces."""
    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(f"[AutoSlicer Uncaught Exception]\n{err_msg}\n")
        sys.stderr.flush()

        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(
                None,
                "AutoSlicer 發生未預期錯誤",
                f"系統捕捉到未處理的異常：\n\n{exc_type.__name__}: {exc_value}\n\n詳細追蹤已輸出至主控台。",
            )

    sys.excepthook = excepthook


def _register_pages(registry: PageRegistry):
    """Discover and register workbench page classes."""
    # 1. 錄播看板 (Page 1)
    try:
        from gui.pages.dashboard_page import DashboardPage
        registry.register(
            page_class=DashboardPage,
            page_id="dashboard",
            title="錄播看板",
            icon_name="dashboard",
            order=10,
        )
    except Exception as e:
        sys.stderr.write(f"[Page Registry] Note: dashboard_page not yet loaded ({e})\n")

    # 2. B站下載 (Page 2)
    try:
        from gui.pages.download_page import DownloadPage
        registry.register(
            page_class=DownloadPage,
            page_id="download",
            title="B站下載",
            icon_name="download",
            order=20,
        )
    except Exception as e:
        sys.stderr.write(f"[Page Registry] Note: download_page not yet loaded ({e})\n")

    # 3. 快速工具 (Page 3)
    try:
        from gui.pages.tools_page import ToolsPage
        registry.register(
            page_class=ToolsPage,
            page_id="tools",
            title="快速工具",
            icon_name="tools",
            order=30,
        )
    except Exception as e:
        sys.stderr.write(f"[Page Registry] Note: tools_page not yet loaded ({e})\n")

    # 4. 全域設定 (Page 4)
    try:
        from gui.pages.settings_page import SettingsPage
        registry.register(
            page_class=SettingsPage,
            page_id="settings",
            title="全域設定",
            icon_name="settings",
            order=40,
        )
    except Exception as e:
        sys.stderr.write(f"[Page Registry] Note: settings_page not yet loaded ({e})\n")


def main() -> int:
    """Application main entry point."""
    _setup_exception_hook()

    # High DPI scaling attributes for standard PySide6 / Qt6
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("AutoSlicer Desktop Workbench")
    app.setApplicationDisplayName("AutoSlicer 桌面工作台")
    app.setOrganizationName("AutoSlicer")

    # Default font fallback
    font = QFont("Microsoft JhengHei UI", 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)

    # Load centralized QSS stylesheet
    theme_path = PROJECT_ROOT / "gui" / "theme.qss"
    if theme_path.exists():
        try:
            qss_content = theme_path.read_text(encoding="utf-8")
            app.setStyleSheet(qss_content)
        except Exception as e:
            sys.stderr.write(f"[Theme] Failed to load theme.qss: {e}\n")

    # Initialize Core Systems
    config_mgr = get_config()
    queue_mgr = get_queue_manager()
    page_reg = get_page_registry()

    # Discover and register pages
    _register_pages(page_reg)

    # Initialize Main Window
    main_window = MainWindow(
        config_manager=config_mgr,
        queue_manager=queue_mgr,
        page_registry=page_reg,
    )
    main_window.show()

    ret = app.exec()
    try:
        del main_window
    except Exception:
        pass
    return ret


if __name__ == "__main__":
    sys.exit(main())
