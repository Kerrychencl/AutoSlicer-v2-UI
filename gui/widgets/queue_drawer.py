"""
Bottom collapsible task drawer:
- Status bar with current task, progress bar, queue count, cancel button, expand toggle
- Expanded view has task list on left and UTF-8 real-time autoscrolling log console on right
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..models import TaskItem, TaskStatus
    from ..queue_manager import QueueManager


class QueueDrawer(QFrame):
    """
    Bottom collapsible task queue and log viewer drawer.
    """
    expanded_changed = Signal(bool)

    def __init__(self, queue_manager: QueueManager | None = None, expanded: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("queueDrawer")
        self._is_expanded = expanded
        self._queue_manager: QueueManager | None = None
        self._active_task_id: str | None = None

        self._init_ui()
        if queue_manager:
            self.set_queue_manager(queue_manager)
        self.set_expanded(expanded)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Status Bar (Always visible)
        self.status_bar = QFrame(self)
        self.status_bar.setObjectName("queueStatusBar")
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(12, 6, 12, 6)
        status_layout.setSpacing(12)

        # Status text
        self.status_label = QLabel("佇列閒置", self.status_bar)
        self.status_label.setStyleSheet("font-weight: 600; color: #9ca3af; font-size: 13px;")

        # Progress bar
        self.progress_bar = QProgressBar(self.status_bar)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumWidth(160)
        self.progress_bar.setMaximumWidth(280)
        self.progress_bar.setVisible(False)

        # Queue count badge
        self.queue_count_label = QLabel("排隊中: 0 筆", self.status_bar)
        self.queue_count_label.setStyleSheet("color: #6b7280; font-size: 12px;")

        # Cancel current task button
        self.cancel_btn = QPushButton("中止目前任務", self.status_bar)
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.setProperty("class", "danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip("中止目前正在執行的子行程（5秒內強行終止進程樹）")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)

        # Resume queue button (shown when paused with pending items)
        self.btn_resume = QPushButton("▶ 繼續佇列", self.status_bar)
        self.btn_resume.setObjectName("primaryButton")
        self.btn_resume.setProperty("class", "primary")
        self.btn_resume.setVisible(False)
        self.btn_resume.clicked.connect(self._on_resume_clicked)

        # Clear pending tasks button (shown when paused or multiple pending)
        self.btn_clear_pending = QPushButton("清空待執行", self.status_bar)
        self.btn_clear_pending.setProperty("class", "compact")
        self.btn_clear_pending.setVisible(False)
        self.btn_clear_pending.clicked.connect(self._on_clear_pending_clicked)

        # Expand / Collapse toggle button
        self.toggle_btn = QPushButton("詳細佇列與日誌 ▲", self.status_bar)
        self.toggle_btn.setObjectName("toggleDrawerBtn")
        self.toggle_btn.clicked.connect(self.toggle_expanded)

        status_layout.addWidget(self.status_label, stretch=1)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.queue_count_label)
        status_layout.addWidget(self.btn_resume)
        status_layout.addWidget(self.btn_clear_pending)
        status_layout.addWidget(self.cancel_btn)
        status_layout.addWidget(self.toggle_btn)

        root_layout.addWidget(self.status_bar)

        # 2. Expanded Container (Task List on left, Log Console on right)
        self.expanded_container = QWidget(self)
        expanded_layout = QVBoxLayout(self.expanded_container)
        expanded_layout.setContentsMargins(8, 8, 8, 8)
        expanded_layout.setSpacing(6)

        # Horizontal Splitter
        self.splitter = QSplitter(Qt.Horizontal, self.expanded_container)
        self.splitter.setChildrenCollapsible(False)

        # Left: Task Queue List Panel
        task_panel = QFrame(self.splitter)
        task_panel_layout = QVBoxLayout(task_panel)
        task_panel_layout.setContentsMargins(4, 4, 4, 4)
        task_panel_layout.setSpacing(6)

        task_header_layout = QHBoxLayout()
        task_title = QLabel("任務佇列", task_panel)
        task_title.setStyleSheet("font-weight: 600; color: #d1d5db; font-size: 12px;")

        self.clear_finished_btn = QPushButton("清除已完成", task_panel)
        self.clear_finished_btn.setProperty("class", "compact")
        self.clear_finished_btn.clicked.connect(self._on_clear_finished_clicked)

        self.retry_failed_btn = QPushButton("重試失敗", task_panel)
        self.retry_failed_btn.setProperty("class", "compact")
        self.retry_failed_btn.clicked.connect(self._on_retry_failed_clicked)

        task_header_layout.addWidget(task_title)
        task_header_layout.addStretch(1)
        task_header_layout.addWidget(self.clear_finished_btn)
        task_header_layout.addWidget(self.retry_failed_btn)
        task_panel_layout.addLayout(task_header_layout)

        self.task_list = QListWidget(task_panel)
        self.task_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.task_list.customContextMenuRequested.connect(self._on_task_context_menu)
        task_panel_layout.addWidget(self.task_list)

        # Right: Log Console Panel
        log_panel = QFrame(self.splitter)
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(4, 4, 4, 4)
        log_panel_layout.setSpacing(6)

        log_header_layout = QHBoxLayout()
        log_title = QLabel("執行日誌 (即時 UTF-8)", log_panel)
        log_title.setStyleSheet("font-weight: 600; color: #d1d5db; font-size: 12px;")

        self.autoscroll_checkbox = QCheckBox("自動捲動", log_panel)
        self.autoscroll_checkbox.setChecked(True)

        self.copy_log_btn = QPushButton("複製全部", log_panel)
        self.copy_log_btn.setProperty("class", "compact")
        self.copy_log_btn.clicked.connect(self._on_copy_logs_clicked)

        self.clear_log_btn = QPushButton("清空畫面", log_panel)
        self.clear_log_btn.setProperty("class", "compact")
        self.clear_log_btn.clicked.connect(self._on_clear_logs_clicked)

        log_header_layout.addWidget(log_title)
        log_header_layout.addStretch(1)
        log_header_layout.addWidget(self.autoscroll_checkbox)
        log_header_layout.addWidget(self.copy_log_btn)
        log_header_layout.addWidget(self.clear_log_btn)
        log_panel_layout.addLayout(log_header_layout)

        self.log_console = QPlainTextEdit(log_panel)
        self.log_console.setObjectName("logConsole")
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumBlockCount(8000)
        log_panel_layout.addWidget(self.log_console)

        # Set splitter proportions (30% tasks, 70% logs)
        self.splitter.addWidget(task_panel)
        self.splitter.addWidget(log_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 7)

        expanded_layout.addWidget(self.splitter)
        root_layout.addWidget(self.expanded_container)

    def set_queue_manager(self, qm: QueueManager):
        """Connect to queue manager signals."""
        self._queue_manager = qm

        if hasattr(qm, "task_started"):
            qm.task_started.connect(self.on_task_started)
        if hasattr(qm, "task_progress"):
            qm.task_progress.connect(self.on_task_progress)
        if hasattr(qm, "task_log"):
            qm.task_log.connect(self._on_task_log)
        if hasattr(qm, "log_output"):
            qm.log_output.connect(self.append_log)
        if hasattr(qm, "task_completed"):
            qm.task_completed.connect(self.on_task_completed)
        if hasattr(qm, "task_failed"):
            qm.task_failed.connect(self.on_task_failed)
        if hasattr(qm, "task_cancelled"):
            qm.task_cancelled.connect(self.on_task_cancelled)
        if hasattr(qm, "queue_updated"):
            qm.queue_updated.connect(self.refresh_tasks)
        if hasattr(qm, "queue_cleared"):
            qm.queue_cleared.connect(self.refresh_tasks)

        self.refresh_tasks()

    @Slot(object, str)
    def _on_task_log(self, task, line: str):
        self.append_log(line)

    def set_expanded(self, expanded: bool):
        self._is_expanded = expanded
        self.expanded_container.setVisible(expanded)
        self.toggle_btn.setText("詳細佇列與日誌 ▼" if expanded else "詳細佇列與日誌 ▲")
        if expanded:
            self.expanded_container.setMinimumHeight(180)
        else:
            self.expanded_container.setMinimumHeight(0)
        self.expanded_changed.emit(expanded)

    def is_expanded(self) -> bool:
        return self._is_expanded

    def toggle_expanded(self):
        self.set_expanded(not self._is_expanded)

    # === Signal Handlers & Log Appending ===

    @Slot(object)
    def on_task_started(self, task):
        title = getattr(task, "title", str(task))
        self._active_task_id = getattr(task, "id", None)
        self.status_label.setText(f"目前執行: {title}")
        self.status_label.setStyleSheet("font-weight: 600; color: #60a5fa; font-size: 13px;")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until percentage reported
        self.cancel_btn.setEnabled(True)
        self.append_log(f"\n=== [開始執行] {title} ===\n")
        self.refresh_tasks()

    @Slot(object, int)
    def on_task_progress(self, task, progress: int):
        if progress < 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, max(0, progress)))

    @Slot(object)
    def on_task_completed(self, task):
        title = getattr(task, "title", str(task))
        self.status_label.setText(f"已完成: {title}")
        self.status_label.setStyleSheet("font-weight: 600; color: #4ade80; font-size: 13px;")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.cancel_btn.setEnabled(False)
        self.append_log(f"=== [完成] {title} ===\n")
        self.refresh_tasks()

    @Slot(object, str)
    def on_task_failed(self, task, error_msg: str):
        title = getattr(task, "title", str(task))
        self.status_label.setText(f"失敗: {title}")
        self.status_label.setStyleSheet("font-weight: 600; color: #f87171; font-size: 13px;")
        self.progress_bar.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.append_log(f"=== [失敗] {title}: {error_msg} ===\n")
        self.refresh_tasks()

    @Slot(object)
    def on_task_cancelled(self, task):
        title = getattr(task, "title", str(task))
        self.status_label.setText(f"已中止: {title}")
        self.status_label.setStyleSheet("font-weight: 600; color: #fb923c; font-size: 13px;")
        self.progress_bar.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self.append_log(f"=== [已中止] {title} ===\n")
        self.refresh_tasks()

    @Slot(str)
    def append_log(self, text: str):
        """Append UTF-8 text to the log console with auto-scrolling."""
        if not text:
            return
        self.log_console.appendPlainText(text)
        if self.autoscroll_checkbox.isChecked():
            cursor = self.log_console.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_console.setTextCursor(cursor)
            self.log_console.ensureCursorVisible()

    @Slot()
    def refresh_tasks(self):
        """Refresh the task list from queue manager."""
        if not self._queue_manager:
            return

        if hasattr(self._queue_manager, "all_tasks"):
            tasks = self._queue_manager.all_tasks()
        else:
            tasks = getattr(self._queue_manager, "tasks", [])
            if callable(tasks):
                tasks = tasks()

        self.task_list.clear()

        pending_count = 0
        is_running = False

        for task in tasks:
            status = getattr(task, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)
            title = getattr(task, "title", "未命名任務")
            task_id = getattr(task, "id", "")

            if status_val == "running":
                is_running = True
                icon = "🔄"
                status_text = "執行中"
            elif status_val == "completed":
                icon = "✅"
                status_text = "已完成"
            elif status_val == "failed":
                icon = "❌"
                status_text = "失敗"
            elif status_val == "cancelled":
                icon = "⏹"
                status_text = "已中止"
            else:
                icon = "⏳"
                status_text = "排隊中"
                pending_count += 1

            duration = getattr(task, "duration_formatted", "")
            dur_str = f" ({duration})" if duration else ""
            item_text = f"{icon} [{status_text}] {title}{dur_str}"

            item = QListWidgetItem(item_text, self.task_list)
            item.setData(Qt.UserRole, task_id)

            if status_val == "failed":
                item.setForeground(QColor("#f87171"))
            elif status_val == "completed":
                item.setForeground(QColor("#4ade80"))
            elif status_val == "running":
                item.setForeground(QColor("#60a5fa"))
            elif status_val == "cancelled":
                item.setForeground(QColor("#fb923c"))
            else:
                item.setForeground(QColor("#9ca3af"))

        self.queue_count_label.setText(f"排隊中: {pending_count} 筆")
        self.cancel_btn.setEnabled(is_running)

        is_paused = getattr(self._queue_manager, "is_paused", False)
        if is_paused and pending_count > 0:
            self.btn_resume.setVisible(True)
            self.btn_clear_pending.setVisible(True)
            self.status_label.setText("佇列已暫停")
            self.status_label.setStyleSheet("font-weight: 600; color: #f59e0b; font-size: 13px;")
            self.progress_bar.setVisible(False)
        else:
            self.btn_resume.setVisible(False)
            self.btn_clear_pending.setVisible(False)
            if not is_running and not self.progress_bar.isVisible():
                self.status_label.setText("佇列閒置")
                self.status_label.setStyleSheet("font-weight: 600; color: #9ca3af; font-size: 13px;")

    def _on_resume_clicked(self):
        if self._queue_manager and hasattr(self._queue_manager, "resume_queue"):
            self._queue_manager.resume_queue()
            self.refresh_tasks()

    def _on_clear_pending_clicked(self):
        if self._queue_manager and hasattr(self._queue_manager, "clear_pending"):
            self._queue_manager.clear_pending()
            self.refresh_tasks()

    def _on_cancel_clicked(self):
        if self._queue_manager and hasattr(self._queue_manager, "cancel_current_task"):
            self._queue_manager.cancel_current_task()

    def _on_clear_finished_clicked(self):
        if self._queue_manager:
            if hasattr(self._queue_manager, "clear_finished"):
                self._queue_manager.clear_finished()
            elif hasattr(self._queue_manager, "clear_completed"):
                self._queue_manager.clear_completed()
            self.refresh_tasks()

    def _on_retry_failed_clicked(self):
        if not self._queue_manager:
            return
        if hasattr(self._queue_manager, "retry_failed"):
            self._queue_manager.retry_failed()
        elif hasattr(self._queue_manager, "finished_tasks"):
            for t in self._queue_manager.finished_tasks():
                if getattr(t, "can_retry", False):
                    self._queue_manager.retry_task(t.id)
        self.refresh_tasks()

    def _on_clear_logs_clicked(self):
        self.log_console.clear()

    def _on_copy_logs_clicked(self):
        text = self.log_console.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)

    def _on_task_context_menu(self, pos):
        item = self.task_list.itemAt(pos)
        if not item or not self._queue_manager:
            return

        task_id = item.data(Qt.UserRole)
        menu = QMenu(self)

        remove_action = menu.addAction("從佇列移除")
        retry_action = menu.addAction("重試此任務")
        copy_cmd_action = menu.addAction("複製執行命令")

        action = menu.exec(self.task_list.mapToGlobal(pos))
        if action == remove_action:
            if hasattr(self._queue_manager, "remove_pending_task"):
                self._queue_manager.remove_pending_task(task_id)
            elif hasattr(self._queue_manager, "remove_task"):
                self._queue_manager.remove_task(task_id)
            self.refresh_tasks()
        elif action == retry_action:
            if hasattr(self._queue_manager, "retry_task"):
                self._queue_manager.retry_task(task_id)
                self.refresh_tasks()
        elif action == copy_cmd_action:
            tasks = (
                self._queue_manager.all_tasks()
                if hasattr(self._queue_manager, "all_tasks")
                else getattr(self._queue_manager, "tasks", [])
            )
            for t in tasks:
                if getattr(t, "id", None) == task_id:
                    cmd = getattr(t, "command", [])
                    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
                    from ..runner import sanitize_text
                    QApplication.clipboard().setText(sanitize_text(cmd_str))
                    break
