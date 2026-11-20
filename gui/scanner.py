"""VOD Scanner and State Machine Engine for AutoSlicer Desktop Workbench.

Adheres strictly to Section 4.1 & 4.2 of the specification:
- Scans multiple watch directories configured in config.paths.watch_dirs.
- Groups VODs by streamer subfolders.
- Classifies files into original VODs vs derived clips according to Section 4.1 rules:
  1. LosslessCut timecode suffixes regex (-HH.MM.SS.mmm-HH.MM.SS.mmm)
  2. Naming features (_songNN, -segNN, _cut_, songNN)
  3. QC safety tags ([✅...], [🟡...], etc.)
  4. Derivative files whose prefix matches a larger original file in the same directory.
- State machine determination (1..5):
  1: 待打軸 (WAITING_LLC)
  2: 已生 .llc (LLC_GENERATED)
  3: 有切片待修 Metadata (HAS_CLIPS_NEED_METADATA)
  4: 待匯入標註 (WAITING_IMPORT_ANNOTATION)
  5: 已完成 (COMPLETED)
- Maintains local cache in .autoslicer_gui_cache.json for fast metadata sync status
  without ffprobe lag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any

from .config import ConfigManager, get_config
from .models import VodItem, VodStatus, WatchDirConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
from media_utils import CANONICAL_STREAMERS

CACHE_FILE = PROJECT_ROOT / ".autoslicer_gui_cache.json"

MEDIA_EXTENSIONS = {".mp4", ".flv", ".mkv", ".ts", ".m4a"}

# Regex rules for clip classification (Section 4.1)
RE_TIMECODE_SUFFIX = re.compile(
    r"[-_]\d{2}\.\d{2}\.\d{2}\.\d{3}[-_]\d{2}\.\d{2}\.\d{2}\.\d{3}"
    r"|[-_]\d{2}\.\d{2}\.\d{2}\.\d{3}"
    r"|\(cut\s*-\s*\d{2}\.\d{2}\.\d{2}\)",
    re.IGNORECASE,
)

RE_CUT_FEATURES = re.compile(
    r"(?:_song\d+|-seg\d+|_cut_|_cut\d+|[Ss]ong_\d+|song\d+)",
    re.IGNORECASE,
)

RE_QC_TAGS = re.compile(
    r"\[(?:✅|🟡|❌|⏳).*?\]|【(?:✅|🟡|❌|⏳).*?】",
)


# ============================================================================
# Cache Management (.autoslicer_gui_cache.json)
# ============================================================================

_cache_lock = threading.Lock()


def load_metadata_cache(cache_path: Path | None = None) -> dict[str, Any]:
    """Load metadata sync cache JSON."""
    if cache_path is None:
        cache_path = CACHE_FILE
    with _cache_lock:
        if not cache_path.exists():
            return {"version": 1, "synced_clips": {}}
        try:
            content = cache_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict) and "synced_clips" in data:
                return data
        except Exception:
            pass
        return {"version": 1, "synced_clips": {}}


def save_metadata_cache(cache_data: dict[str, Any], cache_path: Path | None = None) -> None:
    """Save metadata sync cache JSON atomically."""
    if cache_path is None:
        cache_path = CACHE_FILE
    with _cache_lock:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            content = json.dumps(cache_data, indent=2, ensure_ascii=False)
            temp_path.write_text(content, encoding="utf-8")
            if temp_path.exists():
                temp_path.replace(cache_path)
        except Exception as e:
            print(f"[Scanner Cache] Failed to save cache: {e}", file=sys.stderr)


def is_clip_metadata_synced(clip_path: Path, cache_data: dict[str, Any] | None = None, cache_path: Path | None = None) -> bool:
    """Check if clip has been marked as metadata-synced in local cache without calling ffprobe."""
    if cache_data is None:
        cache_data = load_metadata_cache(cache_path)

    norm_path = str(clip_path.resolve()).replace("\\", "/")
    synced_clips = cache_data.get("synced_clips", {})
    if norm_path not in synced_clips:
        return False

    cached_mtime = synced_clips[norm_path]
    try:
        current_mtime = clip_path.stat().st_mtime
        # Allow 2.0s buffer for Windows filesystem timestamp granularity
        return current_mtime <= cached_mtime + 2.0
    except OSError:
        return False


def mark_clips_synced(clip_paths: list[Path | str], cache_path: Path | None = None) -> None:
    """Update cache marking specified clips as metadata-synced with their current mtime."""
    if cache_path is None:
        cache_path = CACHE_FILE
    cache = load_metadata_cache(cache_path)
    synced_clips = cache.setdefault("synced_clips", {})

    for p in clip_paths:
        path = Path(p)
        if path.exists():
            norm_path = str(path.resolve()).replace("\\", "/")
            try:
                synced_clips[norm_path] = path.stat().st_mtime
            except OSError:
                pass

    save_metadata_cache(cache, cache_path)


# ============================================================================
# Streamer Name Normalization
# ============================================================================

def resolve_streamer_name(text: str) -> str:
    """Resolve raw folder or string to canonical streamer name."""
    clean = text.strip()
    # Strip room id prefix like "3473884--菫時-" or "638257-秋葵AKIAOI"
    m_room = re.match(r"^\d+-(.+)$", clean)
    if m_room:
        clean = m_room.group(1).strip()

    # Check canonical dictionary
    for k, v in CANONICAL_STREAMERS.items():
        if k in clean:
            return v
    return clean


def extract_streamer_from_text(text: str) -> str:
    """Extract streamer name enclosed in 【...】 or return resolved name."""
    m = re.search(r"【([^】]+)】", text)
    if m:
        raw = m.group(1).strip()
        # Clean title prefix like 【歌杂】
        if raw not in ["歌杂", "杂谈", "回放", "录播"]:
            return resolve_streamer_name(raw)
    return resolve_streamer_name(text)


# ============================================================================
# File Classification Logic (Section 4.1)
# ============================================================================

def is_clip_filename(filename: str) -> bool:
    """Determine if a filename represents a derived clip according to regex rules."""
    name_no_ext = Path(filename).stem

    # Rule 1: LosslessCut timecode suffix
    if RE_TIMECODE_SUFFIX.search(name_no_ext):
        return True

    # Rule 2: Cut naming features
    if RE_CUT_FEATURES.search(name_no_ext):
        return True

    # Rule 3: QC safety tags at beginning or end
    if RE_QC_TAGS.search(name_no_ext):
        return True

    return False


def strip_clip_suffixes(stem: str) -> str:
    """Strip common clip suffixes to find the base VOD stem."""
    s = RE_TIMECODE_SUFFIX.sub("", stem)
    s = RE_CUT_FEATURES.sub("", s)
    s = RE_QC_TAGS.sub("", s)
    return s.strip(" -_")


# ============================================================================
# VOD Scanner Class
# ============================================================================

class VodScanner:
    """Scanner that discovers VODs and evaluates the 5-state lifecycle machine."""

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        cache_file: Path | None = None,
    ) -> None:
        self.config_manager = config_manager or get_config()
        self.cache_file = cache_file or CACHE_FILE

    def scan_all(self, only_enabled_watch_dirs: bool = True) -> list[VodItem]:
        """Scan all watch directories from configuration and return list of VodItem."""
        watch_dirs = self.config_manager.get_watch_dirs(only_enabled=only_enabled_watch_dirs)
        ann_dir = self.config_manager.get_annotations_dir()
        cache = load_metadata_cache(self.cache_file)

        all_vods: list[VodItem] = []
        seen_paths: set[Path] = set()

        for wd in watch_dirs:
            p = self.config_manager.resolve_path(wd.path)
            if not p.is_dir():
                continue
            vods = self.scan_directory(p, ann_dir, cache)
            for v in vods:
                if v.file_path not in seen_paths:
                    seen_paths.add(v.file_path)
                    all_vods.append(v)

        # Sort by mtime descending (most recent first)
        all_vods.sort(key=lambda item: item.mtime, reverse=True)
        return all_vods

    def scan_directory(
        self,
        watch_dir: Path,
        annotations_dir: Path | None = None,
        cache: dict[str, Any] | None = None,
    ) -> list[VodItem]:
        """Scan a single watch directory hierarchy and group by streamer subfolders."""
        if annotations_dir is None:
            annotations_dir = self.config_manager.get_annotations_dir()
        if cache is None:
            cache = load_metadata_cache(self.cache_file)

        vod_items: list[VodItem] = []
        if not watch_dir.exists():
            return vod_items

        # Discover streamer directories (first-level children)
        entries = list(watch_dir.iterdir())
        subdirs = [d for d in entries if d.is_dir() and not d.name.startswith(".")]

        # If watch_dir has media files directly in it, treat watch_dir as streamer "未分類"
        direct_media = [
            f for f in entries
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS
        ]
        if direct_media:
            streamer_name = extract_streamer_from_text(watch_dir.name)
            vod_items.extend(self._scan_folder_for_vods(watch_dir, streamer_name, watch_dir, annotations_dir, cache))

        for sdir in subdirs:
            streamer_name = resolve_streamer_name(sdir.name)
            # Scan sdir and its subdirectories
            vod_items.extend(self._scan_streamer_dir(sdir, streamer_name, watch_dir, annotations_dir, cache))

        return vod_items

    def _scan_streamer_dir(
        self,
        streamer_dir: Path,
        streamer_name: str,
        watch_dir: Path,
        annotations_dir: Path,
        cache: dict[str, Any],
    ) -> list[VodItem]:
        """Scan a streamer's directory tree."""
        vod_items: list[VodItem] = []
        
        # Collect all directories inside streamer_dir to process per directory
        dirs_to_check = [streamer_dir]
        for root, dirs, _ in os.walk(streamer_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["__pycache__"]]
            for d in dirs:
                dirs_to_check.append(Path(root) / d)

        for d in dirs_to_check:
            items = self._scan_folder_for_vods(d, streamer_name, watch_dir, annotations_dir, cache)
            vod_items.extend(items)

        return vod_items

    def _scan_folder_for_vods(
        self,
        folder: Path,
        streamer_name: str,
        watch_dir: Path,
        annotations_dir: Path,
        cache: dict[str, Any],
    ) -> list[VodItem]:
        """Scan a single folder, classifying original VODs vs derived clips."""
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []

        media_files = [
            f for f in entries
            if f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS
        ]
        if not media_files:
            return []

        # 1. First pass: classify by filename regex rules
        definite_clips: set[Path] = set()
        candidates: list[Path] = []

        for f in media_files:
            if is_clip_filename(f.name):
                definite_clips.add(f)
            else:
                candidates.append(f)

        # 2. Second pass: Rule 4 - check relative size/prefix inside same folder
        # If candidate B's stem starts with candidate A's stem and A is significantly larger,
        # B is a derived clip of A.
        candidates.sort(key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)
        
        original_vods: list[Path] = []
        for cand in candidates:
            cand_stem = cand.stem
            cand_size = cand.stat().st_size
            is_derivative = False
            for parent_vod in original_vods:
                parent_stem = parent_vod.stem
                parent_size = parent_vod.stat().st_size
                if cand_stem.startswith(parent_stem) and cand_stem != parent_stem:
                    # Cand is smaller prefix-match of an established VOD
                    if parent_size > cand_size * 2 and (parent_size - cand_size > 50 * 1024 * 1024):
                        is_derivative = True
                        definite_clips.add(cand)
                        break
            if not is_derivative:
                original_vods.append(cand)

        # 3. For each original VOD, find companion files and determine state
        vod_items: list[VodItem] = []
        for vod_file in original_vods:
            item = self._build_vod_item(
                vod_file=vod_file,
                streamer_name=streamer_name,
                watch_dir=watch_dir,
                folder_entries=entries,
                folder_clips=definite_clips,
                annotations_dir=annotations_dir,
                cache=cache,
            )
            vod_items.append(item)

        return vod_items

    def _build_vod_item(
        self,
        vod_file: Path,
        streamer_name: str,
        watch_dir: Path,
        folder_entries: list[Path],
        folder_clips: set[Path],
        annotations_dir: Path,
        cache: dict[str, Any],
    ) -> VodItem:
        """Construct a VodItem and calculate its state machine status."""
        stem = vod_file.stem
        # Derive clean stem without trailing " cmp" or "_cmp" for pairing
        clean_stem = re.sub(r"[\s_-]cmp$", "", stem, flags=re.IGNORECASE)

        # Detect specific streamer in VOD filename if present
        resolved_streamer = extract_streamer_from_text(stem)
        if resolved_streamer and resolved_streamer != stem:
            streamer = resolved_streamer
        else:
            streamer = streamer_name

        try:
            stat = vod_file.stat()
            size_bytes = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            size_bytes = 0
            mtime = 0.0

        # Look for companion XML file in folder
        xml_path = self._find_companion_file(folder_entries, [f"{stem}.xml", f"{clean_stem}.xml"])

        # Look for companion LLC project file in folder
        llc_path = self._find_companion_file(
            folder_entries,
            [f"{stem}-proj.llc", f"{stem}.llc", f"{clean_stem}-proj.llc", f"{clean_stem}.llc"]
        )

        # Find matching clips for this VOD
        matching_clips: list[Path] = []
        for clip in folder_clips:
            clip_stem = clip.stem
            # Match if clip stem starts with vod stem or clean stem
            if clip_stem.startswith(stem) or clip_stem.startswith(clean_stem):
                matching_clips.append(clip)
            elif strip_clip_suffixes(clip_stem) in (stem, clean_stem):
                matching_clips.append(clip)

        # Check metadata sync status for each clip
        unprocessed_clips: list[Path] = []
        for clip in matching_clips:
            if not is_clip_metadata_synced(clip, cache):
                unprocessed_clips.append(clip)

        # Check annotation status in data/annotations/<streamer>/
        is_annotated, llc_newer = self._check_annotation_status(
            streamer=streamer,
            stem=stem,
            clean_stem=clean_stem,
            llc_path=llc_path,
            annotations_dir=annotations_dir,
        )

        # State Machine Determination (Section 4.2)
        status = self._determine_status(
            llc_path=llc_path,
            clip_count=len(matching_clips),
            unprocessed_count=len(unprocessed_clips),
            is_annotated=is_annotated,
            llc_newer=llc_newer,
        )

        return VodItem(
            file_path=vod_file,
            stem=stem,
            streamer=streamer,
            watch_dir=watch_dir,
            size_bytes=size_bytes,
            mtime=mtime,
            xml_path=xml_path,
            llc_path=llc_path,
            clip_files=matching_clips,
            unprocessed_clip_files=unprocessed_clips,
            status=status,
            is_annotated=is_annotated,
        )

    def _find_companion_file(self, entries: list[Path], candidate_names: list[str]) -> Path | None:
        """Find the first matching companion file in the directory."""
        name_map = {e.name.lower(): e for e in entries if e.is_file()}
        for cand in candidate_names:
            cand_lower = cand.lower()
            if cand_lower in name_map:
                return name_map[cand_lower]
        return None

    def _check_annotation_status(
        self,
        streamer: str,
        stem: str,
        clean_stem: str,
        llc_path: Path | None,
        annotations_dir: Path,
    ) -> tuple[bool, bool]:
        """Check if annotations exist and whether the .llc file is newer than the annotation."""
        if not annotations_dir.exists():
            return False, False

        streamer_ann_dir = annotations_dir / streamer
        if not streamer_ann_dir.is_dir():
            # Try fuzzy match
            matching_dirs = [
                d for d in annotations_dir.iterdir()
                if d.is_dir() and (streamer in d.name or d.name in streamer)
            ]
            if matching_dirs:
                streamer_ann_dir = matching_dirs[0]
            else:
                return False, False

        # Look for <stem>.segments.json or <clean_stem>.segments.json
        candidate_names = [
            f"{stem}.segments.json",
            f"{clean_stem}.segments.json",
        ]
        ann_file: Path | None = None
        for name in candidate_names:
            p = streamer_ann_dir / name
            if p.is_file():
                ann_file = p
                break

        if ann_file is None:
            # Try fuzzy search in streamer_ann_dir
            for f in streamer_ann_dir.glob("*.segments.json"):
                if clean_stem in f.stem or f.stem in clean_stem:
                    ann_file = f
                    break

        if ann_file is None:
            return False, False

        # If annotation exists, compare mtime with .llc
        llc_newer = False
        if llc_path and llc_path.is_file():
            try:
                llc_mtime = llc_path.stat().st_mtime
                ann_mtime = ann_file.stat().st_mtime
                if llc_mtime > ann_mtime + 2.0:
                    llc_newer = True
            except OSError:
                pass

        return True, llc_newer

    def _determine_status(
        self,
        llc_path: Path | None,
        clip_count: int,
        unprocessed_count: int,
        is_annotated: bool,
        llc_newer: bool,
    ) -> VodStatus:
        """Evaluate the 5-state lifecycle priority:
        
        Priority:
        1: WAITING_LLC (待打軸): 存在原錄播，同目錄無對應 .llc 檔
        2: LLC_GENERATED (已生 .llc 待剪輯): 存在對應 .llc 檔，同目錄無切片，且尚未匯入標註
        3: HAS_CLIPS_NEED_METADATA (有切片待修 Metadata): 偵測到 1 個以上切片，且其中有切片未同步快取
        4: WAITING_IMPORT_ANNOTATION (待匯入標註): 存在 .llc 檔但無標註或 .llc 修改時間晚於標註檔
        5: COMPLETED (已完成): 無待修切片，且 .llc 已同步至標註
        """
        # Priority 1: No .llc file exists
        if llc_path is None or not llc_path.exists():
            return VodStatus.WAITING_LLC

        # Priority 3: Has clips that need metadata repair
        if unprocessed_count > 0:
            return VodStatus.HAS_CLIPS_NEED_METADATA

        # Priority 2: LLC generated, no clips yet, not annotated
        if clip_count == 0 and not is_annotated:
            return VodStatus.LLC_GENERATED

        # Priority 4: Awaiting annotation import (either not annotated, or llc is newer)
        if not is_annotated or llc_newer:
            return VodStatus.WAITING_IMPORT_ANNOTATION

        # Priority 5: Completed
        return VodStatus.COMPLETED
