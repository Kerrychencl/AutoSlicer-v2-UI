"""Convert annotation files (.segments.json or .csv) to LosslessCut (.llc) project files.

Usage:
  python scripts/export_annotations_to_llc.py                     # 轉換所有主播的標註為 .llc
  python scripts/export_annotations_to_llc.py --streamer -菫時-   # 僅轉換指定主播
  python scripts/export_annotations_to_llc.py --file path/to/file.segments.json
"""
import argparse
import json
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_DIRS = [
    Path(r"E:\Bilibili livestream"),
    Path(r"E:\Bilibili livestream\Bilibili live downloder"),
]

def find_video_file(media_name: str) -> Path | None:
    if not media_name:
        return None
    target_name = Path(media_name).name
    target_stem = Path(media_name).stem
    for base in SEARCH_DIRS:
        if not base.exists():
            continue
        cands = list(base.rglob(target_name))
        if cands and cands[0].exists():
            return cands[0]
        cands = list(base.rglob(f"{target_stem}.*"))
        for c in cands:
            if c.suffix.lower() in [".mp4", ".flv", ".mkv", ".m4a", ".mp3", ".wav"]:
                return c
    return None

def convert_json_to_llc(json_path: Path, out_dir: Path | None = None) -> Path:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    media_name = data.get("media_file", json_path.stem.replace(".segments", ""))
    segments = data.get("segments", [])

    llc_data = {
        "version": 2,
        "mediaFileName": Path(media_name).name,
        "cutSegments": [
            {
                "start": round(seg.get("start", seg.get("cut_start", 0)), 3),
                "end": round(seg.get("end", seg.get("cut_end", 0)), 3),
                "name": seg.get("name", f"Track_{i:02d}"),
                "selected": True,
            }
            for i, seg in enumerate(segments, 1)
        ]
    }
    llc_content = json.dumps(llc_data, indent=2, ensure_ascii=False)
    
    stem = Path(media_name).stem
    dest_dir = out_dir if out_dir else json_path.parent
    dest_llc = dest_dir / f"{stem}-proj.llc"
    dest_llc.write_text(llc_content, encoding="utf-8")
    
    # 若在硬碟找到原影片，也在原影片旁邊放一份，LosslessCut 開啟影片時能直接識別加載
    video_path = find_video_file(media_name)
    if video_path and video_path.parent != dest_llc.parent:
        try:
            (video_path.parent / f"{video_path.stem}-proj.llc").write_text(llc_content, encoding="utf-8")
            print(f"  [✓] 已同步放置於原影片目錄: {video_path.parent / f'{video_path.stem}-proj.llc'}")
        except Exception:
            pass

    return dest_llc

def main():
    import sys
    raw_args = sys.argv[1:]
    streamer = None
    file_path = None
    out_dir = None
    
    i = 0
    while i < len(raw_args):
        a = raw_args[i]
        if a in ("--file", "-f") and i + 1 < len(raw_args):
            file_path = raw_args[i+1]
            i += 2
        elif a in ("--out-dir", "-o") and i + 1 < len(raw_args):
            out_dir = Path(raw_args[i+1])
            i += 2
        elif a in ("--streamer", "-s") and i + 1 < len(raw_args):
            streamer = raw_args[i+1]
            i += 2
        elif a.startswith("--streamer="):
            streamer = a.split("=", 1)[1]
            i += 1
        elif not streamer:
            streamer = a
            i += 1
        else:
            i += 1

    if file_path:
        p = Path(file_path)
        if p.exists():
            res = convert_json_to_llc(p, out_dir)
            print(f"成功轉換: {res}")
        else:
            print(f"找不到檔案: {p}")
        return

    ann_root = ROOT / "data" / "annotations"
    if streamer:
        streamer_dirs = [ann_root / streamer]
    else:
        streamer_dirs = [d for d in ann_root.iterdir() if d.is_dir()]

    total = 0
    for sdir in streamer_dirs:
        if not sdir.exists():
            continue
        json_files = list(sdir.glob("*.segments.json"))
        print(f"正在處理 【{sdir.name}】: 共 {len(json_files)} 個標註檔案...")
        for jf in json_files:
            llc = convert_json_to_llc(jf, out_dir)
            print(f"  -> 生成 LLC: {llc.name}")
            total += 1

    print(f"\n全部完成！共轉換 {total} 個 LosslessCut (.llc) 專案檔。")

if __name__ == "__main__":
    main()
