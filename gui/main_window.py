"""
Main window layout and navigation shell for AutoSlicer Desktop Workbench.

Features:
- Fixed 168px left navigation sidebar for [錄播看板, B站下載, 快速工具, 全域設定]
- QStackedWidget central work area dynamically loading pages from PageRegistry
- Bottom collapsible QSplitter queue drawer with real-time sync
- Top-level drag & drop routing to active or relevant pages
- Window geometry and drawer state persistence to config.json
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import ConfigManager, get_config
from .page_registry import PageInfo, PageRegistry, get_page_registry
from .queue_manager import QueueManager, get_queue_manager
from .widgets.queue_drawer import QueueDrawer


class MainWindow(QMainWindow):
    """AutoSlicer Desktop Workbench main window."""

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        queue_manager: QueueManager | None = None,
        page_registry: PageRegistry | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config_manager or get_config()
        self.queue_manager = queue_manager or get_queue_manager()
        self.page_registry = page_registry or get_page_registry()

        self._page_instances: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._active_page_id: str | None = None

        self.setWindowTitle("AutoSlicer 桌面工作台")
        self.setAcceptDrops(True)

        self._restore_window_state()
        self._init_ui()
        self._load_registered_pages()

    def _restore_window_state(self):
        ui_cfg = self.config.get("ui", {})
        width = int(ui_cfg.get("window_width", 1200))
        height = int(ui_cfg.get("window_height", 780))
        self.resize(max(960, width), max(640, height))

    def _init_ui(self):
        # Central widget contains horizontal layout: Sidebar + Main Content Area
        central_container = QWidget(self)
        self.setCentralWidget(central_container)

        root_layout = QHBoxLayout(central_container)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Left Navigation Sidebar (168px fixed width)
        self.sidebar = QFrame(central_container)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(168)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 12)
        sidebar_layout.setSpacing(2)

        # App branding header
        self.sidebar_title = QLabel("AutoSlicer", self.sidebar)
        self.sidebar_title.setObjectName("sidebarTitle")
        self.sidebar_subtitle = QLabel("Desktop Workbench", self.sidebar)
        self.sidebar_subtitle.setObjectName("sidebarSubtitle")

        sidebar_layout.addWidget(self.sidebar_title)
        sidebar_layout.addWidget(self.sidebar_subtitle)

        # Navigation buttons container
        self.nav_button_group = QButtonGroup(self)
        self.nav_button_group.setExclusive(True)
        self.nav_button_container = QVBoxLayout()
        self.nav_button_container.setContentsMargins(0, 8, 0, 0)
        self.nav_button_container.setSpacing(4)
        sidebar_layout.addLayout(self.nav_button_container)

        sidebar_layout.addStretch(1)

        # Developer / QC placeholder slot
        self.qc_placeholder_btn = QPushButton("品管審片 (開發中)", self.sidebar)
        self.qc_placeholder_btn.setObjectName("sidebarQcPlaceholder")
        self.qc_placeholder_btn.setProperty("class", "nav-btn")
        self.qc_placeholder_btn.setEnabled(False)
        self.qc_placeholder_btn.setToolTip("客觀品管與盲測模組將於下階段開放掛載")
        sidebar_layout.addWidget(self.qc_placeholder_btn)

        root_layout.addWidget(self.sidebar)

        # 2. Right Work Area: Splitter containing Stacked Pages + Bottom Queue Drawer
        self.content_splitter = QSplitter(Qt.Vertical, central_container)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setObjectName("mainSplitter")

        # Central Stacked Widget
        self.stacked_widget = QStackedWidget(self.content_splitter)
        self.stacked_widget.setObjectName("centralWorkArea")

        # Bottom Queue Drawer
        initial_expanded = bool(self.config.get("ui.log_drawer_expanded", False))
        self.queue_drawer = QueueDrawer(
            queue_manager=self.queue_manager,
            expanded=initial_expanded,
            parent=self.content_splitter,
        )
        self.queue_drawer.expanded_changed.connect(self._on_drawer_expanded_changed)

        self.content_splitter.addWidget(self.stacked_widget)
        self.content_splitter.addWidget(self.queue_drawer)

        # Initial splitter sizes
        total_h = self.height()
        drawer_h = 240 if initial_expanded else 42
        self.content_splitter.setSizes([max(400, total_h - drawer_h), drawer_h])

        root_layout.addWidget(self.content_splitter, stretch=1)

    def _load_registered_pages(self):
        """Instantiate and mount registered pages in order."""
        page_infos = self.page_registry.get_pages(include_disabled=False)

        # Fallback if pages haven't been dynamically registered yet
        if not page_infos:
            self._register_default_page_fallbacks()
            page_infos = self.page_registry.get_pages(include_disabled=False)

        first_page_id = None
        for info in page_infos:
            page_id = info.page_id
            title = info.title
            icon_char = self._get_icon_for_page(page_id, info.icon_name)

            # Sidebar nav button
            btn = QPushButton(f"{icon_char}  {title}", self.sidebar)
            btn.setCheckable(True)
            btn.setProperty("class", "nav-btn")
            btn.setObjectName(f"navBtn_{page_id}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, pid=page_id: self.switch_page(pid))

            self.nav_button_group.addButton(btn)
            self.nav_button_container.addWidget(btn)
            self._nav_buttons[page_id] = btn

            # Instantiate Page
            try:
                page_instance = info.page_class(
                    config_manager=self.config,
                    queue_manager=self.queue_manager,
                    parent=self.stacked_widget,
                )
            except Exception as e:
                # Show safe placeholder if page initialization encounters missing optional deps
                page_instance = self._create_error_placeholder(page_id, title, str(e))

            self.stacked_widget.addWidget(page_instance)
            self._page_instances[page_id] = page_instance

            if first_page_id is None:
                first_page_id = page_id

        # Switch to first page
        if first_page_id:
            self.switch_page(first_page_id)

    def _register_default_page_fallbacks(self):
        """Attempt to discover and register default workbench pages if registry is empty."""
        try:
            from .pages.dashboard_page import DashboardPage
            self.page_registry.register(DashboardPage, page_id="dashboard", title="錄播看板", order=10)
        except ImportError:
            pass

        try:
            from .pages.download_page import DownloadPage
            self.page_registry.register(DownloadPage, page_id="download", title="B站下載", order=20)
        except ImportError:
            pass

        try:
            from .pages.tools_page import ToolsPage
            self.page_registry.register(ToolsPage, page_id="tools", title="快速工具", order=30)
        except ImportError:
            pass

        try:
            from .pages.settings_page import SettingsPage
            self.page_registry.register(SettingsPage, page_id="settings", title="全域設定", order=40)
        except ImportError:
            pass

    def _get_icon_for_page(self, page_id: str, icon_name: str) -> str:
        icon_map = {
            "dashboard": "🎬",
            "download": "📥",
            "tools": "⚡",
            "settings": "⚙️",
        }
        return icon_map.get(page_id, "📄")

    def _create_error_placeholder(self, page_id: str, title: str, error_msg: str) -> QWidget:
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        layout.setAlignment(Qt.AlignCenter)

        card = QFrame(placeholder)
        card.setObjectName("card")
        card.setStyleSheet("padding: 24px; border: 1px solid #333842; border-radius: 8px;")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        icon_label = QLabel("⚠️", card)
        icon_label.setStyleSheet("font-size: 32px;")
        icon_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(f"頁面載入異常: {title}", card)
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #f87171;")
        title_label.setAlignment(Qt.AlignCenter)

        msg_label = QLabel(error_msg, card)
        msg_label.setStyleSheet("color: #9ca3af; font-family: Consolas;")
        msg_label.setAlignment(Qt.AlignCenter)
        msg_label.setWordWrap(True)

        card_layout.addWidget(icon_label)
        card_layout.addWidget(title_label)
        card_layout.addWidget(msg_label)

        layout.addWidget(card)
        return placeholder

    @Slot(str)
    def switch_page(self, page_id: str):
        """Switch central view to the given page ID."""
        if page_id not in self._page_instances:
            return

        # Deactivate old page
        if self._active_page_id and self._active_page_id in self._page_instances:
            old_widget = self._page_instances[self._active_page_id]
            if hasattr(old_widget, "on_page_deactivated"):
                old_widget.on_page_deactivated()

        # Update active nav button
        for pid, btn in self._nav_buttons.items():
            is_active = pid == page_id
            btn.setChecked(is_active)
            btn.setProperty("active", "true" if is_active else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Switch widget
        new_widget = self._page_instances[page_id]
        self.stacked_widget.setCurrentWidget(new_widget)
        self._active_page_id = page_id

        # Activate new page
        if hasattr(new_widget, "on_page_activated"):
            new_widget.on_page_activated()

    def get_active_page_id(self) -> str | None:
        return self._active_page_id

    def get_page(self, page_id: str) -> QWidget | None:
        return self._page_instances.get(page_id)

    @Slot(bool)
    def _on_drawer_expanded_changed(self, expanded: bool):
        total_h = self.height()
        drawer_h = 240 if expanded else 42
        self.content_splitter.setSizes([max(300, total_h - drawer_h), drawer_h])

    # === Top-Level Drag & Drop Routing ===

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return

        paths: list[str] = []
        for url in urls:
            local = url.toLocalFile()
            if local:
                paths.append(os.path.normpath(local))

        if not paths:
            event.ignore()
            return

        event.acceptProposedAction()
        self._route_dropped_files(paths)

    def _route_dropped_files(self, paths: list[str]):
        """Intelligently route dropped files to current or target page."""
        current_page = self._page_instances.get(self._active_page_id or "")

        # 1. If current page directly handles dropped files
        if current_page and hasattr(current_page, "handle_dropped_files"):
            current_page.handle_dropped_files(paths)
            return

        # 2. Check file types to auto-switch page
        has_llc = any(p.lower().endswith(".llc") for p in paths)
        has_video = any(
            p.lower().endswith((".mp4", ".flv", ".mkv", ".ts", ".m4a")) for p in paths
        )

        if has_llc and "tools" in self._page_instances:
            self.switch_page("tools")
            tools_page = self._page_instances["tools"]
            if hasattr(tools_page, "handle_dropped_files"):
                tools_page.handle_dropped_files(paths)
        elif has_video and "dashboard" in self._page_instances:
            self.switch_page("dashboard")
            dash_page = self._page_instances["dashboard"]
            if hasattr(dash_page, "handle_dropped_files"):
                dash_page.handle_dropped_files(paths)

    # === Window Close & Persistence ===

    def closeEvent(self, event):
        """Save UI layout state to config.json before exit."""
        try:
            self.config.set("ui.window_width", self.width())
            self.config.set("ui.window_height", self.height())
            self.config.set("ui.log_drawer_expanded", self.queue_drawer.is_expanded())
            self.config.save()
        except Exception:
            pass

        # Cleanup child pages (e.g. background scan threads)
        for page in self._page_instances.values():
            if hasattr(page, "cleanup"):
                try:
                    page.cleanup()
                except Exception:
                    pass
            scan_thread = getattr(page, "_scan_thread", None)
            if scan_thread is not None and hasattr(scan_thread, "isRunning") and scan_thread.isRunning():
                try:
                    scan_thread.quit()
                    scan_thread.wait(800)
                except Exception:
                    pass

        # Cleanup runner subprocesses if still running
        if self.queue_manager and hasattr(self.queue_manager, "cancel_current_task"):
            if self.queue_manager.is_running:
                self.queue_manager.cancel_current_task()

        event.accept()
