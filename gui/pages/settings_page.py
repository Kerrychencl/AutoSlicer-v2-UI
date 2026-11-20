"""Page 4: Global Settings Page for AutoSlicer Desktop Workbench.

Features:
- Watch directories management (add, remove, toggle, browse)
- Path configurations for LosslessCut executable, runs_dir, annotations_dir, and default_download_root
- Auto-detection helper for LosslessCut.exe
- Streamer-to-model mapping table scanning runs/ for best.pt checkpoints with status indicators
- Bilibili SESSDATA masked input with show/hide toggle
- Inference and Metadata default parameters configuration
- 'Open config.json in Editor' button
- Full Save & Reset capabilities with automatic signal emissions
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import WatchDirConfig
from ..page_registry import register_page
from .base_page import BasePage


@register_page(page_id="settings", title="全域設定", icon_name="settings", order=40)
class SettingsPage(BasePage):
    """Page 4: 全域設定表單工作台."""

    page_id: str = "settings"
    title: str = "全域設定"
    icon_name: str = "settings"
    enabled: bool = True

    def __init__(self, config_manager=None, queue_manager=None, parent=None) -> None:
        super().__init__(config_manager, queue_manager, parent)
        self._watch_dirs: list[WatchDirConfig] = []
        self._available_models: dict[str, Path] = {}

        self._init_ui()
        self._load_from_config()

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

        # --- Top Header ---
        header_row = QHBoxLayout()
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title_lbl = QLabel("全域設定 (Global Settings)", self)
        title_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #f3f4f6;")
        desc_lbl = QLabel(
            "管理錄播監控資料夾、外部工具路徑、專屬主播 AI 模型對照表與 B站連線設定，設定儲存於 config.json。",
            self,
        )
        desc_lbl.setStyleSheet("color: #9ca3af; font-size: 13px;")
        desc_lbl.setWordWrap(True)
        header_box.addWidget(title_lbl)
        header_box.addWidget(desc_lbl)

        open_json_btn = QPushButton("📝 在系統編輯器開啟 config.json", self)
        open_json_btn.setCursor(Qt.PointingHandCursor)
        open_json_btn.clicked.connect(self._on_open_config_json)

        header_row.addLayout(header_box, stretch=1)
        header_row.addWidget(open_json_btn)
        layout.addLayout(header_row)

        # =========================================================================
        # Section 1: Watch Directories Management
        # =========================================================================
        watch_card = QFrame(self)
        watch_card.setObjectName("cardFrame")
        watch_layout = QVBoxLayout(watch_card)
        watch_layout.setContentsMargins(18, 18, 18, 18)
        watch_layout.setSpacing(12)

        watch_header_row = QHBoxLayout()
        watch_title = QLabel("1. 錄播監控根目錄管理 (Watch Directories)", self)
        watch_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")

        add_watch_btn = QPushButton("➕ 新增監控資料夾", self)
        add_watch_btn.setCursor(Qt.PointingHandCursor)
        add_watch_btn.clicked.connect(self._on_add_watch_dir)

        watch_header_row.addWidget(watch_title)
        watch_header_row.addStretch(1)
        watch_header_row.addWidget(add_watch_btn)
        watch_layout.addLayout(watch_header_row)

        watch_tip = QLabel(
            "💡 提示：監控目錄下預期按「主播名稱」分子資料夾（例如：recordings/秋葵/、recordings/-菫時-/）。"
            "看板掃描時將自動聚合所有已啟用的目錄。",
            self,
        )
        watch_tip.setStyleSheet("color: #9ca3af; font-size: 12px;")
        watch_tip.setWordWrap(True)
        watch_layout.addWidget(watch_tip)

        self.watch_table = QTableWidget(0, 4, self)
        self.watch_table.setHorizontalHeaderLabels(["啟用", "目錄標籤", "本機絕對路徑", "操作"])
        self.watch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.watch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.watch_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.watch_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.watch_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.watch_table.setMinimumHeight(120)
        self.watch_table.setMaximumHeight(180)
        watch_layout.addWidget(self.watch_table)

        layout.addWidget(watch_card)

        # =========================================================================
        # Section 2: External Paths Configuration
        # =========================================================================
        paths_card = QFrame(self)
        paths_card.setObjectName("cardFrame")
        paths_layout = QVBoxLayout(paths_card)
        paths_layout.setContentsMargins(18, 18, 18, 18)
        paths_layout.setSpacing(14)

        paths_title = QLabel("2. 核心路徑與工具設定 (Core Paths)", self)
        paths_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")
        paths_layout.addWidget(paths_title)

        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setColumnStretch(1, 1)

        # LosslessCut Exe Row
        grid.addWidget(QLabel("LosslessCut 執行檔:"), 0, 0)
        self.llc_exe_edit = QLineEdit(self)
        self.llc_exe_edit.setPlaceholderText("留空時自動探測常見安裝路徑或 PATH...")
        self.llc_exe_edit.textChanged.connect(self._check_llc_exe_status)
        grid.addWidget(self.llc_exe_edit, 0, 1)

        llc_btn_row = QHBoxLayout()
        llc_btn_row.setSpacing(6)
        browse_llc_btn = QPushButton("📁 瀏覽...", self)
        browse_llc_btn.setCursor(Qt.PointingHandCursor)
        browse_llc_btn.clicked.connect(self._on_browse_llc_exe)
        auto_llc_btn = QPushButton("🔍 自動探測", self)
        auto_llc_btn.setCursor(Qt.PointingHandCursor)
        auto_llc_btn.clicked.connect(self._on_autodetect_llc)
        llc_btn_row.addWidget(browse_llc_btn)
        llc_btn_row.addWidget(auto_llc_btn)
        grid.addLayout(llc_btn_row, 0, 2)

        # LLC Status
        self.llc_status_lbl = QLabel("", self)
        self.llc_status_lbl.setStyleSheet("font-size: 12px; margin-left: 2px;")
        grid.addWidget(self.llc_status_lbl, 1, 1, 1, 2)

        # Runs dir
        grid.addWidget(QLabel("模型庫目錄 (runs_dir):"), 2, 0)
        self.runs_dir_edit = QLineEdit(self)
        self.runs_dir_edit.setPlaceholderText("預設為專案目錄下 runs")
        grid.addWidget(self.runs_dir_edit, 2, 1)
        browse_runs_btn = QPushButton("📁 瀏覽...", self)
        browse_runs_btn.setCursor(Qt.PointingHandCursor)
        browse_runs_btn.clicked.connect(self._on_browse_runs_dir)
        grid.addWidget(browse_runs_btn, 2, 2)

        # Annotations dir
        grid.addWidget(QLabel("標註庫目錄 (annotations_dir):"), 3, 0)
        self.ann_dir_edit = QLineEdit(self)
        self.ann_dir_edit.setPlaceholderText("預設為專案目錄下 data/annotations")
        grid.addWidget(self.ann_dir_edit, 3, 1)
        browse_ann_btn = QPushButton("📁 瀏覽...", self)
        browse_ann_btn.setCursor(Qt.PointingHandCursor)
        browse_ann_btn.clicked.connect(self._on_browse_ann_dir)
        grid.addWidget(browse_ann_btn, 3, 2)

        # Default download root
        grid.addWidget(QLabel("預設下載落地目錄:"), 4, 0)
        self.down_root_edit = QLineEdit(self)
        self.down_root_edit.setPlaceholderText("留空時自動採用第一個啟用的監控目錄")
        grid.addWidget(self.down_root_edit, 4, 1)
        browse_down_btn = QPushButton("📁 瀏覽...", self)
        browse_down_btn.setCursor(Qt.PointingHandCursor)
        browse_down_btn.clicked.connect(self._on_browse_down_root)
        grid.addWidget(browse_down_btn, 4, 2)

        paths_layout.addLayout(grid)
        layout.addWidget(paths_card)

        # =========================================================================
        # Section 3: Streamer-to-Model Mapping Table
        # =========================================================================
        model_card = QFrame(self)
        model_card.setObjectName("cardFrame")
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(18, 18, 18, 18)
        model_layout.setSpacing(12)

        model_title_row = QHBoxLayout()
        model_title = QLabel("3. 主播專屬 AI 模型對照表 (Streamer-to-Model Mapping)", self)
        model_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")

        rescan_models_btn = QPushButton("🔄 重新掃描模型目錄", self)
        rescan_models_btn.setCursor(Qt.PointingHandCursor)
        rescan_models_btn.clicked.connect(self._on_rescan_models)

        add_streamer_btn = QPushButton("➕ 新增主播項", self)
        add_streamer_btn.setCursor(Qt.PointingHandCursor)
        add_streamer_btn.clicked.connect(self._on_add_streamer_mapping)

        model_title_row.addWidget(model_title)
        model_title_row.addStretch(1)
        model_title_row.addWidget(rescan_models_btn)
        model_title_row.addWidget(add_streamer_btn)
        model_layout.addLayout(model_title_row)

        model_tip = QLabel(
            "系統已在 runs/ 中探測到各主播最佳模型 (best.pt)。在此可手動指定或切換至特定主播模型，"
            "留空時將自動優先採用 runs/<主播>/best.pt，次選專案根目錄官方預設 best.pt。",
            self,
        )
        model_tip.setStyleSheet("color: #9ca3af; font-size: 12px;")
        model_tip.setWordWrap(True)
        model_layout.addWidget(model_tip)

        self.model_table = QTableWidget(0, 3, self)
        self.model_table.setHorizontalHeaderLabels(["主播名稱", "綁定模型權重檔 (best.pt)", "模型檔案狀態"])
        self.model_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.model_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.model_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.model_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.model_table.setMinimumHeight(160)
        self.model_table.setMaximumHeight(220)
        model_layout.addWidget(self.model_table)

        layout.addWidget(model_card)

        # =========================================================================
        # Section 4: Bilibili & Cookie Settings
        # =========================================================================
        bili_card = QFrame(self)
        bili_card.setObjectName("cardFrame")
        bili_layout = QVBoxLayout(bili_card)
        bili_layout.setContentsMargins(18, 18, 18, 18)
        bili_layout.setSpacing(12)

        bili_title = QLabel("4. B站下載與連線設定 (Bilibili Settings)", self)
        bili_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")
        bili_layout.addWidget(bili_title)

        sess_row = QHBoxLayout()
        sess_row.setSpacing(8)
        sess_lbl = QLabel("全域 SESSDATA Cookie:")
        sess_lbl.setStyleSheet("min-width: 140px;")
        self.bili_sess_edit = QLineEdit(self)
        self.bili_sess_edit.setEchoMode(QLineEdit.Password)
        self.bili_sess_edit.setPlaceholderText("選填，填入可解鎖 1080P+ 高畫質下載（免登入預設最高 192kbps 音訊與 480p 影片）")

        self.bili_sess_toggle_btn = QPushButton("👁 顯示", self)
        self.bili_sess_toggle_btn.setFixedWidth(68)
        self.bili_sess_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.bili_sess_toggle_btn.clicked.connect(self._toggle_bili_sess_visibility)

        sess_row.addWidget(sess_lbl)
        sess_row.addWidget(self.bili_sess_edit, stretch=1)
        sess_row.addWidget(self.bili_sess_toggle_btn)
        bili_layout.addLayout(sess_row)

        bili_cookie_hint = QLabel(
            "🔒 安全提醒：SESSDATA 僅儲存於本機 config.json，絕不外傳或同步。命令列執行與日誌輸出時自動遮蔽。",
            self,
        )
        bili_cookie_hint.setStyleSheet("color: #8b949e; font-size: 12px; margin-left: 148px;")
        bili_layout.addWidget(bili_cookie_hint)

        self.prefer_video_check = QCheckBox("預設輸出完整影片 (.mp4) 而非純音訊 (.m4a)", self)
        bili_layout.addWidget(self.prefer_video_check)

        layout.addWidget(bili_card)

        # =========================================================================
        # Section 5: Default Parameters (Inference & Metadata)
        # =========================================================================
        defaults_card = QFrame(self)
        defaults_card.setObjectName("cardFrame")
        defaults_layout = QVBoxLayout(defaults_card)
        defaults_layout.setContentsMargins(18, 18, 18, 18)
        defaults_layout.setSpacing(12)

        defaults_title = QLabel("5. 切歌推論與中繼資料預設參數 (Algorithm Defaults)", self)
        defaults_title.setStyleSheet("font-size: 15px; font-weight: 600; color: #e5e7eb;")
        defaults_layout.addWidget(defaults_title)

        param_grid = QGridLayout()
        param_grid.setSpacing(12)

        # On threshold
        param_grid.addWidget(QLabel("進入門檻 (on_threshold):"), 0, 0)
        self.on_thresh_spin = QDoubleSpinBox(self)
        self.on_thresh_spin.setRange(0.05, 0.99)
        self.on_thresh_spin.setSingleStep(0.05)
        self.on_thresh_spin.setValue(0.60)
        param_grid.addWidget(self.on_thresh_spin, 0, 1)

        # Off threshold
        param_grid.addWidget(QLabel("離開門檻 (off_threshold):"), 0, 2)
        self.off_thresh_spin = QDoubleSpinBox(self)
        self.off_thresh_spin.setRange(0.05, 0.99)
        self.off_thresh_spin.setSingleStep(0.05)
        self.off_thresh_spin.setValue(0.40)
        param_grid.addWidget(self.off_thresh_spin, 0, 3)

        # Merge gap
        param_grid.addWidget(QLabel("間奏合併門檻 (merge_gap_s):"), 1, 0)
        self.merge_gap_spin = QSpinBox(self)
        self.merge_gap_spin.setRange(5, 600)
        self.merge_gap_spin.setValue(90)
        self.merge_gap_spin.setSuffix(" 秒")
        param_grid.addWidget(self.merge_gap_spin, 1, 1)

        # Min song duration
        param_grid.addWidget(QLabel("最短歌曲時長 (min_song_s):"), 1, 2)
        self.min_song_spin = QSpinBox(self)
        self.min_song_spin.setRange(5, 300)
        self.min_song_spin.setValue(50)
        self.min_song_spin.setSuffix(" 秒")
        param_grid.addWidget(self.min_song_spin, 1, 3)

        # Pad s
        param_grid.addWidget(QLabel("前後緩衝補齊 (pad_s):"), 2, 0)
        self.pad_spin = QSpinBox(self)
        self.pad_spin.setRange(0, 30)
        self.pad_spin.setValue(5)
        self.pad_spin.setSuffix(" 秒")
        param_grid.addWidget(self.pad_spin, 2, 1)

        defaults_layout.addLayout(param_grid)
        layout.addWidget(defaults_card)

        # =========================================================================
        # Bottom Save & Reset Row
        # =========================================================================
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 8, 0, 0)
        bottom_row.setSpacing(12)

        self.save_status_lbl = QLabel("", self)
        self.save_status_lbl.setStyleSheet("color: #34d399; font-weight: 500;")

        reset_btn = QPushButton("還原預設設定", self)
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._on_reset_defaults)

        self.save_btn = QPushButton("💾 儲存所有設定", self)
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setMinimumHeight(40)
        self.save_btn.setMinimumWidth(160)
        self.save_btn.clicked.connect(self._on_save_settings)

        bottom_row.addWidget(self.save_status_lbl)
        bottom_row.addStretch(1)
        bottom_row.addWidget(reset_btn)
        bottom_row.addWidget(self.save_btn)
        layout.addLayout(bottom_row)

        layout.addStretch(1)
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    # --- Load & Sync Methods ---

    def on_page_activated(self) -> None:
        self._load_from_config()

    def on_config_updated(self) -> None:
        self._load_from_config()

    def _load_from_config(self) -> None:
        """Load current settings from global ConfigManager and populate UI widgets."""
        # 1. Watch Dirs
        self._watch_dirs = self.config.get_watch_dirs(only_enabled=False)
        self._refresh_watch_dirs_table()

        # 2. Paths
        paths = self.config.get("paths", {})
        self.llc_exe_edit.setText(paths.get("losslesscut_exe", ""))
        self.runs_dir_edit.setText(paths.get("runs_dir", "runs"))
        self.ann_dir_edit.setText(paths.get("annotations_dir", "data/annotations"))
        self.down_root_edit.setText(paths.get("default_download_root", ""))
        self._check_llc_exe_status(self.llc_exe_edit.text())

        # 3. Available Models & Mapping
        self._available_models = self.config.scan_available_models()
        self._refresh_model_mapping_table()

        # 4. Bilibili
        bili = self.config.get_bilibili_config()
        self.bili_sess_edit.setText(bili.get("sessdata", ""))
        self.prefer_video_check.setChecked(bili.get("prefer_video", False))

        # 5. Defaults
        inf = self.config.get_inference_defaults()
        self.on_thresh_spin.setValue(inf.get("on_threshold", 0.60))
        self.off_thresh_spin.setValue(inf.get("off_threshold", 0.40))
        self.merge_gap_spin.setValue(int(inf.get("merge_gap_s", 90)))
        self.min_song_spin.setValue(int(inf.get("min_song_s", 50)))
        self.pad_spin.setValue(int(inf.get("pad_s", 5)))

    # --- Watch Directories Table Handling ---

    def _refresh_watch_dirs_table(self) -> None:
        self.watch_table.setRowCount(len(self._watch_dirs))
        for row, wd in enumerate(self._watch_dirs):
            # Checkbox item for enabled
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Checked if wd.enabled else Qt.Unchecked)
            self.watch_table.setItem(row, 0, chk_item)

            # Label item
            lbl_item = QTableWidgetItem(wd.label)
            self.watch_table.setItem(row, 1, lbl_item)

            # Path item
            p_exists = Path(wd.path).exists()
            path_item = QTableWidgetItem(wd.path)
            if not p_exists:
                path_item.setForeground(Qt.red)
                path_item.setToolTip("路徑不存在，請檢查硬碟或資料夾")
            self.watch_table.setItem(row, 2, path_item)

            # Action button
            del_btn = QPushButton("移除", self)
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setProperty("row", row)
            del_btn.clicked.connect(lambda _, r=row: self._on_remove_watch_dir(r))
            self.watch_table.setCellWidget(row, 3, del_btn)

    def _on_add_watch_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "選擇新的錄播監控資料夾")
        if not chosen:
            return

        default_label = Path(chosen).name or "錄播目錄"
        label, ok = QInputDialog.getText(
            self,
            "輸入目錄標籤",
            "請為此錄播監控資料夾命名標籤 (例如：二號硬碟錄播):",
            text=default_label,
        )
        if not ok or not label.strip():
            label = default_label

        self._watch_dirs.append(WatchDirConfig(label=label.strip(), path=chosen, enabled=True))
        self._refresh_watch_dirs_table()

    def _on_remove_watch_dir(self, row: int) -> None:
        if 0 <= row < len(self._watch_dirs):
            wd = self._watch_dirs[row]
            if self.show_confirm("確認移除", f"確定要從監控清單移除「{wd.label}」嗎？\n路徑: {wd.path}"):
                self._watch_dirs.pop(row)
                self._refresh_watch_dirs_table()

    # --- LosslessCut and Path Handlers ---

    def _check_llc_exe_status(self, text: str) -> None:
        raw = text.strip()
        if raw and Path(raw).is_file():
            self.llc_status_lbl.setText("✓ 已設定有效執行檔")
            self.llc_status_lbl.setStyleSheet("color: #34d399; font-size: 12px;")
        else:
            detected = self.config.find_losslesscut_exe()
            if detected and detected.is_file():
                self.llc_status_lbl.setText(f"✓ 系統自動探測到: {detected}")
                self.llc_status_lbl.setStyleSheet("color: #60a5fa; font-size: 12px;")
            else:
                self.llc_status_lbl.setText("✕ 尚未設定且未探測到 LosslessCut.exe，可手動指定")
                self.llc_status_lbl.setStyleSheet("color: #f87171; font-size: 12px;")

    def _on_browse_llc_exe(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "選擇 LosslessCut 執行檔",
            "",
            "LosslessCut (LosslessCut.exe);;可執行檔 (*.exe);;所有檔案 (*.*)",
        )
        if chosen:
            self.llc_exe_edit.setText(chosen)

    def _on_autodetect_llc(self) -> None:
        detected = self.config.find_losslesscut_exe()
        if detected and detected.is_file():
            self.llc_exe_edit.setText(str(detected))
            self.show_info("探測成功", f"成功找到 LosslessCut:\n{detected}")
        else:
            self.show_warning("未探測到", "無法在常見路徑或環境變數 PATH 中找到 LosslessCut.exe，請手動點擊「瀏覽」指定。")

    def _on_browse_runs_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "選擇模型權重目錄 (runs)", self.runs_dir_edit.text())
        if chosen:
            self.runs_dir_edit.setText(chosen)

    def _on_browse_ann_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "選擇標註庫目錄 (data/annotations)", self.ann_dir_edit.text())
        if chosen:
            self.ann_dir_edit.setText(chosen)

    def _on_browse_down_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "選擇預設下載落地目錄", self.down_root_edit.text())
        if chosen:
            self.down_root_edit.setText(chosen)

    # --- Streamer-to-Model Mapping Table Handling ---

    def _on_rescan_models(self) -> None:
        self._available_models = self.config.scan_available_models()
        self._refresh_model_mapping_table()
        self.show_info("掃描完成", f"在模型庫中成功探測到 {len(self._available_models)} 個模型權重檔。")

    def _on_add_streamer_mapping(self) -> None:
        name, ok = QInputDialog.getText(self, "新增主播對照", "請輸入主播名稱 (例如：阿梓、新主播名):")
        if ok and name.strip():
            streamer_name = name.strip()
            cur_map = self.config.get("streamer_model_map", {})
            if streamer_name not in cur_map:
                cur_map[streamer_name] = ""
                self.config.set("streamer_model_map", cur_map, auto_save=False)
                self._refresh_model_mapping_table()

    def _refresh_model_mapping_table(self) -> None:
        streamer_map = self.config.get("streamer_model_map", {})

        # Ensure canonical streamers are in the list
        all_streamers = ["-菫時-", "秋葵", "薇Steria", "随一Suiii", "_default"]
        for s in streamer_map.keys():
            if s not in all_streamers:
                all_streamers.append(s)

        self.model_table.setRowCount(len(all_streamers))

        for row, streamer in enumerate(all_streamers):
            # Column 0: Streamer name
            name_display = "預設回退模型 (_default)" if streamer == "_default" else streamer
            self.model_table.setItem(row, 0, QTableWidgetItem(name_display))

            # Column 1: Model ComboBox
            combo = QComboBox(self)
            combo.addItem("(自動探測 runs/<主播>/best.pt)", "")
            for model_name, model_path in self._available_models.items():
                combo.addItem(f"{model_name} [{model_path.name}]", str(model_path))

            configured_path = streamer_map.get(streamer, "")
            if configured_path:
                idx = combo.findData(configured_path)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.addItem(f"自訂: {configured_path}", configured_path)
                    combo.setCurrentIndex(combo.count() - 1)
            else:
                combo.setCurrentIndex(0)

            combo.setProperty("streamer", streamer)
            combo.currentIndexChanged.connect(self._on_model_combo_changed)
            self.model_table.setCellWidget(row, 1, combo)

            # Column 2: Status
            resolved_model = self.config.get_model_path_for_streamer(streamer)
            status_item = QTableWidgetItem()
            if resolved_model and resolved_model.is_file():
                sz_mb = resolved_model.stat().st_size / (1024 * 1024)
                status_item.setText(f"✓ 存在 ({sz_mb:.1f} MB)")
                status_item.setForeground(Qt.darkGreen)
                status_item.setToolTip(f"實體路徑: {resolved_model}")
            else:
                status_item.setText("✕ 未找到權重")
                status_item.setForeground(Qt.red)
                status_item.setToolTip("未能找到對應的 best.pt 權重檔")
            self.model_table.setItem(row, 2, status_item)

    def _on_model_combo_changed(self) -> None:
        # Re-check status indicators
        for row in range(self.model_table.rowCount()):
            combo = self.model_table.cellWidget(row, 1)
            if isinstance(combo, QComboBox):
                streamer = combo.property("streamer")
                if not streamer:
                    streamer_item = self.model_table.item(row, 0)
                    streamer = streamer_item.text() if streamer_item else ""
                selected_data = combo.currentData()
                status_item = self.model_table.item(row, 2)
                if not status_item:
                    continue
                if selected_data and Path(selected_data).is_file():
                    status_item.setText("✓ 存在 (指定)")
                    status_item.setForeground(Qt.darkGreen)
                elif not selected_data:
                    res = self.config.get_model_path_for_streamer(streamer)
                    if res and res.is_file():
                        status_item.setText("✓ 自動探測")
                        status_item.setForeground(Qt.darkGreen)
                    else:
                        status_item.setText("✕ 未找到權重")
                        status_item.setForeground(Qt.red)

    # --- Bilibili Visibility Toggle ---

    def _toggle_bili_sess_visibility(self) -> None:
        if self.bili_sess_edit.echoMode() == QLineEdit.Password:
            self.bili_sess_edit.setEchoMode(QLineEdit.Normal)
            self.bili_sess_toggle_btn.setText("🙈 隱藏")
        else:
            self.bili_sess_edit.setEchoMode(QLineEdit.Password)
            self.bili_sess_toggle_btn.setText("👁 顯示")

    # --- Action Handlers ---

    def _on_open_config_json(self) -> None:
        path = self.config.config_path.resolve()
        if not path.exists():
            self.config.save()

        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as e:
            self.show_error("開啟失敗", f"無法開啟 {path}:\n{e}")

    def _on_reset_defaults(self) -> None:
        if self.show_confirm("確認還原", "確定要將推論與演算法參數還原為官方預設值嗎？"):
            self.on_thresh_spin.setValue(0.60)
            self.off_thresh_spin.setValue(0.40)
            self.merge_gap_spin.setValue(90)
            self.min_song_spin.setValue(50)
            self.pad_spin.setValue(5)
            self.save_status_lbl.setText("參數已重置為預設值，請點擊「儲存」以生效。")

    def _on_save_settings(self) -> None:
        """Gather values from all controls and save to config.json."""
        # 1. Watch Dirs
        updated_wds: list[WatchDirConfig] = []
        for row in range(self.watch_table.rowCount()):
            chk = self.watch_table.item(row, 0)
            lbl = self.watch_table.item(row, 1)
            p_item = self.watch_table.item(row, 2)
            if chk and lbl and p_item:
                is_en = chk.checkState() == Qt.Checked
                updated_wds.append(WatchDirConfig(label=lbl.text().strip(), path=p_item.text().strip(), enabled=is_en))
        self.config.set_watch_dirs(updated_wds, auto_save=False)

        # 2. Paths
        paths = self.config.get("paths", {})
        llc_val = self.llc_exe_edit.text().strip()
        if hasattr(self.config, "set_losslesscut_exe"):
            self.config.set_losslesscut_exe(llc_val, auto_save=False)
        else:
            paths["losslesscut_exe"] = llc_val
        paths["runs_dir"] = self.runs_dir_edit.text().strip() or "runs"
        paths["annotations_dir"] = self.ann_dir_edit.text().strip() or "data/annotations"
        paths["default_download_root"] = self.down_root_edit.text().strip()
        self.config.set("paths", paths, auto_save=False)

        # 3. Streamer Model Map
        model_map: dict[str, str] = {}
        for row in range(self.model_table.rowCount()):
            combo = self.model_table.cellWidget(row, 1)
            if isinstance(combo, QComboBox):
                streamer = combo.property("streamer")
                data_val = combo.currentData() or ""
                if streamer:
                    model_map[streamer] = data_val
        self.config.set("streamer_model_map", model_map, auto_save=False)

        # 4. Bilibili
        bili = self.config.get_bilibili_config()
        bili["sessdata"] = self.bili_sess_edit.text().strip()
        bili["prefer_video"] = self.prefer_video_check.isChecked()
        self.config.set("bilibili", bili, auto_save=False)

        # 5. Inference Defaults
        inf = self.config.get_inference_defaults()
        inf["on_threshold"] = round(self.on_thresh_spin.value(), 2)
        inf["off_threshold"] = round(self.off_thresh_spin.value(), 2)
        inf["merge_gap_s"] = int(self.merge_gap_spin.value())
        inf["min_song_s"] = int(self.min_song_spin.value())
        inf["pad_s"] = int(self.pad_spin.value())
        self.config.set("inference_defaults", inf, auto_save=False)

        # Commit to disk
        self.config.save()
        self.save_status_lbl.setText("✓ 設定已成功儲存至 config.json！")
