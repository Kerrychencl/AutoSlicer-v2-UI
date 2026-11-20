import re
import json
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from media_utils import CANONICAL_STREAMERS

MEDIA_EXTENSIONS = {
    ".mp4", ".flv", ".mkv", ".ts", ".mov", ".webm", ".avi",
    ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus"
}

def parse_llc(llc_path: Path) -> dict:
    if not llc_path.is_file():
        raise FileNotFoundError(f"找不到檔案: {llc_path}")
        
    raw_bytes = llc_path.read_bytes()
    
    # 快速判定常見的二進位多媒體格式，防止誤將影片檔當作專案文字檔讀取
    if (
        b"ftyp" in raw_bytes[:64]
        or raw_bytes.startswith(b"FLV")
        or raw_bytes.startswith(b"\x1a\x45\xdf\xa3")
        or raw_bytes.startswith(b"RIFF")
        or b"\x00\x00\x00" in raw_bytes[:64]
    ):
        raise ValueError(
            f"「{llc_path.name}」為多媒體二進位影片/音訊檔案，不是 LosslessCut 專案文字檔 (.llc)！"
        )
        
    encodings = ["utf-8-sig", "utf-8", "utf-16", "gb18030", "cp950", "latin1"]
    text = None
    for enc in encodings:
        try:
            text = raw_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
            
    if text is None:
        raise ValueError(f"無法以常用文字編碼 (UTF-8, GBK, CP950) 解析檔案: {llc_path.name}")

    # 清洗 LosslessCut JS 物件 / JSON 格式
    s = re.sub(r'([a-zA-Z_]\w*)\s*:', r'"\1":', text)
    s = re.sub(r"'((?:\\.|[^'\\])*)'", r'"\1"', s)
    s = re.sub(r',\s*([\]}])', r'\1', s)
    
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.loads(text)
        except Exception as e:
            raise ValueError(f"無法解析專案檔之 JSON 結構: {e}")

def fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    millis = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{int(s):02d}.{millis:03d}"

def extract_streamer_name(llc_path: Path, media_name: str) -> str:
    # 1. 優先從檔名中的 【主播名】 提取 (例如 【-菫時-】 或 【秋葵AKIAOI】)
    for text in [media_name, llc_path.stem, llc_path.name]:
        m = re.search(r"【([^】]+)】", text)
        if m:
            raw_name = m.group(1).strip()
            for key, canonical in CANONICAL_STREAMERS.items():
                if key in raw_name:
                    return canonical
            return raw_name
            
    # 2. 次選從上層資料夾名稱提取 (例如 3473884--菫時- 或 638257-秋葵AKIAOI)
    for parent in [llc_path.parent] + list(llc_path.parents):
        m_dir = re.search(r"^\d+-(.+)$", parent.name)
        if m_dir:
            raw_name = m_dir.group(1).strip()
            for key, canonical in CANONICAL_STREAMERS.items():
                if key in raw_name:
                    return canonical
            return raw_name
            
    # 3. 檢查文字中是否包含已知主播名稱
    full_str = f"{media_name} {llc_path.as_posix()}"
    for key, canonical in CANONICAL_STREAMERS.items():
        if key in full_str:
            return canonical
            
    return "未分類"

def find_companion_llc(media_path: Path) -> Path | None:
    """若傳入的是影片檔，自動在同目錄尋找對應的 LosslessCut .llc 專案檔"""
    parent = media_path.parent
    stem = media_path.stem
    
    # 1. 精確匹配：<檔名>-proj.llc 或 <檔名>.llc
    for candidate in [parent / f"{stem}-proj.llc", parent / f"{stem}.llc"]:
        if candidate.is_file():
            return candidate
            
    # 2. 前綴匹配或包含匹配
    for f in parent.glob("*.llc"):
        if f.stem == stem or f.stem.startswith(stem) or stem in f.stem:
            return f
            
    # 3. 資料夾內只有唯一的 *-proj.llc 或 *.llc
    proj_llcs = list(parent.glob("*-proj.llc"))
    if len(proj_llcs) == 1:
        return proj_llcs[0]
        
    all_llcs = list(parent.glob("*.llc"))
    if len(all_llcs) == 1:
        return all_llcs[0]
        
    return None

def convert_llc(llc_path: str | Path, out_dir: Path | None = None, streamer_override: str | None = None) -> int:
    llc_path = Path(llc_path)
    try:
        data = parse_llc(llc_path)
    except Exception as e:
        print(f"  [x] 讀取專案檔「{llc_path.name}」失敗: {e}")
        return 0
    
    media_name = data.get("mediaFileName") or llc_path.stem.replace("-proj", "")
    media_stem = Path(media_name).stem
    raw_segments = data.get("cutSegments", [])
    
    segments = [s for s in raw_segments if "start" in s and "end" in s]
    if not segments:
        print(f"  [!] 專案檔「{llc_path.name}」中未包含有效剪輯片段 (cutSegments 為空)！")
        return 0
    
    streamer = streamer_override or extract_streamer_name(llc_path, media_name)
    if streamer in CANONICAL_STREAMERS:
        streamer = CANONICAL_STREAMERS[streamer]
    
    if out_dir is None:
        out_dir = ROOT / "data" / "annotations" / streamer
    out_dir.mkdir(parents=True, exist_ok=True)
    
    raw_dir = ROOT / "data" / "raw" / f"{streamer} 标注"
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Export Excel / User-Friendly CSV (UTF-8 with BOM)
    csv_user_path = out_dir / f"{media_stem}.打軸表.csv"
    with open(csv_user_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["序號", "歌曲名稱", "起點時間(秒)", "終點時間(秒)", "時長(秒)", "起點時間碼", "終點時間碼"])
        for i, seg in enumerate(segments, 1):
            st = float(seg["start"])
            et = float(seg["end"])
            dur = et - st
            track_name = seg.get("name") or f"Track_{i}"
            writer.writerow([i, track_name, f"{st:.3f}", f"{et:.3f}", f"{dur:.3f}", fmt_time(st), fmt_time(et)])
            
    # 2. Export Training JSON
    json_train_path = out_dir / f"{media_stem}.segments.json"
    train_data = {
        "streamer": streamer,
        "media_file": media_name,
        "total_songs": len(segments),
        "segments": [
            {
                "index": i,
                "name": seg.get("name") or f"Track_{i}",
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "duration": round(float(seg["end"]) - float(seg["start"]), 3),
                "start_fmt": fmt_time(float(seg["start"])),
                "end_fmt": fmt_time(float(seg["end"])),
                "positive": True
            }
            for i, seg in enumerate(segments, 1)
        ]
    }
    with open(json_train_path, 'w', encoding='utf-8') as f:
        json.dump(train_data, f, ensure_ascii=False, indent=2)
        
    # 3. Export AutoSlicer Gold TSV format (for gold pipeline)
    tsv_gold_path = raw_dir / f"{media_stem}.csv"
    with open(tsv_gold_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow(["Name", "Start", "Duration", "Time Format", "Type", "Description"])
        for i, seg in enumerate(segments, 1):
            st = float(seg["start"])
            dur = float(seg["end"]) - st
            track_name = seg.get("name") or f"Track_{i}"
            writer.writerow([track_name, fmt_time(st), fmt_time(dur), "decimal", "Cue", ""])
            
    print(f"\n[主播: {streamer}] 成功解析 {len(segments)} 首歌曲！")
    print(f"  - 專案檔案: {llc_path.name}")
    print(f"  - 對應影片: {media_name}")
    print(f"  - Excel 打軸表: {csv_user_path}")
    print(f"  - 訓練清單:     {tsv_gold_path}")
    return 1

def process_target(target: str | Path, streamer_override: str | None = None) -> int:
    target_path = Path(target)
    if not target_path.exists():
        print(f"[!] 錯誤：找不到指定路徑: {target_path}")
        return 0
        
    if target_path.is_dir():
        llc_files = sorted(target_path.glob("**/*-proj.llc"))
        if not llc_files:
            llc_files = sorted(target_path.glob("**/*.llc"))
        if not llc_files:
            print(f"[!] 在目錄中未找到任何 .llc 專案檔: {target_path}")
            return 0
        print(f"[*] 在目錄中找到 {len(llc_files)} 個 .llc 專案檔，開始批次轉換...")
        success = 0
        for f in llc_files:
            success += convert_llc(f, streamer_override=streamer_override)
        return success
        
    elif target_path.is_file():
        suffix = target_path.suffix.lower()
        if suffix == ".llc":
            return convert_llc(target_path, streamer_override=streamer_override)
            
        elif suffix in MEDIA_EXTENSIONS:
            companion = find_companion_llc(target_path)
            if companion and companion.is_file():
                print(f"\n[★] 偵測到傳入影片檔案: {target_path.name}")
                print(f"    自動尋獲同目錄之打軸專案: {companion.name}")
                return convert_llc(companion, streamer_override=streamer_override)
            else:
                print(f"\n========================================================")
                print(f"[!] 提示：您傳入的是多媒體影片/音訊檔案：")
                print(f"    {target_path.name}")
                print(f"    但在該檔案同目錄下未找到對應的 LosslessCut 專案檔（.llc）！")
                print(f"--------------------------------------------------------")
                print(f"💡 常見使用情境引導：")
                print(f"  1. 若您是要【讓 AI 聽歌自動生成打軸時間軸 (.llc)】：")
                print(f"     👉 請改將影片拖曳到「generatellc.bat」！")
                print(f"  2. 若您是要【匯入已在 LosslessCut 標註好的時間軸】：")
                print(f"     👉 請在 LosslessCut 內點擊「File」->「Save Project」存成 .llc 專案檔，")
                print(f"        再將該 .llc 專案檔拖曳至此工具。")
                print(f"========================================================")
                return 0
        else:
            print(f"[!] 略過不支援的檔案格式（非 .llc 專案檔）: {target_path.name}")
            return 0
            
    return 0

if __name__ == "__main__":
    import sys
    targets = []
    streamer_override = None
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--streamer" and i + 1 < len(sys.argv):
            streamer_override = sys.argv[i + 1]
            i += 2
        elif arg.startswith("--streamer="):
            streamer_override = arg.split("=", 1)[1]
            i += 1
        else:
            targets.append(arg)
            i += 1

    if not targets:
        default_p = r"e:\Bilibili livestream\Bilibili live downloder\638257-秋葵AKIAOI\秋葵-2026年08月【compressed】\【秋葵AKIAOI】2026年08月16日163044 【歌杂】稍微唱会儿【老鼠干活动持续中】 cmp-proj.llc"
        if Path(default_p).exists():
            targets = [default_p]
        else:
            print("未指定任何目標檔案或目錄。")
            sys.exit(0)
            
    total_processed = sum(process_target(t, streamer_override=streamer_override) for t in targets)
    print("\n========================================================")
    if total_processed > 0:
        print(f"處理完成！共成功轉換 {total_processed} 個專案檔案。")
        sys.exit(0)
    else:
        print(f"未轉換任何專案檔案。請依上方提示檢查。")
        sys.exit(1)
