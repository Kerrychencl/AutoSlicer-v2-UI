import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
import torch

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from features import FeatureConfig, LogMel, load_audio

SEARCH_ROOTS = [
    Path(r"E:\Bilibili livestream"),
    Path(r"E:\Bilibili livestream\Bilibili live downloder"),
]

# 預設特定主播的獨立驗證集（確保測試集跨度合理）
DEFAULT_VAL_STEMS = {
    "-菫時-": {
        "【-菫時-】2026年04月11日112528 diannaoxiuhaole cmp",
        "【-菫時-】2026年05月10日114635 午安 cmp"
    },
    "秋葵": {
        "【秋葵AKIAOI】2026年08月21日160413 【歌杂】不能弹唱了因为【老鼠干活动中】 cmp",
        "【秋葵AKIAOI】2026年09月04日161104 【歌杂】突击！突击！ cmp"
    },
    "薇Steria": {
        "【薇Steria】2026年10月04日230715 晚上好哇 cmp"
    },
    "随一Suiii": {
        "【随一Suiii】2026年10月02日142032 午随歌杂 cmp"
    }
}

def find_video_file(media_name: str, streamer: str = "") -> Path | None:
    target_name = Path(media_name).name
    target_stem = Path(media_name).stem
    for base in SEARCH_ROOTS:
        if not base.exists():
            continue
        # 1. 優先在主播所屬目錄尋找
        streamer_dirs = [d for d in base.glob(f"*{streamer}*") if d.is_dir()]
        for sdir in streamer_dirs:
            cands = list(sdir.rglob(f"{target_stem}.*"))
            for c in cands:
                if c.suffix.lower() in [".mp4", ".flv", ".mkv"]:
                    return c
        # 2. 全局目錄搜尋
        cands = list(base.rglob(f"{target_stem}.*"))
        for c in cands:
            if c.suffix.lower() in [".mp4", ".flv", ".mkv"]:
                return c
    return None

def main():
    streamer = "-菫時-"
    if len(sys.argv) > 1:
        streamer = sys.argv[1].strip()
    if streamer.lower() == "薇steria":
        streamer = "薇Steria"
    elif streamer in ["隨一", "随一", "随一suiii", "隨一suiii", "随一Suiii"]:
        streamer = "随一Suiii"

    ann_dir = ROOT / "data" / "annotations" / streamer
    cache_mel = ROOT / "data" / "cache" / "mel" / streamer
    cache_lab = ROOT / "data" / "cache" / "labels" / streamer
    processed_dir = ROOT / "data" / "processed"

    cache_mel.mkdir(parents=True, exist_ok=True)
    cache_lab.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(ann_dir.glob("*.segments.json"))
    print("=" * 60)
    print(f"      【{streamer}】 資料集特徵提取與標籤生成流水線      ")
    print("=" * 60)
    print(f"共找到 {len(json_files)} 場歌回標註。\n")

    if not json_files:
        print(f"❌ 錯誤：在 {ann_dir} 找不到任何標註檔案 (*.segments.json)！")
        sys.exit(1)

    # 決定驗證集 (Validation Set)
    val_stems = DEFAULT_VAL_STEMS.get(streamer, set())
    if not val_stems and len(json_files) >= 3:
        # 若未指定且場次 >= 3，自動取 1 場作為驗證集
        val_count = max(1, round(len(json_files) * 0.2))
        step = len(json_files) // val_count
        val_stems = {json_files[min(len(json_files)-1, i * step + 1)].stem.replace(".segments", "") for i in range(val_count)}

    fcfg = FeatureConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"使用運算設備: {device}")
    logmel_module = LogMel(fcfg).to(device)

    items = []
    
    for i, jf in enumerate(json_files, 1):
        data = json.loads(jf.read_text(encoding="utf-8"))
        media_name = data.get("media_file", "")
        stem = Path(media_name).stem
        segments = data.get("segments", [])
        
        video_path = find_video_file(media_name, streamer)
        if not video_path:
            print(f"[{i:02d}/{len(json_files):02d}] ❌ 找不到對應影片: {media_name}")
            continue

        print(f"[{i:02d}/{len(json_files):02d}] 正在處理: {stem} ({len(segments)} 首歌)")
        
        mel_path = cache_mel / f"{stem}.npy"
        lab_path = cache_lab / f"{stem}.lab.npy"

        # 1. Extract & cache Log-Mel spectrogram
        if mel_path.exists():
            print(f"      - Log-Mel 快取已存在: {mel_path.name}")
            mel = np.load(mel_path, mmap_mode="r")
        else:
            t0 = time.time()
            wav = load_audio(video_path, fcfg.sample_rate).to(device)
            with torch.no_grad():
                mel_tensor = logmel_module(wav)[0]
                mel = mel_tensor.float().cpu().numpy()
            np.save(mel_path, mel)
            dur_s = len(wav) / fcfg.sample_rate
            print(f"      - 提取 Log-Mel 完成: {dur_s/60:.1f}分鐘, 耗時: {time.time()-t0:.1f}s, 形狀: {mel.shape}")

        # 2. Build frame labels
        n_out = mel.shape[1] // fcfg.time_pool
        out_fps = fcfg.output_frame_rate
        lab = np.zeros(n_out, dtype=np.float32)

        for seg in segments:
            s_frame = int(round(seg["start"] * out_fps))
            e_frame = int(round(seg["end"] * out_fps))
            lab[max(0, s_frame): min(n_out, e_frame)] = 1.0

        np.save(lab_path, lab)
        pos_pct = float(lab.mean()) * 100
        print(f"      - 標籤生成完成: {len(segments)} 首, 唱歌時長佔比: {pos_pct:.1f}%")

        items.append({
            "mel": str(mel_path),
            "label": str(lab_path),
            "vod": f"{streamer}/{stem}",
            "n_songs": len(segments),
            "pos_frac": round(float(lab.mean()), 4),
            "duration_s": round(n_out / out_fps, 1)
        })

    # 3. Split into Train & Validation
    train_items = [it for it in items if Path(it["mel"]).stem not in val_stems]
    val_items = [it for it in items if Path(it["mel"]).stem in val_stems]

    train_songs = sum(it["n_songs"] for it in train_items)
    val_songs = sum(it["n_songs"] for it in val_items)

    train_manifest = processed_dir / f"{streamer}_train.json"
    val_manifest = processed_dir / f"{streamer}_val.json"

    train_manifest.write_text(json.dumps(train_items, indent=2, ensure_ascii=False), encoding="utf-8")
    val_manifest.write_text(json.dumps(val_items, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"資料集準備完成！")
    print(f"  - 訓練集 (Train): {len(train_items)} 場錄播，共 {train_songs} 首歌曲 -> {train_manifest}")
    print(f"  - 驗證集 (Val):   {len(val_items)} 場錄播，共 {val_songs} 首歌曲 -> {val_manifest}")
    print("=" * 60)

if __name__ == "__main__":
    main()
