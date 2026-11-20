"""End-to-end slicer: VOD (video / audio / bilibili URL) -> song segments -> cut clips.

Usage:
  python src/infer.py --input vod.mp4 --checkpoint runs/crnn_a/best.pt --out-dir out/ \
      [--cut-video] [--gpu 0] [--device cpu]
  python src/infer.py --input "https://www.bilibili.com/video/BV1xx411c7md" \
      --checkpoint runs/v2_final/best.pt --out-dir out/ --cut-video

Outputs:
  out/<stem>.segments.json   cut list with probabilities and timing
  out/<stem>.probs.npy       raw frame probabilities (3.125 Hz) for inspection
  out/<stem>_songNN.mp4      cut clips (if --cut-video)
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).parent))
from features import FeatureConfig, LogMel, load_audio  # noqa: E402
from media_utils import clean_artist, extract_date_info, get_media_metadata, set_file_times_windows  # noqa: E402
from models import build_model  # noqa: E402
from postprocess import PostProcessConfig, probs_to_segments  # noqa: E402


@torch.no_grad()
def predict_probs(wav: torch.Tensor, model: torch.nn.Module, device: torch.device,
                  fcfg: FeatureConfig = FeatureConfig(),
                  chunk_s: float = 600.0, overlap_s: float = 30.0) -> np.ndarray:
    """Sliding-window inference over arbitrarily long audio -> probs at out_fps."""
    frontend = LogMel(fcfg).to(device)
    model = model.to(device).eval()
    sr, hop, ratio = fcfg.sample_rate, fcfg.hop_length, fcfg.time_pool
    out_fps = fcfg.output_frame_rate
    n_out_total = int(len(wav) / sr * out_fps) + 1
    probs = np.zeros(n_out_total, dtype=np.float32)
    weight = np.zeros(n_out_total, dtype=np.float32)
    chunk = int(chunk_s * sr // hop) * hop
    overlap = int(overlap_s * sr // hop) * hop
    step = chunk - overlap
    autocast_dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    for s in range(0, len(wav), step):
        seg = wav[s: s + chunk].to(device)
        if len(seg) < fcfg.n_fft:
            break
        mel = frontend(seg)
        if device.type == "cuda":
            with torch.autocast("cuda", dtype=autocast_dtype):
                logits = model(mel)[0].float().cpu().numpy()
        else:
            logits = model(mel)[0].float().cpu().numpy()
        o = int(s / hop / ratio)
        m = min(len(logits), n_out_total - o)
        if logits.ndim == 2:  # multiclass head: P(class 1 = target singing)
            e = np.exp(logits[:m] - logits[:m].max(-1, keepdims=True))
            p = e[:, 1] / e.sum(-1)
        else:
            p = 1 / (1 + np.exp(-logits[:m]))
        probs[o: o + m] += p
        weight[o: o + m] += 1
        if s + chunk >= len(wav):
            break
    return probs / np.maximum(weight, 1)



def cut_video(input_path: Path, segments: list[dict], out_dir: Path, stem: str) -> list[Path]:
    """Cut clips with stream copy when possible (fast); re-encode audio-only boundaries are
    handled by -ss before -i (keyframe snap) plus a small pad already added upstream.
    Preserves and formats metadata (artist, comment, full date for mp3tag), and sets Windows file timestamps."""
    tags = get_media_metadata(input_path)
    raw_artist = tags.get("artist") or tags.get("ARTIST")
    cleaned_artist = clean_artist(raw_artist, input_path.name)
    comment = tags.get("comment") or tags.get("description") or tags.get("COMMENT") or ""
    date_str, target_dt = extract_date_info(input_path.name, comment, tags, input_path)

    meta_args = []
    if cleaned_artist:
        meta_args.extend(["-metadata", f"artist={cleaned_artist}"])
    if comment:
        meta_args.extend(["-metadata", f"comment={comment}"])
    if date_str:
        meta_args.extend(["-metadata", f"date={date_str}"])
        meta_args.extend(["-metadata", f"year={date_str}"])

    outs = []
    for i, seg in enumerate(segments, 1):
        out = out_dir / f"{stem}_song{i:02d}.mp4"
        dur = seg["cut_end"] - seg["cut_start"]
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{seg['cut_start']:.2f}",
               "-i", str(input_path), "-t", f"{dur:.2f}"] + meta_args + [
               "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:  # fall back to re-encode (e.g. odd containers)
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{seg['cut_start']:.2f}",
                   "-i", str(input_path), "-t", f"{dur:.2f}"] + meta_args + [
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-c:a", "aac", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)

        if target_dt:
            set_file_times_windows(out, target_dt)
        outs.append(out)
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True,
                    help="local video/audio file, or a bilibili URL / BV id / b23.tv link")
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--out-dir", default="out", help="directory for analysis data (.probs.npy, .segments.json)")
    ap.add_argument("--clip-out-dir", default=None, help="directory for cut video clips and .llc (defaults to video's parent dir)")
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--device", default=None, help="'cpu' to force CPU")
    ap.add_argument("--cut-video", action="store_true")
    ap.add_argument("--on-threshold", type=float, default=None)
    ap.add_argument("--off-threshold", type=float, default=None)
    ap.add_argument("--merge-gap-s", type=float, default=None, help="maximum gap in seconds between singing segments to merge")
    ap.add_argument("--min-song-s", type=float, default=None, help="minimum song duration in seconds")
    ap.add_argument("--pad-s", type=float, default=None, help="lead-in and lead-out padding in seconds for cut clips")
    ap.add_argument("--page", type=int, default=None, help="part number for bilibili multi-part VODs")
    ap.add_argument("--cookie", default=None, help='bilibili "SESSDATA=..." for higher video quality')
    args = ap.parse_args()

    if args.device:
        device = torch.device(args.device)
    elif torch.cuda.is_available():
        device = torch.device(f"cuda:{args.gpu}")
    else:
        device = torch.device("cpu")
    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    model_name = ckpt["args"]["model"]
    model = build_model(model_name, **json.loads(ckpt["args"].get("model_kwargs", "{}")))
    model.load_state_dict(ckpt["model"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    from bilibili import is_bilibili_input, download as bili_download
    if is_bilibili_input(args.input):
        # need the video stream only if we are cutting clips; audio is enough to detect
        inp = bili_download(args.input, out_dir / "download", want_video=args.cut_video,
                            page=args.page, cookie=args.cookie)
        print(f"downloaded -> {inp}", flush=True)
    else:
        inp = Path(args.input)

    # 決定切片影片 (.mp4) 與 LosslessCut 專案檔 (.llc) 的存放目錄
    if args.clip_out_dir:
        clip_dir = Path(args.clip_out_dir)
    elif inp.is_file():
        clip_dir = inp.parent
    else:
        clip_dir = out_dir
    clip_dir.mkdir(parents=True, exist_ok=True)

    fcfg = FeatureConfig()
    t0 = time.time()
    wav = load_audio(inp, fcfg.sample_rate)
    t_load = time.time() - t0
    dur_s = len(wav) / fcfg.sample_rate

    t0 = time.time()
    probs = predict_probs(wav, model, device, fcfg)
    t_infer = time.time() - t0

    pcfg = PostProcessConfig(frame_rate=fcfg.output_frame_rate)
    if args.on_threshold is not None:
        pcfg.on_threshold = args.on_threshold
    if args.off_threshold is not None:
        pcfg.off_threshold = args.off_threshold
    if args.merge_gap_s is not None:
        pcfg.merge_gap_s = args.merge_gap_s
    if args.min_song_s is not None:
        pcfg.min_song_s = args.min_song_s
    if args.pad_s is not None:
        pcfg.pad_before_s = args.pad_s
        pcfg.pad_after_s = args.pad_s
    segments = probs_to_segments(probs, pcfg, total_duration_s=dur_s)

    # 輔助數據檔 (.probs.npy, .segments.json) 統一存放於 out_dir (例如 auto-slicer/out)
    np.save(out_dir / f"{inp.stem}.probs.npy", probs)
    result = {
        "input": str(inp), "duration_s": round(dur_s, 1),
        "model": model_name, "checkpoint": args.checkpoint,
        "decode_time_s": round(t_load, 1), "inference_time_s": round(t_infer, 1),
        "rtf": round((t_load + t_infer) / max(1, dur_s), 4),
        "segments": segments,
    }
    (out_dir / f"{inp.stem}.segments.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # 輸出 LosslessCut 專案檔 (.llc) 放置於原影片/切片目錄 (clip_dir)
    llc_data = {
        "version": 2,
        "mediaFileName": inp.name,
        "cutSegments": [
            {
                "start": round(seg["cut_start"], 3),
                "end": round(seg["cut_end"], 3),
                "name": f"Song_{i:02d}",
                "selected": True,
            }
            for i, seg in enumerate(segments, 1)
        ]
    }
    llc_content = json.dumps(llc_data, indent=2, ensure_ascii=False)
    (clip_dir / f"{inp.stem}-proj.llc").write_text(llc_content, encoding="utf-8")

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.cut_video and segments:
        t0 = time.time()
        outs = cut_video(inp, segments, clip_dir, inp.stem)
        print(f"cut {len(outs)} clips in {time.time()-t0:.1f}s -> {clip_dir}", flush=True)


if __name__ == "__main__":
    main()
