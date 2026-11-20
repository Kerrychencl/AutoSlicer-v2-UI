"""
Clean collapsible box widget for advanced parameters and expandable sections.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleBox(QWidget):
    """
    A collapsible container widget with a header bar that expands or collapses
    its content area.
    """
    toggled = Signal(bool)

    def __init__(self, title: str = "進階參數", expanded: bool = False, parent=None):
        super().__init__(parent)
        self._title = title
        self._is_expanded = expanded

        self._init_ui()
        self.set_expanded(expanded)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Header bar
        self.header_frame = QFrame(self)
        self.header_frame.setObjectName("collapsibleHeader")
        self.header_frame.setCursor(Qt.PointingHandCursor)

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(8)

        self.arrow_label = QLabel("▶", self.header_frame)
        self.arrow_label.setStyleSheet("color: #9ca3af; font-size: 11px;")

        self.title_label = QLabel(self._title, self.header_frame)
        self.title_label.setStyleSheet("font-weight: 600; color: #d1d5db; font-size: 13px;")

        header_layout.addWidget(self.arrow_label)
        header_layout.addWidget(self.title_label, stretch=1)

        # Content area
        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("collapsibleContent")
        self.content_layout = QVBoxLayout(self.content_frame)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(8)

        root_layout.addWidget(self.header_frame)
        root_layout.addWidget(self.content_frame)

        # Header click event
        self.header_frame.mousePressEvent = self._on_header_clicked

    def _on_header_clicked(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
            event.accept()
        else:
            event.ignore()

    def toggle(self):
        """Toggle expansion state."""
        self.set_expanded(not self._is_expanded)

    def is_expanded(self) -> bool:
        return self._is_expanded

    def set_expanded(self, expanded: bool):
        self._is_expanded = expanded
        self.content_frame.setVisible(expanded)
        self.arrow_label.setText("▼" if expanded else "▶")
        self.arrow_label.setStyleSheet("color: #3b82f6; font-size: 11px;" if expanded else "color: #9ca3af; font-size: 11px;")
        self.toggled.emit(expanded)

    def set_title(self, title: str):
        self._title = title
        self.title_label.setText(title)

    def title(self) -> str:
        return self._title

    def set_content_layout(self, layout: QLayout):
        """Replace the content frame's layout."""
        # Clear existing layout items
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        QWidget().setLayout(self.content_layout)
        self.content_layout = layout
        self.content_frame.setLayout(layout)

    def add_widget(self, widget: QWidget):
        """Add a widget directly to the content layout."""
        self.content_layout.addWidget(widget)

    def add_layout(self, layout: QLayout):
        """Add a sub-layout to the content layout."""
        self.content_layout.addLayout(layout)
