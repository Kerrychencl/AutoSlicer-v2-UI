"""Common media and metadata utilities for AutoSlicer.

Centralized definitions for:
- Streamer canonical naming
- Artist name cleaning
- Date / timestamp extraction
- Media tag inspection via ffprobe
- Windows file creation/modification time update via kernel32 API
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# 主播名稱正規化對照表
CANONICAL_STREAMERS: dict[str, str] = {
    "菫時": "-菫時-",
    "-菫時-": "-菫時-",
    "秋葵": "秋葵",
    "秋葵AKIAOI": "秋葵",
    "薇": "薇Steria",
    "薇Steria": "薇Steria",
    "随一": "随一Suiii",
    "隨一": "随一Suiii",
    "随一Suiii": "随一Suiii",
    "隨一Suiii": "随一Suiii",
}


def clean_artist(raw: str | None, fname: str) -> str:
    """Clean artist name: strip trailing room IDs or parentheses while preserving names like -菫時-."""
    s = (raw or "").strip()
    s = re.sub(r'\s*\(\d+\)\s*$', '', s).strip()
    s = re.sub(r'[\s_]\d+\s*$', '', s).strip()
    if not s:
        m = re.search(r'【([^】]+)】', fname)
        if m:
            s = m.group(1).strip()
            s = re.sub(r'\s*\(\d+\)\s*$', '', s).strip()
            s = re.sub(r'[\s_]\d+\s*$', '', s).strip()
    return s


def extract_date_info(fname: str, comment: str, tags: dict, file_path: Path | None = None) -> tuple[str, datetime | None]:
    """Extract recording date/time string (for MP4 date/year tag) and datetime object."""
    # 1. From comment (錄播姬 standard: 录制时间: 2026-04-07T11:32:44.6698722+08:00)
    if comment:
        m = re.search(r'录制时间:\s*(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})', comment)
        if m:
            d_str = f"{m.group(1)} {m.group(2)}"
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S")
                return d_str, dt
            except Exception:
                pass

    # 2. From filename: YYYY年MM月DD日HHMMSS
    m = re.search(r'(\d{4})年(\d{2})月(\d{2})日\s*(\d{2})[-:.]?(\d{2})[-:.]?(\d{2})', fname)
    if m:
        d_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S")
            return d_str, dt
        except Exception:
            pass

    # 3. From filename: YYYY-MM-DD HH-MM-SS or YYYY-MM-DD_HH-MM-SS
    m = re.search(r'(\d{4}-\d{2}-\d{2})[\s_](\d{2})[-:]?(\d{2})[-:]?(\d{2})', fname)
    if m:
        d_str = f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S")
            return d_str, dt
        except Exception:
            pass

    # 4. From tags date / DATE / year
    tag_date = tags.get("date") or tags.get("DATE") or tags.get("year")
    if tag_date:
        m = re.search(r'(\d{4}-\d{2}-\d{2})(?:[T\s](\d{2}:\d{2}:\d{2}))?', str(tag_date))
        if m:
            time_part = m.group(2) or "00:00:00"
            d_str = f"{m.group(1)} {time_part}"
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S")
                return d_str, dt
            except Exception:
                pass

    # 5. File stat mtime fallback
    if file_path and file_path.exists():
        stat = file_path.stat()
        dt = datetime.fromtimestamp(stat.st_mtime)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt

    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S"), now


def set_file_times_windows(path: Path | str, dt: datetime) -> None:
    """Set Windows CreationTime, LastAccessTime, and LastWriteTime to match recording time."""
    ts = dt.timestamp()
    try:
        os.utime(str(path), (ts, ts))
    except Exception:
        pass

    if sys.platform != "win32":
        return

    try:
        wintime = int((ts + 11644473600) * 10000000)
        ft = wintypes.FILETIME(wintime & 0xFFFFFFFF, wintime >> 32)
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

        handle = ctypes.windll.kernel32.CreateFileW(
            str(path), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None
        )
        if handle != -1 and handle != 0xFFFFFFFFFFFFFFFF:
            try:
                ctypes.windll.kernel32.SetFileTime(handle, ctypes.byref(ft), ctypes.byref(ft), ctypes.byref(ft))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def get_media_metadata(file_path: Path | str) -> dict:
    """Read stream and format metadata tags from input video/audio using ffprobe."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(file_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            data = json.loads(r.stdout)
            return data.get("format", {}).get("tags", {})
    except Exception:
        pass
    return {}
