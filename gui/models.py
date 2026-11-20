"""Data models and enums for AutoSlicer Desktop Workbench."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path
import time
import uuid


class TaskStatus(str, Enum):
    """Execution status for a task in the FIFO queue."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def display_name(self) -> str:
        names = {
            TaskStatus.PENDING: "排隊中",
            TaskStatus.RUNNING: "執行中",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失敗",
            TaskStatus.CANCELLED: "已中止",
        }
        return names.get(self, self.value)

    @property
    def is_finished(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclasses.dataclass
class TaskItem:
    """Represents a discrete executable task in the queue."""
    title: str
    command: list[str]
    id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4())[:8])
    cwd: str | None = None
    env_extra: dict[str, str] | None = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = -1  # -1 for indeterminate, 0..100 for percentage
    log_lines: list[str] = dataclasses.field(default_factory=list)
    error_message: str = ""
    exit_code: int | None = None
    created_at: float = dataclasses.field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    metadata: dict = dataclasses.field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    @property
    def duration_formatted(self) -> str:
        s = int(round(self.duration_s))
        m, sec = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    @property
    def is_active(self) -> bool:
        return self.status == TaskStatus.RUNNING

    @property
    def can_retry(self) -> bool:
        return self.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)


class VodStatus(IntEnum):
    """5-State lifecycle state machine for livestream recordings.
    
    Priority order:
    1: WAITING_LLC (待打軸 - 存在原錄播，同目錄無對應 .llc 檔)
    2: LLC_GENERATED (已生 .llc 待剪輯 - 存在 .llc，同目錄無切片且未匯入標註)
    3: HAS_CLIPS_NEED_METADATA (有切片待修 Metadata - 偵測到切片且有切片未在快取中)
    4: WAITING_IMPORT_ANNOTATION (待匯入標註 - 存在 .llc 但 data/annotations 無記錄或 .llc 較新)
    5: COMPLETED (已完成 - 無待修切片且 .llc 已同步標註)
    """
    WAITING_LLC = 1
    LLC_GENERATED = 2
    HAS_CLIPS_NEED_METADATA = 3
    WAITING_IMPORT_ANNOTATION = 4
    COMPLETED = 5

    @property
    def display_name(self) -> str:
        names = {
            VodStatus.WAITING_LLC: "待打軸",
            VodStatus.LLC_GENERATED: "已生 .llc",
            VodStatus.HAS_CLIPS_NEED_METADATA: "有切片 (待修 Metadata)",
            VodStatus.WAITING_IMPORT_ANNOTATION: "待匯入標註",
            VodStatus.COMPLETED: "已完成",
        }
        return names.get(self, f"狀態 {self.value}")

    @property
    def short_name(self) -> str:
        names = {
            VodStatus.WAITING_LLC: "待打軸",
            VodStatus.LLC_GENERATED: "已生 .llc",
            VodStatus.HAS_CLIPS_NEED_METADATA: "待修標籤",
            VodStatus.WAITING_IMPORT_ANNOTATION: "待匯標註",
            VodStatus.COMPLETED: "已完成",
        }
        return names.get(self, f"狀態 {self.value}")

    @property
    def badge_color(self) -> str:
        colors = {
            VodStatus.WAITING_LLC: "#58a6ff",              # 灰藍
            VodStatus.LLC_GENERATED: "#d29922",            # 琥珀黃
            VodStatus.HAS_CLIPS_NEED_METADATA: "#f85149",  # 橘紅
            VodStatus.WAITING_IMPORT_ANNOTATION: "#bc8cff",# 藍紫
            VodStatus.COMPLETED: "#3fb950",                # 翠綠
        }
        return colors.get(self, "#8b949e")

    @property
    def default_action_name(self) -> str:
        actions = {
            VodStatus.WAITING_LLC: "生成 .llc",
            VodStatus.LLC_GENERATED: "用 LosslessCut 開啟",
            VodStatus.HAS_CLIPS_NEED_METADATA: "修復 Metadata",
            VodStatus.WAITING_IMPORT_ANNOTATION: "匯入標註",
            VodStatus.COMPLETED: "開啟資料夾",
        }
        return actions.get(self, "查看")


@dataclasses.dataclass
class VodItem:
    """Represents a single livestream recording session (VOD) and its associated files."""
    file_path: Path
    stem: str
    streamer: str
    watch_dir: Path
    size_bytes: int = 0
    mtime: float = 0.0
    xml_path: Path | None = None
    llc_path: Path | None = None
    clip_files: list[Path] = dataclasses.field(default_factory=list)
    unprocessed_clip_files: list[Path] = dataclasses.field(default_factory=list)
    status: VodStatus = VodStatus.WAITING_LLC
    is_annotated: bool = False

    @property
    def filename(self) -> str:
        return self.file_path.name

    @property
    def parent_dir(self) -> Path:
        return self.file_path.parent

    @property
    def clip_count(self) -> int:
        return len(self.clip_files)

    @property
    def unprocessed_clip_count(self) -> int:
        return len(self.unprocessed_clip_files)

    @property
    def display_size(self) -> str:
        b = self.size_bytes
        if b <= 0:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024.0 or unit == "TB":
                return f"{b:.2f} {unit}" if unit in ("MB", "GB", "TB") else f"{b:.0f} {unit}"
            b /= 1024.0
        return f"{b:.2f} GB"

    @property
    def display_date(self) -> str:
        if self.mtime <= 0:
            return "--"
        dt = datetime.fromtimestamp(self.mtime)
        return dt.strftime("%Y-%m-%d %H:%M")


@dataclasses.dataclass
class WatchDirConfig:
    """Config item for a watch directory."""
    label: str
    path: str
    enabled: bool = True
