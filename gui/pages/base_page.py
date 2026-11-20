"""Abstract BasePage interface for AutoSlicer Desktop Workbench.

Every page in the workbench inherits from BasePage, providing:
- Access to global ConfigManager and QueueManager
- Lifecycle hooks (on_page_activated, on_page_deactivated, on_config_updated)
- Standard dialog helpers and task enqueuing utilities
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox, QWidget

from ..config import ConfigManager, get_config
from ..models import TaskItem
from ..queue_manager import QueueManager, get_queue_manager


class BasePage(QWidget):
    """Abstract base class for all workbench pages."""

    page_id: str = "base"
    title: str = "基本頁面"
    icon_name: str = "page"
    enabled: bool = True

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        queue_manager: QueueManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config_manager or get_config()
        self.queue_manager = queue_manager or get_queue_manager()

        # Connect global config updates
        self.config.config_changed.connect(self.on_config_updated)

    # --- Lifecycle Hooks ---

    def on_page_activated(self) -> None:
        """Called by MainWindow when this page becomes the active page in QStackedWidget."""
        pass

    def on_page_deactivated(self) -> None:
        """Called by MainWindow when leaving this page."""
        pass

    def on_config_updated(self) -> None:
        """Called whenever global configuration is updated."""
        pass

    # --- Convenience Helpers ---

    def enqueue_task(self, task: TaskItem) -> str:
        """Convenience wrapper to enqueue a task into the global single-thread queue."""
        return self.queue_manager.add_task(task)

    def enqueue_tasks(self, tasks: list[TaskItem]) -> list[str]:
        """Convenience wrapper to enqueue multiple tasks into the queue."""
        return self.queue_manager.add_tasks(tasks)

    def show_info(self, title: str, message: str) -> None:
        """Show standard info modal dialog."""
        QMessageBox.information(self, title, message)

    def show_warning(self, title: str, message: str) -> None:
        """Show standard warning modal dialog."""
        QMessageBox.warning(self, title, message)

    def show_error(self, title: str, message: str) -> None:
        """Show standard error modal dialog."""
        QMessageBox.critical(self, title, message)

    def show_confirm(self, title: str, message: str) -> bool:
        """Show standard yes/no confirmation dialog."""
        res = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return res == QMessageBox.StandardButton.Yes
