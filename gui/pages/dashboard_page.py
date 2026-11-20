"""Page 1: 錄播看板 (Dashboard Page) for AutoSlicer Desktop Workbench.

Features:
- Top toolbar:
  - 🔄 重新掃描 (Rescan) button with non-blocking QThread background worker
  - 主播篩選 (Streamer filter dropdown)
  - 狀態篩選 (Status filter dropdown)
  - 檔名關鍵字即時搜尋 (Search input)
  - 緊湊檔案拖曳分析條 (Compact DropZone)
- Batch Actions Toolbar:
  - Shows when >= 1 VODs selected:
    - ⚡ 批次生成 .llc
    - ✂️ 批次無損直切
    - 🏷️ 批次修復 Metadata
    - 📥 批次匯入標註
- VOD Table:
  - Checkbox, Streamer, Filename & Date/Size, Status Badge, Clip Count, Next Step Button, More Actions Menu (⋮)
- Collapsible Advanced Parameters:
  - Model override dropdown (auto/runs/custom picker)
  - Output mode (.llc / cut video)
  - on_threshold, off_threshold, merge_gap_s, min_song_s, pad_s
  - Reset to defaults button
- Task actions builder:
  - Enqueues jobs into QueueManager using CommandBuilder
  - Auto-rescans table when relevant queue tasks finish
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import ConfigManager, get_config
from ..models import TaskItem, TaskStatus, VodItem, VodStatus
from ..page_registry import register_page
from ..queue_manager import QueueManager, get_queue_manager
from ..runner import CommandBuilder
from ..scanner import (
    VodScanner,
    extract_streamer_from_text,
    load_metadata_cache,
    mark_clips_synced,
    resolve_streamer_name,
)
from ..widgets.collapsible import CollapsibleBox
from ..widgets.drop_zone import DropZone
from .base_page import BasePage

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ============================================================================
# Background Scanner Worker Thread
# ============================================================================

class ScanWorker(QObject):
    """Worker object to run directory scanning in a separate thread."""
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, config_manager: ConfigManager) -> None:
        super().__init__()
        self.config_manager = config_manager

    @Slot()
    def run_scan(self) -> None:
        try:
            scanner = VodScanner(self.config_manager)
            vods = scanner.scan_all()
            self.finished.emit(vods)
        except Exception as e:
            self.failed.emit(str(e))


# ============================================================================
# Dashboard Page Implementation
# ============================================================================

@register_page(page_id="dashboard", title="錄播看板", icon_name="dashboard", order=10)
class DashboardPage(BasePage):
    """Page 1: Livestream Recording Dashboard."""

    page_id: str = "dashboard"
    title: str = "錄播看板"
    icon_name: str = "dashboard"
    enabled: bool = True

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        queue_manager: QueueManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(config_manager, queue_manager, parent)

        self._all_vods: list[VodItem] = []
        self._filtered_vods: list[VodItem] = []
        self._selected_paths: set[Path] = set()
        self._is_scanning: bool = False

        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None

        self._init_ui()
        self._connect_signals()

        # Trigger initial scan
        self.trigger_rescan()

    # --- UI Initialization ---

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # 1. Top Toolbar
        layout.addLayout(self._build_top_toolbar())

        # 2. Batch Actions Bar (hidden by default)
        self.batch_bar = self._build_batch_bar()
        layout.addWidget(self.batch_bar)

        # 3. Main VOD Table
        self.table = self._build_table()
        layout.addWidget(self.table, stretch=1)

        # 4. Collapsible Advanced Parameters Box
        self.advanced_box = self._build_advanced_box()
        layout.addWidget(self.advanced_box)

    def _build_top_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        # Rescan Button
        self.btn_rescan = QPushButton("🔄 重新掃描")
        self.btn_rescan.setCursor(Qt.PointingHandCursor)
        self.btn_rescan.setObjectName("primaryButton")
        self.btn_rescan.setToolTip("掃描所有已啟用的監控錄播資料夾")
        self.btn_rescan.clicked.connect(self.trigger_rescan)
        toolbar.addWidget(self.btn_rescan)

        # Streamer Filter
        lbl_streamer = QLabel("主播:")
        lbl_streamer.setStyleSheet("color: #9ca3af; font-weight: 500;")
        toolbar.addWidget(lbl_streamer)

        self.combo_streamer = QComboBox()
        self.combo_streamer.addItem("全部")
        self.combo_streamer.setMinimumWidth(110)
        self.combo_streamer.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.combo_streamer)

        # Status Filter
        lbl_status = QLabel("狀態:")
        lbl_status.setStyleSheet("color: #9ca3af; font-weight: 500;")
        toolbar.addWidget(lbl_status)

        self.combo_status = QComboBox()
        self.combo_status.addItem("全部")
        for s in VodStatus:
            self.combo_status.addItem(s.short_name, s.value)
        self.combo_status.setMinimumWidth(120)
        self.combo_status.currentIndexChanged.connect(self._apply_filters)
        toolbar.addWidget(self.combo_status)

        # Filename Search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜尋檔名關鍵字...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filters)
        self.search_input.setMinimumWidth(160)
        toolbar.addWidget(self.search_input)

        # Compact Drop Zone
        self.drop_zone = DropZone(
            title="拖入錄播或切片快速處理",
            hint="支援 .mp4, .flv, .mkv, .llc",
            compact=True,
            parent=self,
        )
        self.drop_zone.files_dropped.connect(self._on_files_dropped)
        toolbar.addWidget(self.drop_zone, stretch=1)

        return toolbar

    def _build_batch_bar(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("batchToolbar")
        frame.setStyleSheet(
            "QFrame#batchToolbar { background-color: #21252b; border: 1px solid #3b82f6; "
            "border-radius: 6px; padding: 6px 12px; }"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        self.lbl_batch_count = QLabel("已選取 0 場錄播")
        self.lbl_batch_count.setStyleSheet("color: #3b82f6; font-weight: 700; font-size: 13px;")
        layout.addWidget(self.lbl_batch_count)

        btn_select_all = QPushButton("全選")
        btn_select_all.clicked.connect(self._select_all_displayed)
        layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("取消全選")
        btn_deselect_all.clicked.connect(self._deselect_all)
        layout.addWidget(btn_deselect_all)

        layout.addStretch(1)

        btn_batch_llc = QPushButton("⚡ 批次生成 .llc")
        btn_batch_llc.setCursor(Qt.PointingHandCursor)
        btn_batch_llc.setStyleSheet("QPushButton { background-color: #2563eb; color: white; font-weight: 600; padding: 5px 12px; border-radius: 4px; }")
        btn_batch_llc.clicked.connect(lambda: self._execute_batch_action("llc"))
        layout.addWidget(btn_batch_llc)

        btn_batch_cut = QPushButton("✂️ 批次無損直切")
        btn_batch_cut.setCursor(Qt.PointingHandCursor)
        btn_batch_cut.setStyleSheet("QPushButton { background-color: #0284c7; color: white; font-weight: 600; padding: 5px 12px; border-radius: 4px; }")
        btn_batch_cut.clicked.connect(lambda: self._execute_batch_action("cut"))
        layout.addWidget(btn_batch_cut)

        btn_batch_metadata = QPushButton("🏷️ 批次修復 Metadata")
        btn_batch_metadata.setCursor(Qt.PointingHandCursor)
        btn_batch_metadata.setStyleSheet("QPushButton { background-color: #d97706; color: white; font-weight: 600; padding: 5px 12px; border-radius: 4px; }")
        btn_batch_metadata.clicked.connect(lambda: self._execute_batch_action("metadata"))
        layout.addWidget(btn_batch_metadata)

        btn_batch_import = QPushButton("📥 批次匯入標註")
        btn_batch_import.setCursor(Qt.PointingHandCursor)
        btn_batch_import.setStyleSheet("QPushButton { background-color: #7c3aed; color: white; font-weight: 600; padding: 5px 12px; border-radius: 4px; }")
        btn_batch_import.clicked.connect(lambda: self._execute_batch_action("import"))
        layout.addWidget(btn_batch_import)

        frame.setVisible(False)
        return frame

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(self)
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "選取",
            "主播",
            "錄播名稱 / 日期 / 標籤",
            "目前狀態",
            "切片數",
            "下一步動作",
            "動作",
        ])

        # Table configurations
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 48)

        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(1, 120)

        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(3, 140)

        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(4, 110)

        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(5, 150)

        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(6, 60)

        return table

    def _build_advanced_box(self) -> CollapsibleBox:
        box = CollapsibleBox(title="⚙️ 進階推論與後處理參數 (點擊展開)", expanded=False, parent=self)
        inf_def = self.config.get("inference_defaults", {})

        grid = QGridLayout()
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(12)

        # 1. Model Override
        grid.addWidget(QLabel("模型權重覆寫:"), 0, 0)
        self.combo_model_override = QComboBox()
        self.combo_model_override.addItem("自動依主播匹配 (預設)", "")
        self._populate_available_models()
        self.combo_model_override.addItem("📂 瀏覽自訂權重檔...", "__browse__")
        self.combo_model_override.currentIndexChanged.connect(self._on_model_override_changed)
        grid.addWidget(self.combo_model_override, 0, 1, 1, 2)

        # 2. Output Mode
        grid.addWidget(QLabel("預設輸出模式:"), 0, 3)
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("僅生成 .llc 打軸專案 (極速)", "llc")
        self.combo_mode.addItem("無損直切剪輯影片 (切片檔)", "cut")
        cur_mode = inf_def.get("mode", "llc")
        self.combo_mode.setCurrentIndex(1 if cur_mode == "cut" else 0)
        grid.addWidget(self.combo_mode, 0, 4)

        # 3. on_threshold
        grid.addWidget(QLabel("進入唱歌門檻 (on):"), 1, 0)
        self.spin_on = QDoubleSpinBox()
        self.spin_on.setRange(0.1, 0.95)
        self.spin_on.setSingleStep(0.05)
        self.spin_on.setValue(float(inf_def.get("on_threshold", 0.6)))
        grid.addWidget(self.spin_on, 1, 1)

        # 4. off_threshold
        grid.addWidget(QLabel("離開唱歌門檻 (off):"), 1, 2)
        self.spin_off = QDoubleSpinBox()
        self.spin_off.setRange(0.05, 0.9)
        self.spin_off.setSingleStep(0.05)
        self.spin_off.setValue(float(inf_def.get("off_threshold", 0.4)))
        grid.addWidget(self.spin_off, 1, 3)

        # 5. merge_gap_s
        grid.addWidget(QLabel("鄰段合併間隙 (gap):"), 2, 0)
        self.spin_merge_gap = QSpinBox()
        self.spin_merge_gap.setRange(0, 300)
        self.spin_merge_gap.setSuffix(" 秒")
        self.spin_merge_gap.setValue(int(inf_def.get("merge_gap_s", 90)))
        grid.addWidget(self.spin_merge_gap, 2, 1)

        # 6. min_song_s
        grid.addWidget(QLabel("最短歌曲長度 (min):"), 2, 2)
        self.spin_min_song = QSpinBox()
        self.spin_min_song.setRange(10, 300)
        self.spin_min_song.setSuffix(" 秒")
        self.spin_min_song.setValue(int(inf_def.get("min_song_s", 50)))
        grid.addWidget(self.spin_min_song, 2, 3)

        # 7. pad_s
        grid.addWidget(QLabel("前後緩衝 (pad):"), 2, 4)
        self.spin_pad = QSpinBox()
        self.spin_pad.setRange(0, 30)
        self.spin_pad.setSuffix(" 秒")
        self.spin_pad.setValue(int(inf_def.get("pad_s", 5)))
        grid.addWidget(self.spin_pad, 2, 5)

        # Reset button
        btn_reset = QPushButton("重設參數為設定預設值")
        btn_reset.clicked.connect(self._reset_advanced_params)
        grid.addWidget(btn_reset, 1, 4, 1, 2)

        box.set_content_layout(grid)
        return box

    def _populate_available_models(self) -> None:
        """Populate available best.pt checkpoints from runs/ into combo."""
        models = self.config.scan_available_models()
        for name, path in models.items():
            self.combo_model_override.addItem(f"{name} ({path.name})", str(path))

    def _on_model_override_changed(self, index: int) -> None:
        val = self.combo_model_override.currentData()
        if val == "__browse__":
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "選擇 PyTorch 模型權重檔",
                str(self.config.get_runs_dir()),
                "PyTorch Checkpoints (*.pt *.pth);;所有檔案 (*.*)",
            )
            if chosen:
                p = Path(chosen)
                self.combo_model_override.insertItem(
                    1, f"自訂: {p.name}", str(p)
                )
                self.combo_model_override.setCurrentIndex(1)
            else:
                self.combo_model_override.setCurrentIndex(0)

    def _reset_advanced_params(self) -> None:
        inf_def = self.config.get("inference_defaults", {})
        self.combo_model_override.setCurrentIndex(0)
        self.combo_mode.setCurrentIndex(1 if inf_def.get("mode") == "cut" else 0)
        self.spin_on.setValue(float(inf_def.get("on_threshold", 0.6)))
        self.spin_off.setValue(float(inf_def.get("off_threshold", 0.4)))
        self.spin_merge_gap.setValue(int(inf_def.get("merge_gap_s", 90)))
        self.spin_min_song.setValue(int(inf_def.get("min_song_s", 50)))
        self.spin_pad.setValue(int(inf_def.get("pad_s", 5)))

    # --- Signal Connections ---

    def _connect_signals(self) -> None:
        # Auto-rescan when task completed in queue
        self.queue_manager.task_completed.connect(self._on_task_finished)
        self.queue_manager.task_failed.connect(self._on_task_finished)

    def on_page_activated(self) -> None:
        """Called when user switches to Dashboard."""
        if not self._all_vods:
            self.trigger_rescan()

    def on_config_updated(self) -> None:
        """Called when configuration changes (e.g. watch_dirs edited)."""
        self.trigger_rescan()

    # --- Rescan Logic (Background QThread) ---

    def stop_scan(self, timeout_ms: int = 2000) -> None:
        """Safely stop and wait for background scanner thread to exit."""
        if self._scan_thread and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(timeout_ms)
        self._is_scanning = False

    def closeEvent(self, event) -> None:
        """Ensure thread cleanup on widget closure."""
        self.stop_scan()
        super().closeEvent(event)

    def trigger_rescan(self) -> None:
        """Trigger background scan of watch directories."""
        if self._is_scanning:
            return

        self._is_scanning = True
        self.btn_rescan.setEnabled(False)
        self.btn_rescan.setText("⏳ 掃描中...")

        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(self.config)
        self._scan_worker.moveToThread(self._scan_thread)

        self._scan_thread.started.connect(self._scan_worker.run_scan)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)

        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)

        self._scan_thread.start()

    @Slot(list)
    def _on_scan_finished(self, vods: list[VodItem]) -> None:
        self._is_scanning = False
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("🔄 重新掃描")

        self._all_vods = vods

        # Update streamer combo items
        streamers = sorted({v.streamer for v in vods if v.streamer})
        cur_streamer = self.combo_streamer.currentText()
        self.combo_streamer.blockSignals(True)
        self.combo_streamer.clear()
        self.combo_streamer.addItem("全部")
        for s in streamers:
            self.combo_streamer.addItem(s)
        idx = self.combo_streamer.findText(cur_streamer)
        if idx >= 0:
            self.combo_streamer.setCurrentIndex(idx)
        self.combo_streamer.blockSignals(False)

        self._apply_filters()

    @Slot(str)
    def _on_scan_failed(self, error: str) -> None:
        self._is_scanning = False
        self.btn_rescan.setEnabled(True)
        self.btn_rescan.setText("🔄 重新掃描")
        print(f"[Dashboard] Scan error: {error}", file=sys.stderr)

    @Slot(TaskItem)
    def _on_task_finished(self, task: TaskItem) -> None:
        """Automatically rescan after queue finishes a relevant task."""
        # Check if task was related to VOD processing
        meta = task.metadata
        if meta and meta.get("rescan_dashboard"):
            # If metadata task completed, mark affected clips in cache (excluding any XML files)
            if meta.get("action") == "metadata" and task.status == TaskStatus.COMPLETED:
                clip_files = meta.get("clip_files")
                if clip_files:
                    actual_clips = [c for c in clip_files if not str(c).lower().endswith(".xml")]
                    mark_clips_synced(actual_clips)
                else:
                    targets = meta.get("targets", [])
                    actual_clips = [t for t in targets if not str(t).lower().endswith(".xml")]
                    mark_clips_synced(actual_clips)
            self.trigger_rescan()

    # --- Filtering & Table Rendering ---

    def _apply_filters(self) -> None:
        streamer_filter = self.combo_streamer.currentText()
        status_filter = self.combo_status.currentData()
        search_kw = self.search_input.text().strip().lower()

        filtered: list[VodItem] = []
        for v in self._all_vods:
            if streamer_filter != "全部" and v.streamer != streamer_filter:
                continue
            if status_filter is not None and v.status.value != status_filter:
                continue
            if search_kw and (search_kw not in v.filename.lower() and search_kw not in v.streamer.lower()):
                continue
            filtered.append(v)

        self._filtered_vods = filtered
        self._render_table(filtered)

    def _render_table(self, vods: list[VodItem]) -> None:
        self.table.setRowCount(0)
        self.table.setRowCount(len(vods))

        for row, vod in enumerate(vods):
            self.table.setRowHeight(row, 56)

            # 0. Selection Checkbox
            cb_container, cb = self._create_checkbox_widget(vod)
            self.table.setCellWidget(row, 0, cb_container)

            # 1. Streamer
            lbl_streamer = QLabel(vod.streamer)
            lbl_streamer.setAlignment(Qt.AlignCenter)
            lbl_streamer.setStyleSheet("font-weight: 600; color: #93c5fd;")
            self.table.setCellWidget(row, 1, lbl_streamer)

            # 2. Filename & Date / Size
            name_widget = self._create_name_widget(vod)
            self.table.setCellWidget(row, 2, name_widget)

            # 3. Status Badge
            badge_widget = self._create_badge_widget(vod.status)
            self.table.setCellWidget(row, 3, badge_widget)

            # 4. Clip Count
            clip_widget = self._create_clips_widget(vod)
            self.table.setCellWidget(row, 4, clip_widget)

            # 5. Next Step Action Button
            action_btn = self._create_action_button(vod)
            self.table.setCellWidget(row, 5, action_btn)

            # 6. More Actions (⋮)
            more_btn = self._create_more_button(vod)
            self.table.setCellWidget(row, 6, more_btn)

        self._update_batch_toolbar_visibility()

    def _create_checkbox_widget(self, vod: VodItem) -> tuple[QWidget, QCheckBox]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(vod.file_path in self._selected_paths)
        cb.toggled.connect(lambda checked, v=vod: self._on_row_checked(v, checked))
        layout.addWidget(cb)
        return container, cb

    def _create_name_widget(self, vod: VodItem) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        title = QLabel(vod.filename)
        title.setStyleSheet("font-weight: 600; color: #f3f4f6; font-size: 13px;")
        title.setToolTip(str(vod.file_path))

        sub_parts = [vod.display_date, vod.display_size]
        if vod.xml_path:
            sub_parts.append("📄 錄播姬 XML")
        if vod.llc_path:
            sub_parts.append("✂️ LLC 專案")

        sub = QLabel("  •  ".join(sub_parts))
        sub.setStyleSheet("font-size: 11px; color: #8b949e;")

        layout.addWidget(title)
        layout.addWidget(sub)
        return container

    def _create_badge_widget(self, status: VodStatus) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setAlignment(Qt.AlignCenter)

        badge = QLabel(status.display_name)
        badge.setStyleSheet(
            f"background-color: {status.badge_color}22; "
            f"color: {status.badge_color}; "
            f"border: 1px solid {status.badge_color}66; "
            f"border-radius: 4px; padding: 3px 8px; font-weight: 600; font-size: 11px;"
        )
        layout.addWidget(badge)
        return container

    def _create_clips_widget(self, vod: VodItem) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setAlignment(Qt.AlignCenter)

        count = vod.clip_count
        if count == 0:
            lbl = QLabel("-")
            lbl.setStyleSheet("color: #6b7280; font-size: 12px;")
        else:
            unprocessed = vod.unprocessed_clip_count
            if unprocessed > 0:
                text = f"{count} 首 ({unprocessed} 待修)"
                lbl = QLabel(text)
                lbl.setStyleSheet("color: #f97316; font-weight: 600; font-size: 11px;")
            else:
                text = f"{count} 首 (已修)"
                lbl = QLabel(text)
                lbl.setStyleSheet("color: #10b981; font-weight: 500; font-size: 11px;")
        layout.addWidget(lbl)
        return container

    def _create_action_button(self, vod: VodItem) -> QPushButton:
        action_name = vod.status.default_action_name
        btn = QPushButton(action_name)
        btn.setCursor(Qt.PointingHandCursor)

        # Style by lifecycle state
        if vod.status == VodStatus.WAITING_LLC:
            btn.setStyleSheet(
                "QPushButton { background-color: #2563eb; color: white; font-weight: 600; border-radius: 4px; padding: 5px 8px; } "
                "QPushButton:hover { background-color: #1d4ed8; }"
            )
        elif vod.status == VodStatus.LLC_GENERATED:
            btn.setStyleSheet(
                "QPushButton { background-color: #d97706; color: white; font-weight: 600; border-radius: 4px; padding: 5px 8px; } "
                "QPushButton:hover { background-color: #b45309; }"
            )
        elif vod.status == VodStatus.HAS_CLIPS_NEED_METADATA:
            btn.setStyleSheet(
                "QPushButton { background-color: #ea580c; color: white; font-weight: 600; border-radius: 4px; padding: 5px 8px; } "
                "QPushButton:hover { background-color: #c2410c; }"
            )
        elif vod.status == VodStatus.WAITING_IMPORT_ANNOTATION:
            btn.setStyleSheet(
                "QPushButton { background-color: #7c3aed; color: white; font-weight: 600; border-radius: 4px; padding: 5px 8px; } "
                "QPushButton:hover { background-color: #6d28d9; }"
            )
        else:  # COMPLETED
            btn.setStyleSheet(
                "QPushButton { background-color: #059669; color: white; font-weight: 600; border-radius: 4px; padding: 5px 8px; } "
                "QPushButton:hover { background-color: #047857; }"
            )

        btn.clicked.connect(lambda _, v=vod: self._execute_next_step(v))
        return btn

    def _create_more_button(self, vod: VodItem) -> QPushButton:
        btn = QPushButton("⋮")
        btn.setFixedSize(32, 28)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background-color: #282d36; color: #9ca3af; font-size: 15px; "
            "font-weight: bold; border: 1px solid #333842; border-radius: 4px; } "
            "QPushButton:hover { background-color: #333842; color: #ffffff; }"
        )
        btn.clicked.connect(lambda _, b=btn, v=vod: self._show_more_menu(b, v))
        return btn

    # --- Checkbox Selection & Batch Actions ---

    def _on_row_checked(self, vod: VodItem, checked: bool) -> None:
        if checked:
            self._selected_paths.add(vod.file_path)
        else:
            self._selected_paths.discard(vod.file_path)
        self._update_batch_toolbar_visibility()

    def _update_batch_toolbar_visibility(self) -> None:
        count = len(self._selected_paths)
        if count > 0:
            self.lbl_batch_count.setText(f"已選取 {count} 場錄播")
            self.batch_bar.setVisible(True)
        else:
            self.batch_bar.setVisible(False)

    def _select_all_displayed(self) -> None:
        for vod in self._filtered_vods:
            self._selected_paths.add(vod.file_path)
        self._render_table(self._filtered_vods)

    def _deselect_all(self) -> None:
        self._selected_paths.clear()
        self._render_table(self._filtered_vods)

    def _get_selected_vod_items(self) -> list[VodItem]:
        return [v for v in self._all_vods if v.file_path in self._selected_paths]

    # --- Action Execution Routing ---

    def _execute_next_step(self, vod: VodItem) -> None:
        """Route to appropriate action based on current state machine status."""
        status = vod.status
        if status == VodStatus.WAITING_LLC:
            want_cut = (self.combo_mode.currentData() == "cut")
            self.action_generate_llc(vod, cut_video=want_cut)
        elif status == VodStatus.LLC_GENERATED:
            self.action_open_losslesscut(vod)
        elif status == VodStatus.HAS_CLIPS_NEED_METADATA:
            self.action_sync_metadata(vod)
        elif status == VodStatus.WAITING_IMPORT_ANNOTATION:
            self.action_import_annotations(vod)
        elif status == VodStatus.COMPLETED:
            self.action_open_folder(vod)
        else:
            self.action_open_folder(vod)

    def _show_more_menu(self, parent_btn: QPushButton, vod: VodItem) -> None:
        menu = QMenu(self)

        act_llc = menu.addAction("⚡ 生成 .llc (僅打軸)")
        act_llc.triggered.connect(lambda: self.action_generate_llc(vod, cut_video=False))

        act_cut = menu.addAction("✂️ 無損直切 (切片影片)")
        act_cut.triggered.connect(lambda: self.action_generate_llc(vod, cut_video=True))

        act_losslesscut = menu.addAction("🎬 用 LosslessCut 開啟")
        act_losslesscut.triggered.connect(lambda: self.action_open_losslesscut(vod))

        act_metadata = menu.addAction("🏷️ 修復 Metadata 與時間戳")
        act_metadata.triggered.connect(lambda: self.action_sync_metadata(vod))

        act_import = menu.addAction("📥 匯入 .llc 反哺標註")
        act_import.triggered.connect(lambda: self.action_import_annotations(vod))

        menu.addSeparator()

        act_folder = menu.addAction("📁 在檔案總管中顯示")
        act_folder.triggered.connect(lambda: self.action_open_folder(vod))

        act_copy = menu.addAction("📋 複製檔案路徑")
        act_copy.triggered.connect(lambda: self._copy_path_to_clipboard(vod.file_path))

        menu.exec(parent_btn.mapToGlobal(parent_btn.rect().bottomLeft()))

    def _copy_path_to_clipboard(self, path: Path) -> None:
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(str(path))

    # --- Concrete Actions (Enqueueing Tasks) ---

    def action_generate_llc(self, vod: VodItem, cut_video: bool = False) -> None:
        """Enqueue infer.py job to generate .llc or cut video directly."""
        checkpoint = self._resolve_checkpoint_for_vod(vod)
        if not checkpoint or not checkpoint.is_file():
            QMessageBox.warning(
                self,
                "找不到模型權重",
                f"無法為主播「{vod.streamer}」解析可用的 best.pt 權重檔。\n請至「全域設定」指定權重檔路徑。",
            )
            return

        out_dir = PROJECT_ROOT / "out"
        clip_out_dir = vod.parent_dir

        import inspect
        sig = inspect.signature(CommandBuilder.build_infer_command)
        kwargs = {}
        if "merge_gap_s" in sig.parameters:
            kwargs["merge_gap_s"] = float(self.spin_merge_gap.value())
        if "min_song_s" in sig.parameters:
            kwargs["min_song_s"] = float(self.spin_min_song.value())
        if "pad_s" in sig.parameters:
            kwargs["pad_s"] = float(self.spin_pad.value())

        cmd = CommandBuilder.build_infer_command(
            repo_root=PROJECT_ROOT,
            input_path=vod.file_path,
            checkpoint=checkpoint,
            out_dir=out_dir,
            clip_out_dir=clip_out_dir,
            cut_video=cut_video,
            on_threshold=self.spin_on.value(),
            off_threshold=self.spin_off.value(),
            **kwargs,
        )
        if "merge_gap_s" not in sig.parameters:
            cmd.extend([
                "--merge-gap-s", str(self.spin_merge_gap.value()),
                "--min-song-s", str(self.spin_min_song.value()),
                "--pad-s", str(self.spin_pad.value()),
            ])

        title = f"{'無損切歌' if cut_video else '生成打軸 .llc'}: {vod.filename}"
        task = TaskItem(
            title=title,
            command=cmd,
            cwd=str(PROJECT_ROOT),
            metadata={"rescan_dashboard": True, "action": "infer", "vod_path": str(vod.file_path)},
        )
        self.enqueue_task(task)

    def action_open_losslesscut(self, vod: VodItem) -> None:
        """Launch LosslessCut asynchronously with video and .llc project loaded."""
        exe = self.config.get_losslesscut_exe()
        if not exe or not exe.is_file():
            res = QMessageBox.question(
                self,
                "尚未指定 LosslessCut",
                "未在常見路徑找到 LosslessCut.exe。\n是否現在手動指定其位置？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                chosen, _ = QFileDialog.getOpenFileName(
                    self, "選擇 LosslessCut.exe", "C:\\", "執行檔 (*.exe)"
                )
                if chosen:
                    self.config.set_losslesscut_exe(chosen)
                    exe = Path(chosen)
                else:
                    return
            else:
                return

        success = CommandBuilder.launch_losslesscut(
            exe_path=exe,
            media_path=vod.file_path,
            llc_path=vod.llc_path,
        )
        if not success:
            QMessageBox.warning(self, "啟動失敗", f"無法啟動 LosslessCut：{exe}")

    def action_sync_metadata(self, vod: VodItem) -> None:
        """Enqueue sync_clip_metadata.py job to fix tags and Windows timestamps."""
        targets: list[str | Path] = []
        if vod.clip_files:
            targets.extend(vod.clip_files)
        else:
            # Fallback to parent directory
            targets.append(vod.parent_dir)

        if vod.xml_path and vod.xml_path.is_file():
            targets.append(vod.xml_path)

        cmd = CommandBuilder.build_sync_metadata_command(PROJECT_ROOT, targets)
        title = f"修復 Metadata: {vod.filename} ({len(vod.clip_files)} 個切片)"
        task = TaskItem(
            title=title,
            command=cmd,
            cwd=str(PROJECT_ROOT),
            metadata={
                "rescan_dashboard": True,
                "action": "metadata",
                "targets": [str(t) for t in targets],
                "clip_files": [str(c) for c in vod.clip_files],
            },
        )
        self.enqueue_task(task)

    def action_import_annotations(self, vod: VodItem) -> None:
        """Enqueue convert_llc_to_annotations.py job to import LLC into data/annotations/."""
        target = vod.llc_path if (vod.llc_path and vod.llc_path.is_file()) else vod.file_path
        cmd = CommandBuilder.build_import_llc_command(PROJECT_ROOT, [target])
        title = f"匯入標註: {vod.filename}"
        task = TaskItem(
            title=title,
            command=cmd,
            cwd=str(PROJECT_ROOT),
            metadata={"rescan_dashboard": True, "action": "import"},
        )
        self.enqueue_task(task)

    def action_open_folder(self, vod: VodItem) -> None:
        """Reveal file in Windows File Explorer."""
        p = vod.file_path
        if p.exists():
            subprocess.Popen(f'explorer.exe /select,"{p}"')
        else:
            parent = vod.parent_dir
            if parent.exists():
                os.startfile(str(parent))

    def _resolve_checkpoint_for_vod(self, vod: VodItem) -> Path | None:
        """Resolve model checkpoint honoring advanced override or streamer mapping."""
        # 1. Advanced param override
        override_data = self.combo_model_override.currentData()
        if override_data and override_data not in ["", "__browse__"]:
            p = Path(override_data)
            if p.is_file():
                return p

        # 2. Config mapping for streamer
        return self.config.get_model_path_for_streamer(vod.streamer)

    # --- Batch Operations ---

    def _execute_batch_action(self, action_type: str) -> None:
        selected_vods = self._get_selected_vod_items()
        if not selected_vods:
            return

        if action_type == "llc":
            for v in selected_vods:
                self.action_generate_llc(v, cut_video=False)
        elif action_type == "cut":
            for v in selected_vods:
                self.action_generate_llc(v, cut_video=True)
        elif action_type == "metadata":
            for v in selected_vods:
                self.action_sync_metadata(v)
        elif action_type == "import":
            for v in selected_vods:
                self.action_import_annotations(v)

        # Clear selection after enqueuing
        self._deselect_all()

    # --- Drag & Drop Handler ---

    def handle_dropped_files(self, paths: list[str]) -> None:
        """Entry point for dropped files routed from MainWindow or DropZone."""
        self._on_files_dropped(paths)

    def _on_files_dropped(self, paths: list[str]) -> None:
        """Handle files dropped into the compact drop zone."""
        if not paths:
            return

        # If a directory was dropped, offer to add as watch dir or scan it
        dirs = [Path(p) for p in paths if Path(p).is_dir()]
        files = [Path(p) for p in paths if Path(p).is_file()]

        if dirs:
            res = QMessageBox.question(
                self,
                "偵測到資料夾",
                f"您拖入了 {len(dirs)} 個資料夾。\n是否將其新增至監控錄播目錄？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if res == QMessageBox.StandardButton.Yes:
                for d in dirs:
                    self.config.add_watch_dir(label=d.name, path_str=str(d))
                self.trigger_rescan()
                return

        if files:
            # Check if user dropped .llc files
            llc_files = [f for f in files if f.suffix.lower() == ".llc"]
            if llc_files:
                for llc in llc_files:
                    cmd = CommandBuilder.build_import_llc_command(PROJECT_ROOT, [llc])
                    task = TaskItem(
                        title=f"匯入外部標註: {llc.name}",
                        command=cmd,
                        cwd=str(PROJECT_ROOT),
                        metadata={"rescan_dashboard": True},
                    )
                    self.enqueue_task(task)
                return

            # Check if user dropped video files
            media_files = [
                f for f in files
                if f.suffix.lower() in [".mp4", ".flv", ".mkv", ".ts", ".m4a"]
            ]
            if media_files:
                want_cut = (self.combo_mode.currentData() == "cut")
                for mf in media_files:
                    # Extract streamer from filename or parent folder
                    streamer = extract_streamer_from_text(mf.stem)
                    if not streamer or streamer == mf.stem:
                        streamer = resolve_streamer_name(mf.parent.name)

                    checkpoint = (
                        self.config.get_model_path_for_streamer(streamer)
                        or (PROJECT_ROOT / "best.pt")
                    )

                    import inspect
                    sig = inspect.signature(CommandBuilder.build_infer_command)
                    kwargs = {}
                    if "merge_gap_s" in sig.parameters:
                        kwargs["merge_gap_s"] = float(self.spin_merge_gap.value())
                    if "min_song_s" in sig.parameters:
                        kwargs["min_song_s"] = float(self.spin_min_song.value())
                    if "pad_s" in sig.parameters:
                        kwargs["pad_s"] = float(self.spin_pad.value())

                    cmd = CommandBuilder.build_infer_command(
                        repo_root=PROJECT_ROOT,
                        input_path=mf,
                        checkpoint=checkpoint,
                        clip_out_dir=mf.parent,
                        cut_video=want_cut,
                        on_threshold=self.spin_on.value(),
                        off_threshold=self.spin_off.value(),
                        **kwargs,
                    )
                    if "merge_gap_s" not in sig.parameters:
                        cmd.extend([
                            "--merge-gap-s", str(self.spin_merge_gap.value()),
                            "--min-song-s", str(self.spin_min_song.value()),
                            "--pad-s", str(self.spin_pad.value()),
                        ])

                    title = f"{'拖曳無損切歌' if want_cut else '拖曳分析打軸'}: {mf.name}"
                    task = TaskItem(
                        title=title,
                        command=cmd,
                        cwd=str(PROJECT_ROOT),
                        metadata={"rescan_dashboard": True, "action": "infer"},
                    )
                    self.enqueue_task(task)
