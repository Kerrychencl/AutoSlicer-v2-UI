"""Page 2: Bilibili VOD Downloader Page for AutoSlicer Desktop Workbench.

Features:
- Multi-line input supporting URLs, BV IDs, av IDs, b23.tv short links
- Streamer directory target selector scanning watch directories + custom folder picker
- Audio (.m4a) vs Video (.mp4) format selector
- Collapsible advanced settings (multi-part page selection & SESSDATA override)
- Builds and enqueues commands calling src/bilibili.py into FIFO queue
- Auto-rescan signal trigger upon task completion
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import TaskItem
from ..page_registry import register_page
from ..runner import CommandBuilder
from ..widgets.collapsible import CollapsibleBox
from .base_page import BasePage

URL_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10}|av\d+|b23\.tv/\S+|bilibili\.com/video/\S+)", re.IGNORECASE)


@register_page(page_id="download", title="B站下載", icon_name="download", order=20)
class DownloadPage(BasePage):
    """Page 2: B站錄播直接解析與下載工作台."""

    page_id: str = "download"
    title: str = "B站下載"
    icon_name: str = "download"
    enabled: bool = True

    # Signal emitted when a download task finishes to trigger Dashboard rescan
    download_completed = Signal(str)

    def __init__(self, config_manager=None, queue_manager=None, parent=None) -> None:
        super().__init__(config_manager, queue_manager, parent)
        self._init_ui()
        self._refresh_streamer_targets()
        self._sync_with_config()

        # Connect task completed signal to handle auto-rescan trigger
        self.queue_manager.task_completed.connect(self._on_task_finished)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll Area
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setObjectName("pageScrollArea")

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        # --- Top Header ---
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title_lbl = QLabel("B站錄播直接下載", self)
        title_lbl.setStyleSheet("font-size: 20px; font-weight: bold; color: #f3f4f6;")
        desc_lbl = QLabel(
            "免第三方軟體，直接解析最高音質 DASH 音訊 (192kbps) 或高畫質影片，支援多行批次排隊下載並自動分類至主播目錄。",
            self,
        )
        desc_lbl.setStyleSheet("color: #9ca3af; font-size: 13px;")
        desc_lbl.setWordWrap(True)
        header_box.addWidget(title_lbl)
        header_box.addWidget(desc_lbl)
        layout.addLayout(header_box)

        # --- Card 1: Input URLs ---
        input_card = QFrame(self)
        input_card.setObjectName("cardFrame")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 16, 16, 16)
        input_layout.setSpacing(10)

        card1_top = QHBoxLayout()
        card1_title = QLabel("下載連結與識別碼 (支援多行貼上)", self)
        card1_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #e5e7eb;")
        self.count_badge = QLabel("待下載: 0 筆", self)
        self.count_badge.setStyleSheet(
            "background-color: #2b313c; color: #60a5fa; padding: 2px 8px; border-radius: 4px; font-size: 12px;"
        )

        paste_btn = QPushButton("📋 貼上剪貼簿", self)
        paste_btn.setCursor(Qt.PointingHandCursor)
        paste_btn.clicked.connect(self._on_paste_clipboard)

        clear_btn = QPushButton("🗑️ 清空", self)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_input)

        card1_top.addWidget(card1_title)
        card1_top.addWidget(self.count_badge)
        card1_top.addStretch(1)
        card1_top.addWidget(paste_btn)
        card1_top.addWidget(clear_btn)
        input_layout.addLayout(card1_top)

        self.url_text_edit = QPlainTextEdit(self)
        self.url_text_edit.setPlaceholderText(
            "請在此輸入或貼上 B站連結（每行一個），支援：\n"
            "  • 完整影片連結：https://www.bilibili.com/video/BV1xx411c7md\n"
            "  • BV 號：BV1xx411c7md\n"
            "  • av 號：av170001\n"
            "  • b23.tv 短網址：https://b23.tv/example\n"
            "  • 分 P 網址：https://www.bilibili.com/video/BV1xx411c7md?p=2"
        )
        self.url_text_edit.setMinimumHeight(130)
        self.url_text_edit.textChanged.connect(self._on_text_changed)
        input_layout.addWidget(self.url_text_edit)
        layout.addWidget(input_card)

        # --- Card 2: Destination & Format ---
        opt_card = QFrame(self)
        opt_card.setObjectName("cardFrame")
        opt_layout = QVBoxLayout(opt_card)
        opt_layout.setContentsMargins(16, 16, 16, 16)
        opt_layout.setSpacing(12)

        card2_title = QLabel("下載落地目錄與檔案格式", self)
        card2_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #e5e7eb;")
        opt_layout.addWidget(card2_title)

        # Target folder row
        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_lbl = QLabel("目標主播 / 資料夾:", self)
        target_lbl.setStyleSheet("color: #d1d5db; min-width: 120px;")
        
        self.target_combo = QComboBox(self)
        self.target_combo.setEditable(True)
        self.target_combo.setMinimumWidth(260)
        self.target_combo.currentTextChanged.connect(self._update_resolved_path_label)

        browse_dir_btn = QPushButton("📁 選擇自訂資料夾...", self)
        browse_dir_btn.setCursor(Qt.PointingHandCursor)
        browse_dir_btn.clicked.connect(self._on_browse_target_dir)

        target_row.addWidget(target_lbl)
        target_row.addWidget(self.target_combo, stretch=1)
        target_row.addWidget(browse_dir_btn)
        opt_layout.addLayout(target_row)

        # Resolved path info
        self.resolved_path_lbl = QLabel("", self)
        self.resolved_path_lbl.setStyleSheet("color: #9ca3af; font-size: 12px; margin-left: 128px;")
        opt_layout.addWidget(self.resolved_path_lbl)

        # Format Radio group
        format_row = QHBoxLayout()
        format_row.setSpacing(16)
        format_lbl = QLabel("輸出檔案格式:", self)
        format_lbl.setStyleSheet("color: #d1d5db; min-width: 120px;")

        self.radio_audio = QRadioButton("🎵 純音訊 (.m4a) — [預設推薦，極速無損，最適合切歌分析]", self)
        self.radio_video = QRadioButton("🎬 完整影片 (.mp4) — [自動下載高畫質影像並封裝]", self)
        self.radio_audio.setChecked(True)

        self.format_group = QButtonGroup(self)
        self.format_group.addButton(self.radio_audio)
        self.format_group.addButton(self.radio_video)

        format_row.addWidget(format_lbl)
        format_row.addWidget(self.radio_audio)
        format_row.addWidget(self.radio_video)
        format_row.addStretch(1)
        opt_layout.addLayout(format_row)

        layout.addWidget(opt_card)

        # --- Card 3: Collapsible Advanced Settings ---
        self.adv_box = CollapsibleBox(title="進階參數 (特定分 P 與 SESSDATA Cookie 覆寫)", expanded=False, parent=self)
        adv_content_layout = QVBoxLayout()
        adv_content_layout.setContentsMargins(8, 8, 8, 8)
        adv_content_layout.setSpacing(12)

        # Multi-part page row
        page_row = QHBoxLayout()
        page_lbl = QLabel("指定分 P 序號:", self)
        page_lbl.setStyleSheet("color: #d1d5db; min-width: 120px;")
        self.page_spin = QSpinBox(self)
        self.page_spin.setRange(0, 999)
        self.page_spin.setValue(0)
        self.page_spin.setSpecialValueText("0 (預設依網址或第 1 P)")
        self.page_spin.setFixedWidth(180)
        page_hint = QLabel("若網址中已包含 ?p=N 則自動優先採用", self)
        page_hint.setStyleSheet("color: #8b949e; font-size: 12px;")
        page_row.addWidget(page_lbl)
        page_row.addWidget(self.page_spin)
        page_row.addWidget(page_hint)
        page_row.addStretch(1)
        adv_content_layout.addLayout(page_row)

        # SESSDATA row
        sess_layout = QVBoxLayout()
        sess_layout.setSpacing(4)
        
        sess_check_row = QHBoxLayout()
        self.override_sess_check = QCheckBox("單次覆寫全域 SESSDATA Cookie (解鎖 1080P+ 高畫質)", self)
        self.override_sess_check.toggled.connect(self._on_override_sess_toggled)
        sess_check_row.addWidget(self.override_sess_check)
        sess_check_row.addStretch(1)
        sess_layout.addLayout(sess_check_row)

        sess_input_row = QHBoxLayout()
        sess_input_row.setSpacing(8)
        sess_lbl = QLabel("SESSDATA:", self)
        sess_lbl.setStyleSheet("color: #d1d5db; min-width: 120px;")
        self.sess_edit = QLineEdit(self)
        self.sess_edit.setEchoMode(QLineEdit.Password)
        self.sess_edit.setPlaceholderText("請貼上 SESSDATA 值或留空使用全域設定...")
        self.sess_edit.setEnabled(False)

        self.sess_toggle_btn = QPushButton("👁 顯示", self)
        self.sess_toggle_btn.setFixedWidth(68)
        self.sess_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.sess_toggle_btn.clicked.connect(self._toggle_sess_visibility)
        self.sess_toggle_btn.setEnabled(False)

        sess_input_row.addWidget(sess_lbl)
        sess_input_row.addWidget(self.sess_edit, stretch=1)
        sess_input_row.addWidget(self.sess_toggle_btn)
        sess_layout.addLayout(sess_input_row)

        sess_hint = QLabel(
            "🔒 安全保證：SESSDATA 僅用於向 B站請求串流位址，日誌輸出與佇列紀錄中將自動遮蔽脫敏，絕不洩漏。",
            self,
        )
        sess_hint.setStyleSheet("color: #8b949e; font-size: 12px; margin-left: 128px;")
        sess_layout.addWidget(sess_hint)

        adv_content_layout.addLayout(sess_layout)
        self.adv_box.content_layout.addLayout(adv_content_layout)
        layout.addWidget(self.adv_box)

        # --- Bottom Action Row ---
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 8, 0, 0)
        action_row.setSpacing(12)

        self.status_msg_lbl = QLabel("", self)
        self.status_msg_lbl.setStyleSheet("color: #34d399; font-weight: 500;")

        self.submit_btn = QPushButton("📥 加入下載佇列", self)
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.setCursor(Qt.PointingHandCursor)
        self.submit_btn.setMinimumHeight(40)
        self.submit_btn.setMinimumWidth(160)
        self.submit_btn.clicked.connect(self._on_enqueue_downloads)

        action_row.addWidget(self.status_msg_lbl)
        action_row.addStretch(1)
        action_row.addWidget(self.submit_btn)
        layout.addLayout(action_row)

        layout.addStretch(1)
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

    # --- Data & Lifecycle Methods ---

    def on_page_activated(self) -> None:
        """Called when this page becomes active."""
        self._refresh_streamer_targets()
        self._sync_with_config()

    def on_config_updated(self) -> None:
        """Called when global config changes."""
        self._refresh_streamer_targets()
        self._sync_with_config()

    def _sync_with_config(self) -> None:
        bili_cfg = self.config.get_bilibili_config()
        if bili_cfg.get("prefer_video", False):
            self.radio_video.setChecked(True)
        else:
            self.radio_audio.setChecked(True)

    def _refresh_streamer_targets(self) -> None:
        """Populate the streamer target dropdown from watch directories and config."""
        cur_text = self.target_combo.currentText().strip()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()

        streamers: set[str] = set()

        # 1. From enabled watch directories
        watch_dirs = self.config.get_watch_dirs(only_enabled=True)
        for wd in watch_dirs:
            p = self.config.resolve_path(wd.path)
            if p.is_dir():
                for sub in p.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        streamers.add(sub.name)

        # 2. From streamer_model_map keys
        model_map = self.config.get("streamer_model_map", {})
        for s in model_map.keys():
            if s and s != "_default":
                streamers.add(s)

        # Default sorted streamers
        sorted_streamers = sorted(streamers)
        for s in sorted_streamers:
            self.target_combo.addItem(s)

        # Fallback if empty
        if not sorted_streamers:
            self.target_combo.addItem("預設下載目錄")

        if cur_text and cur_text in sorted_streamers:
            self.target_combo.setCurrentText(cur_text)
        elif sorted_streamers:
            self.target_combo.setCurrentIndex(0)

        self.target_combo.blockSignals(False)
        self._update_resolved_path_label()

    def _resolve_target_dir(self) -> Path:
        """Calculate the absolute target directory based on combo selection."""
        selection = self.target_combo.currentText().strip()
        default_root = self.config.get_default_download_root()

        if not selection:
            return default_root

        # Check if selection is an absolute path
        p = Path(selection)
        if p.is_absolute():
            return p

        # Check if existing directory inside any watch_dir
        watch_dirs = self.config.get_watch_dirs(only_enabled=True)
        for wd in watch_dirs:
            wp = self.config.resolve_path(wd.path)
            cand = wp / selection
            if cand.is_dir():
                return cand

        # Otherwise relative to default_root
        return (default_root / selection).resolve()

    def _update_resolved_path_label(self) -> None:
        target_dir = self._resolve_target_dir()
        self.resolved_path_lbl.setText(f"📁 儲存路徑: {target_dir}")

    # --- UI Event Handlers ---

    def _on_paste_clipboard(self) -> None:
        clip = QGuiApplication.clipboard()
        text = clip.text().strip()
        if not text:
            return
        cur = self.url_text_edit.toPlainText()
        if cur.strip():
            self.url_text_edit.setPlainText(cur.rstrip() + "\n" + text)
        else:
            self.url_text_edit.setPlainText(text)

    def _on_clear_input(self) -> None:
        self.url_text_edit.clear()
        self.status_msg_lbl.setText("")

    def _on_text_changed(self) -> None:
        items = self._parse_inputs()
        count = len(items)
        self.count_badge.setText(f"待下載: {count} 筆")

    def _on_browse_target_dir(self) -> None:
        default_dir = str(self._resolve_target_dir())
        chosen = QFileDialog.getExistingDirectory(self, "選擇下載落地目錄", default_dir)
        if chosen:
            self.target_combo.setCurrentText(chosen)
            self._update_resolved_path_label()

    def _on_override_sess_toggled(self, checked: bool) -> None:
        self.sess_edit.setEnabled(checked)
        self.sess_toggle_btn.setEnabled(checked)
        if not checked:
            self.sess_edit.clear()

    def _toggle_sess_visibility(self) -> None:
        if self.sess_edit.echoMode() == QLineEdit.Password:
            self.sess_edit.setEchoMode(QLineEdit.Normal)
            self.sess_toggle_btn.setText("🙈 隱藏")
        else:
            self.sess_edit.setEchoMode(QLineEdit.Password)
            self.sess_toggle_btn.setText("👁 顯示")

    def _parse_inputs(self) -> list[str]:
        """Extract valid URLs/BVs from the multiline input."""
        raw_text = self.url_text_edit.toPlainText()
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        valid_items: list[str] = []
        for line in lines:
            # Handle space-separated tokens on same line
            tokens = line.split()
            for t in tokens:
                t = t.strip(" ,;\"'<>")
                if t:
                    valid_items.append(t)
        return valid_items

    def _on_enqueue_downloads(self) -> None:
        """Build and enqueue tasks to the QueueManager."""
        items = self._parse_inputs()
        if not items:
            self.show_warning("未輸入連結", "請在輸入框中至少貼上一個 B站影片網址、BV 號或短網址。")
            return

        target_dir = self._resolve_target_dir()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.show_error("目錄建立失敗", f"無法建立目標下載目錄:\n{target_dir}\n錯誤: {e}")
            return

        is_video = self.radio_video.isChecked()
        page_val = self.page_spin.value()
        page_arg = page_val if page_val > 0 else None

        # SESSDATA cookie override or global config
        sessdata: str | None = None
        if self.override_sess_check.isChecked() and self.sess_edit.text().strip():
            sessdata = self.sess_edit.text().strip()
        else:
            global_sess = self.config.get_bilibili_config().get("sessdata", "").strip()
            if global_sess:
                sessdata = global_sess

        tasks: list[TaskItem] = []
        repo_root = self.config.project_root

        for item in items:
            cmd = CommandBuilder.build_bilibili_download_command(
                repo_root=repo_root,
                input_url_or_bv=item,
                out_dir=target_dir,
                video=is_video,
                page=page_arg,
                sessdata=sessdata,
            )
            mode_desc = "影片" if is_video else "音訊"
            task_title = f"[B站下載-{mode_desc}] {item} -> {target_dir.name}"

            task = TaskItem(
                title=task_title,
                command=cmd,
                cwd=str(repo_root),
                metadata={
                    "action": "bilibili_download",
                    "target_dir": str(target_dir),
                    "input": item,
                    "video": is_video,
                    "rescan_dashboard": True,
                },
            )
            tasks.append(task)

        task_ids = self.enqueue_tasks(tasks)
        self.status_msg_lbl.setText(f"✓ 已成功加入 {len(task_ids)} 個下載任務至單線排程！")
        self.url_text_edit.clear()

    def _on_task_finished(self, task: TaskItem) -> None:
        """Trigger rescan when a bilibili download task completes successfully."""
        if task.metadata.get("action") == "bilibili_download":
            target_dir = task.metadata.get("target_dir", "")
            self.download_completed.emit(target_dir)
