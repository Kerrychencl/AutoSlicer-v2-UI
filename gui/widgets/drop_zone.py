"""
Universal native Windows drag & drop widget.
Extracts local file paths via event.mimeData().urls() -> toLocalFile().
Supports instant path extraction without blocking or loading file contents.
"""

from __future__ import annotations

import os
from typing import Sequence
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


DEFAULT_ALLOWED_EXTENSIONS = [".mp4", ".flv", ".mkv", ".ts", ".m4a", ".llc", ".json"]


class DropZone(QFrame):
    """
    Drag and drop target area for files and directories.
    Extracts local filesystem paths instantly from Windows Explorer.
    """
    # Support both snake_case and camelCase signal names for flexible consumer use
    files_dropped = Signal(list)
    filesDropped = files_dropped

    def __init__(
        self,
        title: str = "將檔案或資料夾拖曳至此處",
        hint: str = "或點擊此處選擇本機檔案（支援 .mp4, .flv, .mkv, .ts, .m4a, .llc）",
        allowed_extensions: Sequence[str] | None = None,
        allow_directories: bool = True,
        compact: bool = False,
        file_dialog_filter: str = "支援格式 (*.mp4 *.flv *.mkv *.ts *.m4a *.llc *.json);;所有檔案 (*.*)",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)

        if allowed_extensions is None:
            self.allowed_extensions = list(DEFAULT_ALLOWED_EXTENSIONS)
        elif len(allowed_extensions) == 0:
            self.allowed_extensions = None  # Allow any extension when explicitly passed empty sequence
        else:
            self.allowed_extensions = [
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in allowed_extensions
            ]
        self.allow_directories = allow_directories
        self.file_dialog_filter = file_dialog_filter
        self.compact = compact

        self._init_ui(title, hint)

    def _init_ui(self, title: str, hint: str):
        if self.compact:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 6, 12, 6)
            layout.setSpacing(8)

            self.icon_label = QLabel("📥", self)
            self.icon_label.setStyleSheet("font-size: 14px;")

            self.title_label = QLabel(f"{title} - {hint}", self)
            self.title_label.setObjectName("dropZoneTitle")
            self.title_label.setStyleSheet("font-size: 12px; font-weight: normal; color: #9ca3af;")

            layout.addWidget(self.icon_label)
            layout.addWidget(self.title_label, stretch=1)
            self.hint_label = None
            self.setFixedHeight(38)
        else:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(6)
            layout.setAlignment(Qt.AlignCenter)

            self.icon_label = QLabel("📥", self)
            self.icon_label.setAlignment(Qt.AlignCenter)
            self.icon_label.setStyleSheet("font-size: 26px; margin-bottom: 2px;")

            self.title_label = QLabel(title, self)
            self.title_label.setObjectName("dropZoneTitle")
            self.title_label.setAlignment(Qt.AlignCenter)

            self.hint_label = QLabel(hint, self)
            self.hint_label.setObjectName("dropZoneHint")
            self.hint_label.setAlignment(Qt.AlignCenter)

            layout.addWidget(self.icon_label)
            layout.addWidget(self.title_label)
            layout.addWidget(self.hint_label)
            self.setMinimumHeight(100)

    def set_text(self, title: str, hint: str | None = None):
        """Update label texts dynamically."""
        if self.compact:
            text = f"{title} - {hint}" if hint else title
            self.title_label.setText(text)
        else:
            self.title_label.setText(title)
            if hint and self.hint_label:
                self.hint_label.setText(hint)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        self._set_drag_active(False)
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return

        extracted_paths: list[str] = []
        for url in urls:
            local_path = url.toLocalFile()
            if not local_path:
                continue
            norm_path = os.path.normpath(local_path)
            if self._is_path_allowed(norm_path):
                extracted_paths.append(norm_path)

        if extracted_paths:
            event.acceptProposedAction()
            self.files_dropped.emit(extracted_paths)
        else:
            event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._open_file_dialog()
            event.accept()
        else:
            super().mousePressEvent(event)

    def _open_file_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "選擇檔案",
            "",
            self.file_dialog_filter,
        )
        if files:
            norm_files = [os.path.normpath(f) for f in files if self._is_path_allowed(f)]
            if norm_files:
                self.files_dropped.emit(norm_files)

    def _is_path_allowed(self, path: str) -> bool:
        if os.path.isdir(path):
            return self.allow_directories
        if not self.allowed_extensions:
            return True
        ext = os.path.splitext(path)[1].lower()
        return ext in self.allowed_extensions

    def _set_drag_active(self, active: bool):
        self.setProperty("dragOver", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
