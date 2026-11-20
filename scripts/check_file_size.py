"""Pre-commit check to enforce file size limits (< 5,000 tokens / 20 KB).

Prevents bloat so agent context windows stay fast and accurate.
Exemptions:
- Binary model weights, audio/video media, images
- Lockfiles, build artifacts, gitignored caches
- Raw research datasets and HTML visual roadmaps
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

MAX_BYTES = 20 * 1024  # 20 KB (~5,000 tokens)

EXEMPT_EXTENSIONS = {
    ".pt", ".pth", ".bin", ".onnx", ".ckpt",
    ".mp4", ".flv", ".mkv", ".ts", ".mov", ".webm", ".avi",
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
    ".png", ".jpg", ".jpeg", ".ico", ".svg",
    ".exe", ".dll", ".so", ".dylib", ".whl",
}

EXEMPT_PATTERNS = [
    "package-lock.json",
    "poetry.lock",
    "Pipfile.lock",
    ".autoslicer_gui_cache.json",
    "research_raw.json",
    "autoslicer_roadmap.html",
]

EXEMPT_DIRS = [
    "data",
    "runs",
    "out",
    ".venv",
    ".git",
    "autoslicer.egg-info",
]


def is_exempt(rel_path: str) -> bool:
    norm_path = rel_path.replace("\\", "/")
    path_obj = Path(norm_path)

    # 1. Extension check
    if path_obj.suffix.lower() in EXEMPT_EXTENSIONS:
        return True

    # 2. Directory check
    parts = norm_path.split("/")
    for d in EXEMPT_DIRS:
        if d in parts:
            return True

    # 3. Filename pattern check
    fname = path_obj.name
    for pat in EXEMPT_PATTERNS:
        if pat in fname:
            return True

    return False


def get_staged_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            check=True,
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return [Path(line) for line in lines]
    except Exception as e:
        print(f"[Warning] Failed to get staged files: {e}", file=sys.stderr)
        return []


def main() -> int:
    staged_files = get_staged_files()
    if not staged_files:
        return 0

    oversized: list[tuple[str, int]] = []
    root = Path(__file__).resolve().parent.parent

    for rel_file in staged_files:
        if is_exempt(str(rel_file)):
            continue

        full_path = root / rel_file
        if not full_path.is_file():
            continue

        size = full_path.stat().st_size
        if size > MAX_BYTES:
            oversized.append((str(rel_file), size))

    if oversized:
        print("\n" + "=" * 70, file=sys.stderr)
        print("❌ [Git Hook 攔截] 提交被拒絕：偵測到下列檔案超過大小限制 (5,000 tokens / 20 KB)：", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for f, sz in oversized:
            print(f"  • {f} -> {sz / 1024:.1f} KB (上限: 20.0 KB)", file=sys.stderr)
        print("\n💡 規範說明：", file=sys.stderr)
        print("  Agent 每次讀入超大檔案會迅速消耗 context window 並降低推論精準度。", file=sys.stderr)
        print("  請將超標檔案按功能職責拆分成數個較小的模組（或將靜態數據移入 data/docs）後再行提交。", file=sys.stderr)
        print("=" * 70 + "\n", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
