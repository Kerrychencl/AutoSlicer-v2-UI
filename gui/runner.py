"""QProcess runner wrapper and command builders for AutoSlicer Desktop Workbench.

Features:
- Prepares sys.executable calls with UTF-8 env: PYTHONUTF8=1, PYTHONIOENCODING=utf-8, PYTHONUNBUFFERED=1
- Regex stream parser for output progress extraction
- Output sanitation hiding sensitive parameters (e.g. SESSDATA, cookies)
- Process tree termination using taskkill /PID <pid> /T /F on cancel
- Command builders strictly aligned with CLI interfaces of src/ and scripts/
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

PROGRESS_REGEXES = [
    re.compile(r"(\d{1,3})\s*%"),                          # "45%", "100 %"
    re.compile(r"\[(\d+)/(\d+)\]"),                         # "[3/10]"
    re.compile(r"(\d+)/(\d+)\s+\["),                        # "30/100 [" (tqdm)
    re.compile(r"(\d+)/(\d+)\s+files", re.IGNORECASE),      # "4/12 files"
]

def sanitize_text(text: str) -> str:
    """Mask sensitive credentials like SESSDATA or cookies from logs and commands."""
    if not text:
        return ""
    # Mask --cookie <val> with --cookie ***
    text = re.sub(r"(--cookie\s+)(?:\"[^\"]*\"|'[^']*'|[^\s]+)", r"\1***", text, flags=re.IGNORECASE)
    # Mask any standalone SESSDATA=... with SESSDATA=***
    text = re.sub(r"(SESSDATA=)[^;&\s\"']+", r"\1***", text, flags=re.IGNORECASE)
    return text


class ProcessRunner(QObject):
    """Manages execution of a single external subprocess via QProcess with UTF-8 streaming."""

    output_received = Signal(str)          # Emits sanitized log lines
    progress_changed = Signal(int)         # Emits progress percentage (0..100) or -1 for indeterminate
    process_started = Signal(int)          # Emits PID
    process_finished = Signal(int, int)    # Emits exitCode, exitStatus
    process_error = Signal(str)            # Emits error description
    process_cancelled = Signal()           # Emits when cancelled by user

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        self._pid: int | None = None
        self._is_cancelled: bool = False
        self._last_progress: int = -1

    @property
    def pid(self) -> int | None:
        return self._pid

    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def start(
        self,
        command: list[str],
        cwd: str | Path | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> bool:
        """Start a command in the runner."""
        if self.is_running():
            self.output_received.emit("[錯誤] 上一個任務仍在執行中，無法啟動新任務。")
            return False

        if not command:
            self.output_received.emit("[錯誤] 傳入的命令列為空。")
            return False

        self._is_cancelled = False
        self._last_progress = -1
        self._pid = None

        # Build clean environment with enforced UTF-8 settings
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        if env_extra:
            for k, v in env_extra.items():
                env.insert(k, str(v))
        self._process.setProcessEnvironment(env)

        if cwd:
            self._process.setWorkingDirectory(str(cwd))

        program = command[0]
        arguments = command[1:]

        preview_cmd = sanitize_text(" ".join(f'"{a}"' if " " in a else a for a in command))
        self.output_received.emit(f"[*] 執行指令: {preview_cmd}")

        self._process.start(program, arguments)
        return True

    def cancel(self, timeout_ms: int = 3000) -> None:
        """Cancel and terminate the process tree cleanly."""
        if not self.is_running():
            return

        self._is_cancelled = True
        self.output_received.emit("[!] 正在中止目前任務行程...")

        target_pid = self._pid or self._process.processId()

        # Step 1: Normal terminate
        self._process.terminate()

        # Step 2: On Windows, enforce process tree kill via taskkill to clean child processes (ffmpeg/python)
        if target_pid and sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(target_pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception as e:
                self.output_received.emit(f"[!] taskkill 執行警告: {e}")

        # Step 3: Hard kill if still alive
        if not self._process.waitForFinished(timeout_ms):
            self._process.kill()
            self._process.waitForFinished(1000)

        self.process_cancelled.emit()
        self.output_received.emit("[x] 任務已成功中止。")

    def _on_started(self) -> None:
        self._pid = self._process.processId()
        self.process_started.emit(self._pid)

    def _on_ready_read(self) -> None:
        raw_data = self._process.readAllStandardOutput().data()
        try:
            text = raw_data.decode("utf-8", errors="replace")
        except Exception:
            text = raw_data.decode(sys.getdefaultencoding(), errors="replace")

        lines = text.splitlines()
        for line in lines:
            line_str = line.strip("\r")
            if not line_str:
                continue

            # Parse progress
            self._parse_progress(line_str)

            # Sanitize and emit
            sanitized = sanitize_text(line_str)
            self.output_received.emit(sanitized)

    def _parse_progress(self, line: str) -> None:
        """Extract percentage or step progress from output line."""
        for regex in PROGRESS_REGEXES:
            match = regex.search(line)
            if match:
                groups = match.groups()
                if len(groups) == 1:
                    pct = int(groups[0])
                    if 0 <= pct <= 100 and pct != self._last_progress:
                        self._last_progress = pct
                        self.progress_changed.emit(pct)
                        return
                elif len(groups) == 2:
                    try:
                        curr, total = int(groups[0]), int(groups[1])
                        if total > 0:
                            pct = min(100, int((curr / total) * 100))
                            if pct != self._last_progress:
                                self._last_progress = pct
                                self.progress_changed.emit(pct)
                                return
                    except ValueError:
                        pass

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        status_val = exit_status.value if hasattr(exit_status, "value") else int(exit_status)
        self.process_finished.emit(exit_code, int(status_val))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        desc = {
            QProcess.ProcessError.FailedToStart: "無法啟動程序（檔案不存在或缺少執行權限）",
            QProcess.ProcessError.Crashed: "程序執行中意外崩潰 (Crashed)",
            QProcess.ProcessError.Timedout: "程序執行超時",
            QProcess.ProcessError.WriteError: "寫入子行程管道失敗",
            QProcess.ProcessError.ReadError: "讀取子行程管道失敗",
            QProcess.ProcessError.UnknownError: "未知的程序錯誤",
        }.get(error, f"程序錯誤碼: {error}")
        self.process_error.emit(desc)


class CommandBuilder:
    """Helper methods to construct CLI arguments strictly matching AutoSlicer scripts."""

    @staticmethod
    def get_python_exe() -> str:
        """Returns the current Python executable."""
        return sys.executable

    @staticmethod
    def build_infer_command(
        repo_root: Path,
        input_path: str | Path,
        checkpoint: str | Path,
        out_dir: str | Path = "out",
        clip_out_dir: str | Path | None = None,
        cut_video: bool = False,
        on_threshold: float | None = None,
        off_threshold: float | None = None,
        gpu: int = 0,
        device: str | None = None,
        page: int | None = None,
        sessdata: str | None = None,
    ) -> list[str]:
        """Construct CLI call for src/infer.py."""
        script_path = (repo_root / "src" / "infer.py").resolve()
        cmd = [
            CommandBuilder.get_python_exe(),
            "-u",
            str(script_path),
            "--input",
            str(input_path),
            "--checkpoint",
            str(checkpoint),
            "--out-dir",
            str(out_dir),
            "--gpu",
            str(gpu),
        ]
        if clip_out_dir:
            cmd.extend(["--clip-out-dir", str(clip_out_dir)])
        if cut_video:
            cmd.append("--cut-video")
        if device:
            cmd.extend(["--device", str(device)])
        if on_threshold is not None:
            cmd.extend(["--on-threshold", str(on_threshold)])
        if off_threshold is not None:
            cmd.extend(["--off-threshold", str(off_threshold)])
        if page is not None:
            cmd.extend(["--page", str(page)])
        if sessdata:
            cookie_val = sessdata if sessdata.startswith("SESSDATA=") else f"SESSDATA={sessdata}"
            cmd.extend(["--cookie", cookie_val])
        return cmd

    @staticmethod
    def build_bilibili_download_command(
        repo_root: Path,
        input_url_or_bv: str,
        out_dir: str | Path,
        video: bool = False,
        page: int | None = None,
        sessdata: str | None = None,
    ) -> list[str]:
        """Construct CLI call for src/bilibili.py."""
        script_path = (repo_root / "src" / "bilibili.py").resolve()
        cmd = [
            CommandBuilder.get_python_exe(),
            "-u",
            str(script_path),
            input_url_or_bv.strip(),
            "--out-dir",
            str(out_dir),
        ]
        if video:
            cmd.append("--video")
        if page is not None:
            cmd.extend(["--page", str(page)])
        if sessdata:
            cookie_val = sessdata if sessdata.startswith("SESSDATA=") else f"SESSDATA={sessdata}"
            cmd.extend(["--cookie", cookie_val])
        return cmd

    @staticmethod
    def build_sync_metadata_command(
        repo_root: Path,
        target_paths: list[str | Path],
    ) -> list[str]:
        """Construct CLI call for scripts/sync_clip_metadata.py (Mode A + Mode C)."""
        script_path = (repo_root / "scripts" / "sync_clip_metadata.py").resolve()
        cmd = [
            CommandBuilder.get_python_exe(),
            "-u",
            str(script_path),
        ]
        cmd.extend(str(p) for p in target_paths)
        return cmd

    @staticmethod
    def build_import_llc_command(
        repo_root: Path,
        target_paths: list[str | Path],
    ) -> list[str]:
        """Construct CLI call for scripts/convert_llc_to_annotations.py."""
        script_path = (repo_root / "scripts" / "convert_llc_to_annotations.py").resolve()
        cmd = [
            CommandBuilder.get_python_exe(),
            "-u",
            str(script_path),
        ]
        cmd.extend(str(p) for p in target_paths)
        return cmd

    @staticmethod
    def launch_losslesscut(
        exe_path: str | Path,
        media_path: str | Path | None = None,
        llc_path: str | Path | None = None,
    ) -> bool:
        """Launch LosslessCut asynchronously without tying to the single-thread task queue."""
        exe = Path(exe_path)
        if not exe.is_file():
            return False

        args = []
        if media_path and Path(media_path).exists():
            args.append(str(media_path))
        if llc_path and Path(llc_path).exists():
            args.append(str(llc_path))

        try:
            return QProcess.startDetached(str(exe), args)
        except Exception:
            try:
                subprocess.Popen([str(exe), *args], close_fds=True)
                return True
            except Exception:
                return False
