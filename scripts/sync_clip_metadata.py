"""Batch synchronize video clip metadata (artist, comment, date/year) and Windows file timestamps.
Supports Mode A (automatic reverse matching by LosslessCut suffixes and timestamps)
and Mode C (pairing / interactive selection fallback for renamed clips).

Usage:
  python scripts/sync_clip_metadata.py [files or directories...]
"""
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from media_utils import clean_artist, extract_date_info, get_media_metadata, set_file_times_windows

SEARCH_DIRS = [
    Path(r"E:\Bilibili livestream"),
    Path(r"E:\Bilibili livestream\Bilibili live downloder"),
    Path(r"E:\音樂\主播歌切"),
]


def parse_recorder_xml(xml_path: Path) -> dict:
    """Parse 錄播姬 (bililive-recorder) XML for live stream metadata."""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        info = root.find("BililiveRecorderRecordInfo")
        if info is not None:
            roomid = info.get("roomid", "").strip()
            name = info.get("name", "").strip()
            title = info.get("title", "").strip()
            parent_area = info.get("areanameparent", "").strip()
            child_area = info.get("areanamechild", "").strip()
            start_time = info.get("start_time", "").strip()
            
            area_str = f"{parent_area}·{child_area}" if parent_area and child_area else (parent_area or child_area)
            comment = (
                f"mikufans直播间 {roomid} 的直播录像\n"
                f"主播名: {name}\n"
                f"直播标题: {title}\n"
                f"直播分区: {area_str}\n"
                f"录制时间: {start_time}\n\n"
                f"使用 mikufans录播姬 录制 https://rec.danmuji.org"
            )
            return {
                "artist": name,
                "title": title,
                "comment": comment,
                "start_time": start_time
            }
    except Exception:
        pass
    return {}


RE_QC_TAGS = re.compile(r"\[(?:[✅🟡🔴]|得分|分|\d+分)[^\]]*\]\s*", re.UNICODE)


def strip_losslesscut_suffix(stem: str, strip_qc_tags: bool = True) -> str:
    """Strip LosslessCut timecode/index/label suffixes to recover original recording stem."""
    s = stem
    if strip_qc_tags:
        s = RE_QC_TAGS.sub("", s).strip(" -_")
    # Pattern 1: -00.38.41.493-00.41.49.763-label or -00.38.41.493-00.41.49.763
    p1 = re.sub(r'(-\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{2}\.\d{2}\.\d{2}\.\d{3}(?:-.*)?)$', '', s)
    if p1 != s:
        return p1
    # Pattern 2: _song01, -seg01, _part01
    p2 = re.sub(r'(_song\d+|-seg\d+|_\d{3}|-\d{3})(?:-.*)?$', '', s, flags=re.IGNORECASE)
    if p2 != s:
        return p2
    # Pattern 3: -1, -2, -1-songname
    p3 = re.sub(r'(-\d{1,4}(?:-.*)?)$', '', s)
    if p3 != s:
        return p3
    # Pattern 4: _cut_...
    p4 = re.sub(r'(_cut_\d+.*)$', '', s)
    return p4

def extract_timestamp_fingerprint(text: str) -> str | None:
    """Extract recording timestamp fingerprint (e.g. 2026年10月10日082510 or 2026-10-10)"""
    m = re.search(r'(\d{4}年\d{2}月\d{2}日\d{6})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}年\d{2}月\d{2}日\s*\d{2}[-:.]?\d{2}[-:.]?\d{2})', text)
    if m:
        return re.sub(r'[\s-:.]', '', m.group(1))
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    return None

def find_source_metadata(clip_path: Path, known_ref: Path | None = None) -> tuple[str, str, str, datetime | None]:
    """Find source recording or XML, extracting (artist, comment, date_str, target_dt)."""
    # 1. If explicit reference provided (Mode C pairing)
    ref_file = known_ref
    
    # 2. Try sibling files using stripped stem (Mode A)
    stem = clip_path.stem
    base_stem = strip_losslesscut_suffix(stem)

    if not ref_file and base_stem != stem:
        for ext in [".mp4", ".flv", ".mkv"]:
            cand = clip_path.parent / f"{base_stem}{ext}"
            if cand.exists() and cand != clip_path:
                ref_file = cand
                break

    # 3. Try sibling XML
    if not ref_file and base_stem != stem:
        cand_xml = clip_path.parent / f"{base_stem}.xml"
        if not cand_xml.exists():
            # try without cmp
            cand_xml = clip_path.parent / f"{base_stem.replace(' cmp', '')}.xml"
        if cand_xml.exists():
            ref_file = cand_xml

    # 4. Search by timestamp fingerprint across SEARCH_DIRS (Mode A wide search)
    ts_fp = extract_timestamp_fingerprint(stem)
    if not ref_file and ts_fp:
        search_roots = [clip_path.parent]
        if clip_path.parent.parent.exists():
            search_roots.append(clip_path.parent.parent)
        search_roots.extend(SEARCH_DIRS)

        # Look for video or XML matching timestamp
        for root_dir in search_roots:
            if not root_dir.exists():
                continue
            matches = list(root_dir.rglob(f"*{ts_fp}*.mp4")) + \
                      list(root_dir.rglob(f"*{ts_fp}*.flv")) + \
                      list(root_dir.rglob(f"*{ts_fp}*.xml"))
            valid = [m for m in matches if m != clip_path and not strip_losslesscut_suffix(m.stem) != m.stem]
            if valid:
                # Prefer video, fallback to xml
                vids = [v for v in valid if v.suffix.lower() in [".mp4", ".flv", ".mkv"]]
                ref_file = vids[0] if vids else valid[0]
                break

    # 5. Extract metadata from ref_file or clip itself
    comment = ""
    artist = ""
    date_str = ""
    target_dt = None

    if ref_file and ref_file.exists():
        print(f"  [>] 成功溯源原錄播: {ref_file.name}")
        if ref_file.suffix.lower() == ".xml":
            xinfo = parse_recorder_xml(ref_file)
            artist = xinfo.get("artist", "")
            comment = xinfo.get("comment", "")
            date_str, target_dt = extract_date_info(ref_file.name, comment, {}, ref_file)
        else:
            tags = get_media_metadata(ref_file)
            artist = clean_artist(tags.get("artist") or tags.get("ARTIST"), ref_file.name)
            comment = tags.get("comment") or tags.get("description") or tags.get("COMMENT") or ""
            date_str, target_dt = extract_date_info(ref_file.name, comment, tags, ref_file)

    # 6. Fallback: inspect clip itself if no ref_file or comment is empty
    clip_tags = get_media_metadata(clip_path)
    if not comment:
        comment = clip_tags.get("comment") or clip_tags.get("description") or clip_tags.get("COMMENT") or ""
    if not artist:
        artist = clean_artist(clip_tags.get("artist") or clip_tags.get("ARTIST"), clip_path.name)
    if not date_str or not target_dt:
        date_str, target_dt = extract_date_info(clip_path.name, comment, clip_tags, clip_path)

    return artist, comment, date_str, target_dt

def apply_metadata_to_file(
    clip_path: Path,
    artist: str,
    comment: str,
    date_str: str,
    target_dt: datetime | None,
    sync_creation_time: bool = True,
    strip_qc_tags: bool = True,
) -> Path:
    """Write tags via ffmpeg stream copy, optionally strip QC tags, and update Windows CreationTime."""
    if strip_qc_tags:
        clean_stem = RE_QC_TAGS.sub("", clip_path.stem).strip(" -_")
        if clean_stem and clean_stem != clip_path.stem:
            new_path = clip_path.with_name(f"{clean_stem}{clip_path.suffix}")
            if not new_path.exists():
                try:
                    clip_path.rename(new_path)
                    print(f"  [✓] 檔名已清除品管標籤: {clip_path.name} -> {new_path.name}")
                    clip_path = new_path
                except Exception as e:
                    print(f"  [!] 檔名清除品管標籤失敗: {e}")

    clip_tags = get_media_metadata(clip_path)
    cur_artist = clip_tags.get("artist", "").strip()
    cur_comment = clip_tags.get("comment", "").strip()
    cur_date = clip_tags.get("date", "").strip()

    need_tag_update = (
        (artist and cur_artist != artist) or
        (date_str and cur_date != date_str) or
        (comment and cur_comment != comment)
    )

    if need_tag_update:
        tmp_out = clip_path.with_name(f"{clip_path.stem}.tmp_meta{clip_path.suffix}")
        meta_args = []
        if artist:
            meta_args.extend(["-metadata", f"artist={artist}"])
        if comment:
            meta_args.extend(["-metadata", f"comment={comment}"])
        if date_str:
            meta_args.extend(["-metadata", f"date={date_str}"])
            meta_args.extend(["-metadata", f"year={date_str}"])

        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(clip_path)] + meta_args + [
            "-c", "copy", "-avoid_negative_ts", "make_zero", str(tmp_out)
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and tmp_out.exists() and tmp_out.stat().st_size > 0:
            tmp_out.replace(clip_path)
            print("  [✓] MP4 元數據標籤已更新 (Artist / Date / Comment 已寫入)")
        else:
            if tmp_out.exists():
                tmp_out.unlink()
            print("  [!] 標籤更新跳過或未更動")
    else:
        print("  [i] MP4 元數據標籤已是最新，無需重寫")

    if target_dt and sync_creation_time:
        set_file_times_windows(clip_path, target_dt)
        print(f"  [✓] Windows 建立日期與修改時間已同步為: {date_str}")
    elif not sync_creation_time:
        print("  [i] 已指定 --no-creation-time，略過 Windows 建立時間同步")
    else:
        print("  [!] 未能取得確切錄播時分秒，保留現有檔案時間戳")
    return clip_path


def process_single_clip(
    clip_path: Path,
    known_ref: Path | None = None,
    sync_creation_time: bool = True,
    strip_qc_tags: bool = True,
) -> Path | None:
    print(f"\n[*] 正在處理切片: {clip_path.name}")
    artist, comment, date_str, target_dt = find_source_metadata(clip_path, known_ref)

    # Mode C: Fallback interactive prompt if completely orphan (no timestamp, no comment, no ref)
    if not comment and not target_dt and known_ref is None:
        print("  [?] 提示：未能自動逆向推算出原錄播來源（檔名可能已重命名為純歌名）。")
        print("      請直接拖曳該場「原錄播影片 (.mp4/.flv)」、「.xml」或「.llc 專案檔」到此視窗 (或直接按 Enter 跳過)：")
        try:
            manual_in = input("      > ").strip(" `\"'")
            if manual_in:
                m_path = Path(manual_in)
                if m_path.exists():
                    known_ref = m_path
                    artist, comment, date_str, target_dt = find_source_metadata(clip_path, known_ref)
        except (EOFError, KeyboardInterrupt):
            print("  [i] 非交互模式 (Non-interactive) 或輸入結束，跳過手動參考檔輸入。")
        except Exception:
            pass

    print(f"  [>] 演唱者 (Artist) : {artist or '(未指定)'}")
    print(f"  [>] 年份/時間 (Date) : {date_str or '(未指定)'}")
    print(f"  [>] 註解長度        : {len(comment)} 字元")

    apply_metadata_to_file(
        clip_path,
        artist,
        comment,
        date_str,
        target_dt,
        sync_creation_time=sync_creation_time,
        strip_qc_tags=strip_qc_tags,
    )
    return known_ref

import shlex

def parse_input_paths(val: str) -> list[Path]:
    """Parse single or multiple paths from interactive user input (handles drag-and-drop multiple paths)."""
    text = val.strip()
    if not text:
        return []
    try:
        tokens = shlex.split(text, posix=False)
    except Exception:
        tokens = re.findall(r'"([^"]+)"|(\S+)', text)
        tokens = [t[0] or t[1] for t in tokens]
    paths = []
    for t in tokens:
        clean = t.strip(' "')
        if clean:
            p = Path(clean)
            if p.exists():
                paths.append(p)
    return paths

def main():
    print("=" * 60)
    print("    AutoSlicer-v2 切片影片元數據與時間戳同步工具 (Mode A+C)")
    print("=" * 60)

    raw_args = []
    sync_creation_time = True
    strip_qc_tags = True

    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            clean_a = a.strip(" `'\"")
            if clean_a == "--no-creation-time":
                sync_creation_time = False
            elif clean_a == "--keep-qc-tags":
                strip_qc_tags = False
            elif clean_a:
                raw_args.append(Path(clean_a))
    else:
        print("使用說明：")
        print("  1. 可直接將多個 LosslessCut 切片影片或切片資料夾拖曳至 sync_metadata.bat")
        print("  2. 若檔案已重命名，可將「原錄播影片」與「切片影片」同時選取並拖入")
        print("  3. 支援參數: --no-creation-time, --keep-qc-tags")
        print("")
        try:
            val = input("請輸入切片檔案、資料夾路徑，或直接拖曳檔案至此：\n> ").strip(" `\"'")
            if val:
                raw_args = parse_input_paths(val)
        except (EOFError, KeyboardInterrupt):
            pass

    if not raw_args:
        print("未輸入任何檔案或目錄，程式結束。")
        return

    # 分流輸入：區分「原錄播/專案參考源」與「待處理切片」
    ref_files = []
    clip_files = []

    for item in raw_args:
        if not item.exists():
            continue
        if item.is_dir():
            # 支援目錄及其子目錄中的切片
            found = list(item.glob("*.mp4")) + list(item.glob("*.mkv")) + list(item.glob("*.flv"))
            clip_files.extend(found)
        elif item.is_file():
            ext = item.suffix.lower()
            if ext in [".xml", ".llc"]:
                ref_files.append(item)
            elif ext in [".mp4", ".flv", ".mkv"]:
                stem = item.stem
                if (item.stat().st_size > 400 * 1024 * 1024) and (strip_losslesscut_suffix(stem, strip_qc_tags=strip_qc_tags) == stem):
                    ref_files.append(item)
                else:
                    clip_files.append(item)

    # 去重並保持順序
    seen = set()
    dedup_clips = []
    for c in clip_files:
        if c.resolve() not in seen:
            seen.add(c.resolve())
            dedup_clips.append(c)
    clip_files = dedup_clips

    # If user dropped only large recordings, treat them as targets to sync their own timestamps
    if not clip_files and ref_files:
        clip_files = [r for r in ref_files if r.suffix.lower() in [".mp4", ".flv", ".mkv"]]
        ref_files = []

    explicit_ref = ref_files[0] if ref_files else None
    if explicit_ref:
        print(f"\n[Mode C 模式啟動] 偵測到參考源：{explicit_ref.name}，將自動為同行切片配對！")

    print(f"共計待處理 {len(clip_files)} 個切片檔案...")

    for f in clip_files:
        process_single_clip(
            f,
            known_ref=explicit_ref,
            sync_creation_time=sync_creation_time,
            strip_qc_tags=strip_qc_tags,
        )

    print("\n" + "=" * 60)
    print("🎉 所有切片檔案元數據與 Windows 時間戳同步完畢！")
    print("=" * 60)

if __name__ == "__main__":
    main()
