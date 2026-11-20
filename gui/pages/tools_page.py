"""Page 3: Quick Tools Page for AutoSlicer Desktop Workbench.

Features:
- Mode A + Mode C Metadata & Windows CreationTime repair drag-drop zone
- Optional reference VOD/XML picker for Mode C manual pairing
- Checkboxes for syncing Windows CreationTime and stripping QC tags
- Standalone .llc import drag-drop zone calling scripts/convert_llc_to_annotations.py
- Enqueuing tasks into the single-threaded TaskQueue
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import TaskItem
from ..page_registry import register_page
from ..runner import CommandBuilder
from ..widgets.drop_zone import DropZone
from .base_page import BasePage


@register_page(page_id="tools", title="快速工具", icon_name="tools", order=30)
class ToolsPage(BasePage):
    """Page 3: 快速獨立工具（不在監控目錄或 Mode C 專用）."""

    page_id: str = "tools"
    title: str = "快速工具"
    icon_name: str = "tools"
    enabled: bool = True

    def __init__(self, config_manager=None, queue_manager=None, parent=None) -> None:
        super().__init__(config_manager, queue_manager, parent)
        self._clip_paths: list[Path] = []
        self._llc_paths: list[Path] = []
        self._mode_c_ref: Path | None = None

        self._init_ui()
        self._sync_with_config()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll area
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setObjectName("pageScrollArea")

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(20)

        # Top Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title_lbl = QLabel("快速獨立工具箱", self)
        title_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #f3f4f6;")
        desc_lbl = QLabel(
            "提供不在監控目錄下的臨時影音處理：包含 Mode A 逆向溯源與 Mode C 人機配對標籤修復、以及獨立 LosslessCut 打軸專案匯入。",
            self,
        )
        desc_lbl.setStyleSheet("color: #9ca3af; font-size: 13px;")
        desc_lbl.setWordWrap(True)
        header_box.addWidget(title_lbl)
        header_box.addWidget(desc_lbl)
        layout.addLayout(header_box)

        # =========================================================================
        # Zone 1: Metadata & CreationTime Repair (Mode A + Mode C)
        # =========================================================================
        meta_card = QFrame(self)
        meta_card.setObjectName("cardFrame")
        meta_layout = QVBoxLayout(meta_card)
        meta_layout.setContentsMargins(18, 18, 18, 18)
        meta_layout.setSpacing(12)

        meta_title_row = QHBoxLayout()
        meta_title = QLabel("1. 影音切片中繼資料 (Metadata) 與 Windows 建立時間修復 (Mode A + Mode C)", self)
        meta_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")
        self.clip_count_badge = QLabel("0 個切片", self)
        self.clip_count_badge.setStyleSheet(
            "background-color: #2b313c; color: #60a5fa; padding: 2px 8px; border-radius: 4px; font-size: 12px;"
        )
        meta_title_row.addWidget(meta_title)
        meta_title_row.addWidget(self.clip_count_badge)
        meta_title_row.addStretch(1)
        meta_layout.addLayout(meta_title_row)

        meta_desc = QLabel(
            "將切片拖曳至下方，Mode A 會自動根據 LosslessCut 時間碼後綴逆向溯源原錄播與 XML；"
            "若切片已被重命名為純歌名失去時間戳，可指定原錄播或 XML 進行 Mode C 人機配對。",
            self,
        )
        meta_desc.setStyleSheet("color: #9ca3af; font-size: 12px;")
        meta_desc.setWordWrap(True)
        meta_layout.addWidget(meta_desc)

        # DropZone for clips
        self.clip_drop_zone = DropZone(
            title="拖曳切片影片或資料夾至此",
            hint="支援 .mp4, .flv, .mkv 切片或包含切片的資料夾（點擊可開啟檔案選擇器）",
            allowed_extensions=[".mp4", ".flv", ".mkv", ".ts", ".m4a"],
            allow_directories=True,
            compact=False,
            parent=self,
        )
        self.clip_drop_zone.files_dropped.connect(self._on_clips_dropped)
        meta_layout.addWidget(self.clip_drop_zone)

        # Clip list table
        self.clip_table = QTableWidget(0, 3, self)
        self.clip_table.setHorizontalHeaderLabels(["切片檔名", "檔案大小", "路徑"])
        self.clip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.clip_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.clip_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.clip_table.setMaximumHeight(130)
        meta_layout.addWidget(self.clip_table)

        # Mode C Reference file picker row
        ref_frame = QFrame(self)
        ref_frame.setStyleSheet("background-color: #1e222b; border-radius: 6px; padding: 8px;")
        ref_layout = QVBoxLayout(ref_frame)
        ref_layout.setContentsMargins(6, 6, 6, 6)
        ref_layout.setSpacing(6)

        ref_header = QLabel("指定原錄播影片或錄播姬 .xml 參考檔 (Mode C 人機配對模式，選填):", ref_frame)
        ref_header.setStyleSheet("color: #d1d5db; font-size: 12px; font-weight: 500;")
        ref_layout.addWidget(ref_header)

        ref_input_row = QHBoxLayout()
        ref_input_row.setSpacing(8)
        self.ref_path_edit = QLineEdit(ref_frame)
        self.ref_path_edit.setReadOnly(True)
        self.ref_path_edit.setPlaceholderText("留空時使用 Mode A 自動逆向溯源；若切片已改為純歌名，請指定原錄播大檔或同名 XML...")

        browse_ref_btn = QPushButton("📁 選擇參考檔...", ref_frame)
        browse_ref_btn.setCursor(Qt.PointingHandCursor)
        browse_ref_btn.clicked.connect(self._on_browse_mode_c_ref)

        clear_ref_btn = QPushButton("✕ 清除", ref_frame)
        clear_ref_btn.setCursor(Qt.PointingHandCursor)
        clear_ref_btn.clicked.connect(self._on_clear_mode_c_ref)

        ref_input_row.addWidget(self.ref_path_edit, stretch=1)
        ref_input_row.addWidget(browse_ref_btn)
        ref_input_row.addWidget(clear_ref_btn)
        ref_layout.addLayout(ref_input_row)
        meta_layout.addWidget(ref_frame)

        # Checkboxes row
        opt_check_row = QHBoxLayout()
        opt_check_row.setSpacing(20)

        self.sync_creation_time_check = QCheckBox("同步 Windows 檔案建立時間 (CreationTime)", self)
        self.sync_creation_time_check.setChecked(True)

        self.strip_qc_tags_check = QCheckBox("自動清除檔名品管標籤 ([✅...], [🟡...])", self)
        self.strip_qc_tags_check.setChecked(True)

        opt_check_row.addWidget(self.sync_creation_time_check)
        opt_check_row.addWidget(self.strip_qc_tags_check)
        opt_check_row.addStretch(1)
        meta_layout.addLayout(opt_check_row)

        # Action row for Zone 1
        meta_action_row = QHBoxLayout()
        meta_action_row.setSpacing(10)

        clear_clips_btn = QPushButton("清空切片清單", self)
        clear_clips_btn.setCursor(Qt.PointingHandCursor)
        clear_clips_btn.clicked.connect(self._on_clear_clips)

        self.meta_status_lbl = QLabel("", self)
        self.meta_status_lbl.setStyleSheet("color: #34d399; font-weight: 500;")

        self.run_meta_btn = QPushButton("⚡ 開始修復切片中繼資料與時間戳", self)
        self.run_meta_btn.setObjectName("primaryButton")
        self.run_meta_btn.setCursor(Qt.PointingHandCursor)
        self.run_meta_btn.setMinimumHeight(38)
        self.run_meta_btn.clicked.connect(self._on_start_metadata_repair)

        meta_action_row.addWidget(clear_clips_btn)
        meta_action_row.addWidget(self.meta_status_lbl)
        meta_action_row.addStretch(1)
        meta_action_row.addWidget(self.run_meta_btn)
        meta_layout.addLayout(meta_action_row)

        layout.addWidget(meta_card)

        # =========================================================================
        # Zone 2: Standalone .llc Import
        # =========================================================================
        llc_card = QFrame(self)
        llc_card.setObjectName("cardFrame")
        llc_layout = QVBoxLayout(llc_card)
        llc_layout.setContentsMargins(18, 18, 18, 18)
        llc_layout.setSpacing(12)

        llc_title_row = QHBoxLayout()
        llc_title = QLabel("2. 獨立 LosslessCut 打軸專案匯入 (.llc ➔ 標註庫)", self)
        llc_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")
        self.llc_count_badge = QLabel("0 個專案", self)
        self.llc_count_badge.setStyleSheet(
            "background-color: #2b313c; color: #a78bfa; padding: 2px 8px; border-radius: 4px; font-size: 12px;"
        )
        llc_title_row.addWidget(llc_title)
        llc_title_row.addWidget(self.llc_count_badge)
        llc_title_row.addStretch(1)
        llc_layout.addLayout(llc_title_row)

        llc_desc = QLabel(
            "拖曳任意 *-proj.llc 專案檔或包含 .llc 的資料夾，將微調後的打軸區間批次轉換為人眼 Excel 打軸表 (.打軸表.csv)、"
            "機器訓練檔 (.segments.json) 與 Gold 標準 TSV，反哺模型自監督訓練集。",
            self,
        )
        llc_desc.setStyleSheet("color: #9ca3af; font-size: 12px;")
        llc_desc.setWordWrap(True)
        llc_layout.addWidget(llc_desc)

        # DropZone for .llc
        self.llc_drop_zone = DropZone(
            title="拖曳 .llc 專案檔或包含專案的資料夾至此",
            hint="支援單場或批次 LosslessCut 專案檔 (*.llc, *-proj.llc)",
            allowed_extensions=[".llc", ".json"],
            allow_directories=True,
            compact=False,
            file_dialog_filter="LosslessCut 專案檔 (*.llc *.json);;所有檔案 (*.*)",
            parent=self,
        )
        self.llc_drop_zone.files_dropped.connect(self._on_llc_dropped)
        llc_layout.addWidget(self.llc_drop_zone)

        # LLC list table
        self.llc_table = QTableWidget(0, 3, self)
        self.llc_table.setHorizontalHeaderLabels(["專案檔名", "檔案大小", "路徑"])
        self.llc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.llc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.llc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.llc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.llc_table.setMaximumHeight(120)
        llc_layout.addWidget(self.llc_table)

        # Streamer override row (optional)
        streamer_row = QHBoxLayout()
        streamer_row.setSpacing(8)
        streamer_lbl = QLabel("指定主播歸屬 (選填):", self)
        streamer_lbl.setStyleSheet("color: #d1d5db; min-width: 140px;")
        
        self.streamer_combo = QComboBox(self)
        self.streamer_combo.setEditable(True)
        self.streamer_combo.addItem("自動由檔名/目錄識別 (預設)")
        self.streamer_combo.addItems(["-菫時-", "秋葵", "薇Steria", "随一Suiii"])
        self.streamer_combo.setMinimumWidth(240)

        streamer_row.addWidget(streamer_lbl)
        streamer_row.addWidget(self.streamer_combo)
        streamer_row.addStretch(1)
        llc_layout.addLayout(streamer_row)

        # Action row for Zone 2
        llc_action_row = QHBoxLayout()
        llc_action_row.setSpacing(10)

        clear_llc_btn = QPushButton("清空專案清單", self)
        clear_llc_btn.setCursor(Qt.PointingHandCursor)
        clear_llc_btn.clicked.connect(self._on_clear_llc)

        self.llc_status_lbl = QLabel("", self)
        self.llc_status_lbl.setStyleSheet("color: #a78bfa; font-weight: 500;")

        self.run_llc_btn = QPushButton("📑 匯入打軸專案並生成標註表", self)
        self.run_llc_btn.setObjectName("primaryButton")
        self.run_llc_btn.setCursor(Qt.PointingHandCursor)
        self.run_llc_btn.setMinimumHeight(38)
        self.run_llc_btn.clicked.connect(self._on_start_llc_import)

        llc_action_row.addWidget(clear_llc_btn)
        llc_action_row.addWidget(self.llc_status_lbl)
        llc_action_row.addStretch(1)
        llc_action_row.addWidget(self.run_llc_btn)
        llc_layout.addLayout(llc_action_row)

        layout.addWidget(llc_card)

        layout.addStretch(1)
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    # --- Configuration Sync ---

    def _sync_with_config(self) -> None:
        meta_cfg = self.config.get_metadata_defaults()
        self.sync_creation_time_check.setChecked(meta_cfg.get("sync_creation_time", True))
        self.strip_qc_tags_check.setChecked(meta_cfg.get("strip_qc_tags", True))

    def on_config_updated(self) -> None:
        self._sync_with_config()

    # --- Zone 1 Event Handlers ---

    def _on_clips_dropped(self, paths: list[str]) -> None:
        for p_str in paths:
            p = Path(p_str)
            if not p.exists():
                continue
            if p.is_dir():
                for ext in [".mp4", ".flv", ".mkv", ".ts", ".m4a"]:
                    for f in p.glob(f"*{ext}"):
                        if f not in self._clip_paths:
                            self._clip_paths.append(f)
            elif p.is_file():
                if p not in self._clip_paths:
                    self._clip_paths.append(p)
        self._refresh_clip_table()

    def _refresh_clip_table(self) -> None:
        self.clip_table.setRowCount(len(self._clip_paths))
        for row, path in enumerate(self._clip_paths):
            size_mb = f"{path.stat().st_size / (1024 * 1024):.1f} MB" if path.exists() else "--"
            self.clip_table.setItem(row, 0, QTableWidgetItem(path.name))
            self.clip_table.setItem(row, 1, QTableWidgetItem(size_mb))
            self.clip_table.setItem(row, 2, QTableWidgetItem(str(path.parent)))
        self.clip_count_badge.setText(f"{len(self._clip_paths)} 個切片")

    def _on_clear_clips(self) -> None:
        self._clip_paths.clear()
        self._refresh_clip_table()

    def _on_browse_mode_c_ref(self) -> None:
        initial_dir = str(self._clip_paths[0].parent) if self._clip_paths else str(self.config.project_root)
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "選擇原錄播影片或錄播姬 .xml 參考檔 (Mode C)",
            initial_dir,
            "參考檔案 (*.mp4 *.flv *.mkv *.xml *.llc);;所有檔案 (*.*)",
        )
        if chosen:
            self._mode_c_ref = Path(chosen)
            self.ref_path_edit.setText(chosen)

    def _on_clear_mode_c_ref(self) -> None:
        self._mode_c_ref = None
        self.ref_path_edit.clear()

    def _on_start_metadata_repair(self) -> None:
        if not self._clip_paths:
            self.show_warning("未選取切片", "請先拖入至少一個待修復的影音切片檔案或資料夾。")
            return

        targets: list[str | Path] = []
        if self._mode_c_ref and self._mode_c_ref.exists():
            targets.append(self._mode_c_ref)

        targets.extend(self._clip_paths)

        cmd = CommandBuilder.build_sync_metadata_command(
            repo_root=self.config.project_root,
            target_paths=targets,
        )

        mode_str = f"Mode C (參考: {self._mode_c_ref.name})" if self._mode_c_ref else "Mode A 自動溯源"
        task_title = f"[標籤修復] 共 {len(self._clip_paths)} 個切片 [{mode_str}]"

        task = TaskItem(
            title=task_title,
            command=cmd,
            cwd=str(self.config.project_root),
            metadata={
                "action": "sync_metadata",
                "clip_count": len(self._clip_paths),
                "mode_c": self._mode_c_ref is not None,
            },
        )

        task_id = self.enqueue_task(task)
        self.meta_status_lbl.setText(f"✓ 已排入任務 (ID: {task_id})")
        self._clip_paths.clear()
        self._refresh_clip_table()

    # --- Zone 2 Event Handlers ---

    def _on_llc_dropped(self, paths: list[str]) -> None:
        for p_str in paths:
            p = Path(p_str)
            if not p.exists():
                continue
            if p.is_dir():
                for f in p.glob("**/*.llc"):
                    if f not in self._llc_paths:
                        self._llc_paths.append(f)
            elif p.is_file() and p.suffix.lower() in [".llc", ".json"]:
                if p not in self._llc_paths:
                    self._llc_paths.append(p)
        self._refresh_llc_table()

    def _refresh_llc_table(self) -> None:
        self.llc_table.setRowCount(len(self._llc_paths))
        for row, path in enumerate(self._llc_paths):
            size_kb = f"{path.stat().st_size / 1024:.1f} KB" if path.exists() else "--"
            self.llc_table.setItem(row, 0, QTableWidgetItem(path.name))
            self.llc_table.setItem(row, 1, QTableWidgetItem(size_kb))
            self.llc_table.setItem(row, 2, QTableWidgetItem(str(path.parent)))
        self.llc_count_badge.setText(f"{len(self._llc_paths)} 個專案")

    def _on_clear_llc(self) -> None:
        self._llc_paths.clear()
        self._refresh_llc_table()

    def _on_start_llc_import(self) -> None:
        if not self._llc_paths:
            self.show_warning("未選取專案檔", "請先拖入至少一個 LosslessCut .llc 專案檔或資料夾。")
            return

        cmd = CommandBuilder.build_import_llc_command(
            repo_root=self.config.project_root,
            target_paths=self._llc_paths,
        )

        streamer_sel = self.streamer_combo.currentText().strip()
        task_title = f"[匯入打軸] 共 {len(self._llc_paths)} 個 .llc 專案 [{streamer_sel}]"

        task = TaskItem(
            title=task_title,
            command=cmd,
            cwd=str(self.config.project_root),
            metadata={
                "action": "import_llc",
                "llc_count": len(self._llc_paths),
                "streamer": streamer_sel,
            },
        )

        task_id = self.enqueue_task(task)
        self.llc_status_lbl.setText(f"✓ 已排入任務 (ID: {task_id})")
        self._llc_paths.clear()
        self._refresh_llc_table()
