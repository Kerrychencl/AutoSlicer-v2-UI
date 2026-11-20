"""
AutoSlicer-v2 Step 0 盲測驗證腳本 (step0_blind_test.py)
========================================================
用途：
  放入「同一曲風」下的 5 首滿意歌切 (./good_clips/) 與 5 首不想留歌切 (./bad_clips/)，
  依序執行修正後的純物理聲學評分管線，輸出兩組對照表、Cohen's d 分離度與 blind_test_report.csv，
  在正式接進工具鏈前，以科學數據驗證客觀分數是否與雙耳體感一致。

硬體與架構約束 (NVIDIA RTX 5060 8GB VRAM)：
  - 嚴格採用「循序載入、用完即釋放 (del model; gc.collect(); torch.cuda.empty_cache())」，
    確保峰值顯存壓在 6GB 以下。
  - 與現有 generate_llc.bat 完全解耦，不影響 18 秒極速生軸。
  - 零粉絲濾鏡：不依賴彈幕 XML、原曲 MIDI 或歌名爬蟲，純粹以伴奏軌為客觀基準。

技術規範 (v2.0)：
  1. 人聲分離：Mel-Band-RoFormer / BS-RoFormer (audio-separator)，保留直播機架混響尾音。
  2. 客觀音準：RMVPE (或 FCPE/pYIN 備援) + 穩態音符切分中位數 Cents 偏差 + 12~15 dB 防原唱墊音
     RMS 能量門檻 + 2 秒滑動視窗 Chroma 調性餘弦相似度。
  3. 節奏與穩定：IOI 內部律動變異係數 + 伴奏鼓點能量門檻（保護抒情 Rubato）；
     高音八度破音跳變 (Pitch Break) + 長音驟降斷氣檢測（取代醫學 Jitter）。
  4. Windows 安全標籤：預覽標籤嚴禁包含 / : | 等非法字元，時間採用 01m42s 格式。

相依套件安裝（於 .venv 內執行）：
  pip install "audio-separator[gpu]" librosa soundfile torchfcpe
  （若需原生 RMVPE，可另外安裝 rmvpe 套件或於 --pitch-backend 指定 fcpe / pyin）

使用範例：
  python scripts/step0_blind_test.py --good ./good_clips --bad ./bad_clips
  python scripts/step0_blind_test.py --good ./good_clips --bad ./bad_clips --skip-separation
"""

from __future__ import annotations

import argparse
import csv
import gc
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
MEDIA_EXTS = {".mp4", ".flv", ".mkv", ".ts", ".mov", ".webm", ".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg"}
STEM_MARKER_RE = re.compile(r"(_vocals|_no_vocals|\(Vocals\)|\(Instrumental\))$", re.IGNORECASE)
LLC_PREFIX_STRIP_RE = re.compile(r"^[✅🟡🔴🎵]\s*\[.*?\]\s*")

# 預設採用 audio-separator 內建高品質 RoFormer 人聲分離權重（可透過 --sep-model 替換任意 Mel-Band-RoFormer ckpt）
DEFAULT_SEP_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


@dataclass
class ClipMetrics:
    group: str
    filename: str
    duration_s: float
    active_singing_s: float
    gated_voiced_ratio: float
    valid_notes_count: int
    steady_cents_err: float
    chroma_sim_mean: float
    key_conflict_rate: float
    pitch_accuracy_score: float
    tonality_score: float
    pitch_total_score: float
    ioi_cv: float
    percussive_ratio: float
    beat_gated: bool
    beat_align_score: float
    rhythm_score: float
    pitch_breaks_per_min: float
    breath_drops_per_min: float
    stability_score: float
    overall_score: float
    llc_preview_tag: str


# ==============================================================================
# GPU 顯存管理與基礎工具
# ==============================================================================

def release_vram(stage_name: str = "") -> None:
    """嚴格釋放 GPU 顯存，確保單張 RTX 5060 (8GB) 峰值低於 6GB。"""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            peak_gb = torch.cuda.max_memory_allocated() / (1024 ** 3)
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            cur_gb = torch.cuda.memory_allocated() / (1024 ** 3)
            torch.cuda.reset_peak_memory_stats()
            if stage_name:
                print(f"  [VRAM] {stage_name} 完成並已釋放顯存 (本階段峰值: {peak_gb:.2f} GB, 釋放後: {cur_gb:.2f} GB)")
    except Exception:
        pass


def discover_clips(folder: Path) -> List[Path]:
    """掃描資料夾中的音訊／影片歌切，自動跳過已分離的 stem 檔。"""
    if not folder.exists():
        return []
    clips = []
    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in MEDIA_EXTS:
            continue
        if STEM_MARKER_RE.search(p.stem):
            continue
        clips.append(p)
    return clips


def find_existing_stems(clip_path: Path, cache_dir: Path) -> Optional[Tuple[Path, Path]]:
    """檢查快取目錄或原目錄是否已有分離好的 vocals.wav 與 no_vocals.wav。"""
    stem = clip_path.stem
    search_dirs = [cache_dir, clip_path.parent]
    for d in search_dirs:
        if not d.exists():
            continue
        v = d / f"{stem}_vocals.wav"
        a = d / f"{stem}_no_vocals.wav"
        if v.is_file() and a.is_file():
            return v, a
        # 兼容 audio-separator 原始命名格式
        v_cands = list(d.glob(f"{stem}*(Vocals)*.wav")) + list(d.glob(f"{stem}*(Vocals)*.flac"))
        a_cands = list(d.glob(f"{stem}*(Instrumental)*.wav")) + list(d.glob(f"{stem}*(Instrumental)*.flac"))
        if v_cands and a_cands:
            return v_cands[0], a_cands[0]
    return None


def extract_wav_via_ffmpeg(media_path: Path, out_wav: Path, sr: int = 44100, channels: int = 2) -> None:
    """使用 ffmpeg 將影片或任意音訊安全轉為標準 WAV（供 RoFormer 分離使用）。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(media_path),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(sr), "-ac", str(channels),
        str(out_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0 or not out_wav.is_file():
        raise RuntimeError(f"ffmpeg 音訊解碼失敗 ({media_path.name}): {res.stderr.strip()}")


# ==============================================================================
# Stage 1: Mel-Band-RoFormer / BS-RoFormer 人聲分離（循序載入與釋放）
# ==============================================================================

def run_stage1_separation(
    all_clips: List[Path],
    cache_dir: Path,
    sep_model: str,
    skip_separation: bool,
) -> Dict[Path, Tuple[Path, Path]]:
    """
    對尚未快取的歌切執行 RoFormer 分離。
    全部歌切共用一次模型載入，完成後立即刪除模型並清空 CUDA Cache。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem_map: Dict[Path, Tuple[Path, Path]] = {}
    to_separate: List[Path] = []

    for clip in all_clips:
        existing = find_existing_stems(clip, cache_dir)
        if existing is not None:
            stem_map[clip] = existing
        else:
            to_separate.append(clip)

    print(f"\n[Stage 1/4] 人聲與伴奏分離 (目標模型: {sep_model})")
    print(f"  - 已命中分離快取: {len(stem_map)} 首 | 待分離: {len(to_separate)} 首")

    if not to_separate:
        return stem_map

    if skip_separation:
        missing_names = ", ".join(p.name for p in to_separate)
        raise FileNotFoundError(
            f"已指定 --skip-separation，但在 {cache_dir} 或來源目錄找不到以下歌切的分離音軌：\n  {missing_names}\n"
            f"請先將對應的 <檔名>_vocals.wav 與 <檔名>_no_vocals.wav 放入 {cache_dir}，或移除 --skip-separation。"
        )

    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise ImportError(
            "尚未安裝 audio-separator！請於虛擬環境執行：\n"
            '  pip install "audio-separator[gpu]" librosa soundfile torchfcpe'
        ) from exc

    sep_tmp_dir = Path(tempfile.mkdtemp(prefix="roformer_sep_"))
    separator = None
    try:
        separator = Separator(
            output_dir=str(sep_tmp_dir),
            output_format="WAV",
            normalization_threshold=0.9,
            sample_rate=44100,
        )
        print(f"  [*] 載入 RoFormer 模型權重中（首次執行將自動下載）...")
        separator.load_model(model_filename=sep_model)

        for idx, clip in enumerate(to_separate, 1):
            t0 = time.time()
            print(f"  [{idx}/{len(to_separate)}] 分離中: {clip.name}")
            src_audio = clip
            tmp_wav = None
            if clip.suffix.lower() not in {".wav", ".flac", ".mp3"}:
                tmp_wav = sep_tmp_dir / f"{clip.stem}__src.wav"
                extract_wav_via_ffmpeg(clip, tmp_wav, sr=44100, channels=2)
                src_audio = tmp_wav

            out_files = separator.separate(str(src_audio))
            voc_src, acc_src = None, None
            for f in out_files:
                fp = Path(f)
                if not fp.is_absolute():
                    fp = sep_tmp_dir / fp.name
                low = fp.name.lower()
                if "(vocals)" in low or "_vocals" in low:
                    voc_src = fp
                elif "(instrumental)" in low or "(no_vocals)" in low or "no_vocals" in low:
                    acc_src = fp

            if voc_src is None or acc_src is None:
                raise RuntimeError(f"分離輸出未能識別 vocals / instrumental 檔: {out_files}")

            voc_dst = cache_dir / f"{clip.stem}_vocals.wav"
            acc_dst = cache_dir / f"{clip.stem}_no_vocals.wav"
            shutil.move(str(voc_src), str(voc_dst))
            shutil.move(str(acc_src), str(acc_dst))
            if tmp_wav is not None and tmp_wav.exists():
                tmp_wav.unlink(missing_ok=True)

            stem_map[clip] = (voc_dst, acc_dst)
            print(f"      完成 ({time.time() - t0:.1f}s) -> 快取至 {cache_dir.name}/")
    finally:
        if separator is not None:
            del separator
        shutil.rmtree(sep_tmp_dir, ignore_errors=True)
        release_vram("Stage 1 RoFormer 分離")

    return stem_map


# ==============================================================================
# Stage 2: RMVPE (優先) / FCPE / pYIN 基頻 F0 提取（循序載入與釋放）
# ==============================================================================

def _load_mono_audio(path: Path, sr: int) -> np.ndarray:
    import librosa
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    return y.astype(np.float32)


def run_stage2_f0_extraction(
    all_clips: List[Path],
    stem_map: Dict[Path, Tuple[Path, Path]],
    pitch_backend: str,
    rmvpe_ckpt: Path,
) -> Tuple[Dict[Path, np.ndarray], str]:
    """
    對全部 vocals.wav 提取 100 fps (10ms hop, 16kHz) 的 F0 (Hz) 曲線。
    無聲幀為 0.0。提取完成立即釋放 GPU 顯存。
    """
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    chosen = pitch_backend.lower()

    # 自動偵測最佳可用後端：優先 RMVPE -> 次選 TorchFCPE (GPU) -> 備援 librosa.pyin (CPU)
    if chosen == "auto":
        has_rmvpe_pkg = False
        try:
            import rmvpe  # noqa: F401
            has_rmvpe_pkg = True
        except ImportError:
            pass
        if has_rmvpe_pkg and rmvpe_ckpt.is_file():
            chosen = "rmvpe"
        else:
            try:
                import torchfcpe  # noqa: F401
                chosen = "fcpe"
            except ImportError:
                chosen = "pyin"

    print(f"\n[Stage 2/4] 歌聲基頻 F0 提取 (後端: {chosen.upper()}, 裝置: {device})")
    if chosen != "rmvpe" and pitch_backend == "auto":
        print(f"  [提示] 未偵測到 rmvpe 套件或 {rmvpe_ckpt.name}，自動切換至 {chosen.upper()}（100 fps 高精度基頻）。")

    f0_map: Dict[Path, np.ndarray] = {}
    model = None

    try:
        if chosen == "rmvpe":
            from rmvpe import RMVPE
            if not rmvpe_ckpt.is_file():
                raise FileNotFoundError(f"找不到 RMVPE 權重檔: {rmvpe_ckpt}")
            model = RMVPE(str(rmvpe_ckpt), is_half=(device == "cuda"), device=device)
            for idx, clip in enumerate(all_clips, 1):
                voc_path, _ = stem_map[clip]
                wav_16k = _load_mono_audio(voc_path, sr=16000)
                f0 = model.infer_from_audio(wav_16k, thred=0.03)
                f0_map[clip] = np.asarray(f0, dtype=np.float32)
                print(f"  [{idx}/{len(all_clips)}] RMVPE F0 提取完成: {clip.name}")

        elif chosen == "fcpe":
            from torchfcpe import spawn_bundled_infer_model
            model = spawn_bundled_infer_model(device=device)
            sr = 16000
            hop = 160  # 10ms = 100 fps
            for idx, clip in enumerate(all_clips, 1):
                voc_path, _ = stem_map[clip]
                wav_16k = _load_mono_audio(voc_path, sr=sr)
                target_len = (len(wav_16k) // hop) + 1
                audio_t = torch.from_numpy(wav_16k).float().unsqueeze(0).unsqueeze(-1).to(device)
                with torch.no_grad():
                    f0_t = model.infer(
                        audio=audio_t,
                        sr=sr,
                        decoder_mode="local_argmax",
                        threshold=0.006,
                        f0_min=65.0,
                        f0_max=1100.0,
                        interp_uv=False,
                        output_interp_target_length=target_len,
                    )
                f0_np = f0_t.squeeze().detach().cpu().numpy().astype(np.float32)
                f0_np = np.nan_to_num(f0_np, nan=0.0)
                f0_map[clip] = f0_np
                del audio_t, f0_t
                print(f"  [{idx}/{len(all_clips)}] FCPE F0 提取完成: {clip.name}")

        elif chosen == "pyin":
            import librosa
            sr = 16000
            hop = 160  # 10ms = 100 fps
            for idx, clip in enumerate(all_clips, 1):
                voc_path, _ = stem_map[clip]
                wav_16k = _load_mono_audio(voc_path, sr=sr)
                f0, voiced_flag, _ = librosa.pyin(
                    wav_16k, fmin=65.0, fmax=1100.0, sr=sr, hop_length=hop, frame_length=1024
                )
                f0 = np.where(voiced_flag & ~np.isnan(f0), f0, 0.0).astype(np.float32)
                f0_map[clip] = f0
                print(f"  [{idx}/{len(all_clips)}] pYIN F0 提取完成: {clip.name}")
        else:
            raise ValueError(f"不支援的 pitch_backend: {pitch_backend}")
    finally:
        if model is not None:
            del model
        release_vram(f"Stage 2 {chosen.upper()} 基頻提取")

    return f0_map, chosen


# ==============================================================================
# Stage 3: 純物理聲學特徵計算 (v2.0 修正規格，純 CPU 執行、0 VRAM)
# ==============================================================================

def _align_frames(arr: np.ndarray, target_len: int) -> np.ndarray:
    """線性插值或裁切對齊幀長度至 100 fps。"""
    if len(arr) == target_len:
        return arr
    if len(arr) == 0:
        return np.zeros(target_len, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=len(arr))
    x_new = np.linspace(0.0, 1.0, num=target_len)
    return np.interp(x_new, x_old, arr).astype(np.float32)


def _compute_rms_db(y: np.ndarray, hop_length: int, frame_length: int = 1024) -> np.ndarray:
    import librosa
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length, center=True)[0]
    return (20.0 * np.log10(np.maximum(rms, 1e-7))).astype(np.float32)


def build_energy_gate_mask(
    f0_hz: np.ndarray,
    voc_rms_db: np.ndarray,
    acc_rms_db: np.ndarray,
    energy_gate_db: float = 12.0,
) -> np.ndarray:
    """
    防原唱墊音干擾之 RMS 能量門檻 (Energy Gate)：
    1. 估算伴奏活躍但無主唱（或低能量墊音漏聲）時的底噪/導唱墊音位準 bleed_floor_db。
    2. 僅保留同時滿足：
       - 有聲 (f0 > 60 Hz)
       - 主唱音量高於導唱墊音底噪 >= energy_gate_db (預設 12 dB，建議 12~15 dB)
       - 距離本曲主唱 95 分位高點不超過 24 dB（濾除殘響尾音與極微弱氣息）
       的有效主唱發聲幀。
    """
    voiced = f0_hz > 60.0
    if not np.any(voiced):
        return np.zeros_like(voiced, dtype=bool)

    # 估算主唱有效能量上限
    p95_voc = float(np.percentile(voc_rms_db[voiced], 95))

    # 估算「伴奏正在響但非主唱主音」時的墊音／分離漏聲底線
    acc_active = acc_rms_db > (float(np.percentile(acc_rms_db, 75)) - 15.0)
    bleed_candidates = voc_rms_db[acc_active & (~voiced)]
    if len(bleed_candidates) >= 30:
        # 取墊音區段的 60 分位作為典型導唱墊音底線（避免把短暫無聲氣口算太低，也避免偶發漏判拉太高）
        bleed_floor_db = float(np.percentile(bleed_candidates, 60))
    else:
        bleed_floor_db = float(np.percentile(voc_rms_db, 15))

    # 確保門檻合理：主唱需高於墊音底線 energy_gate_db，且高於絕對底噪 -45 dB
    min_voc_db = max(-45.0, p95_voc - 24.0, min(p95_voc - 6.0, bleed_floor_db + energy_gate_db))
    return voiced & (voc_rms_db >= min_voc_db)


def segment_notes(
    f0_hz: np.ndarray,
    gate_mask: np.ndarray,
    voc_rms_db: np.ndarray,
    fps: float = 100.0,
    min_note_s: float = 0.16,
    pitch_jump_semitones: float = 0.85,
) -> List[Tuple[int, int]]:
    """
    基於能量門檻與音高突變的音符切分 (Note Segmentation)。
    回傳每個有效音符的 (start_frame, end_frame)（半開區間）。
    """
    n = len(f0_hz)
    midi = np.zeros(n, dtype=np.float32)
    valid = f0_hz > 0
    midi[valid] = 69.0 + 12.0 * np.log2(f0_hz[valid] / 440.0)

    min_frames = max(4, int(round(min_note_s * fps)))
    notes: List[Tuple[int, int]] = []

    in_note = False
    start = 0

    for i in range(n):
        if not gate_mask[i]:
            if in_note:
                if i - start >= min_frames:
                    notes.append((start, i))
                in_note = False
            continue

        if not in_note:
            in_note = True
            start = i
            continue

        # 偵測換音（相鄰幀跳變 >= 0.85 半音，或音符能量驟升代表重起新字）
        dp = abs(midi[i] - midi[i - 1])
        # 局部能量谷底後急升（同音重複咬字切分）
        re_articulate = (
            i >= 2
            and (voc_rms_db[i] - voc_rms_db[i - 1] > 4.5)
            and (voc_rms_db[i - 1] < voc_rms_db[i - 2] - 2.0)
        )
        if dp >= pitch_jump_semitones or re_articulate:
            if i - start >= min_frames:
                notes.append((start, i))
            start = i

    if in_note and (n - start >= min_frames):
        notes.append((start, n))

    return notes


def compute_steady_state_pitch_metrics(
    f0_hz: np.ndarray,
    notes: List[Tuple[int, int]],
    fps: float = 100.0,
) -> Tuple[float, float, int]:
    """
    穩態音符 Cents 偏差計算：
    - 嚴禁直接對全幀 F0 算誤差！
    - 對每個切分出的音符，裁除起音過渡幀（前 20%，30~80ms）與收尾/顫音尾幀（後 25%，40~100ms），
      僅取中間穩態核心段的「中位數 MIDI 頻率」計算與最近標準半音的 Cents 偏差。
    """
    if not notes:
        return 50.0, 0.0, 0

    weighted_errs = []
    weights = []
    valid_notes = 0

    for st, ed in notes:
        dur = ed - st
        trim_head = int(np.clip(round(dur * 0.20), int(0.03 * fps), int(0.08 * fps)))
        trim_tail = int(np.clip(round(dur * 0.25), int(0.04 * fps), int(0.10 * fps)))
        core_st = st + trim_head
        core_ed = ed - trim_tail
        if core_ed - core_st < 3:
            # 較短音符至少取正中央 40%
            mid = (st + ed) // 2
            half = max(2, dur // 5)
            core_st = max(st, mid - half)
            core_ed = min(ed, mid + half)

        seg_f0 = f0_hz[core_st:core_ed]
        seg_f0 = seg_f0[seg_f0 > 60.0]
        if len(seg_f0) < 3:
            continue

        seg_midi = 69.0 + 12.0 * np.log2(seg_f0 / 440.0)
        # 中位數對 5~7Hz 對稱顫音 (Vibrato) 天然免疫
        median_midi = float(np.median(seg_midi))
        cents_err = abs(median_midi - round(median_midi)) * 100.0

        w = float(core_ed - core_st)
        weighted_errs.append(cents_err * w)
        weights.append(w)
        valid_notes += 1

    if not weights:
        return 50.0, 0.0, 0

    mean_cents = float(sum(weighted_errs) / sum(weights))
    # 映射 0~100 分：10 cents -> ~95 分，22 cents -> ~80 分，35 cents -> ~62 分，50 cents -> 40 分
    pitch_acc_score = float(np.clip(105.0 - 1.3 * mean_cents, 0.0, 100.0))
    return mean_cents, pitch_acc_score, valid_notes


def compute_sliding_chroma_metrics(
    y_voc: np.ndarray,
    y_acc: np.ndarray,
    gate_mask: np.ndarray,
    sr: int,
    hop_length: int,
    fps: float = 100.0,
    window_s: float = 2.0,
    step_s: float = 0.5,
) -> Tuple[float, float, float]:
    """
    2 秒滑動視窗 Chroma 餘弦相似度（適應轉調與借用和弦）。
    僅統計有效主唱佔比 >= 40% 的視窗。
    """
    import librosa

    chroma_voc = librosa.feature.chroma_cqt(y=y_voc, sr=sr, hop_length=hop_length)
    chroma_acc = librosa.feature.chroma_cqt(y=y_acc, sr=sr, hop_length=hop_length)

    n = min(chroma_voc.shape[1], chroma_acc.shape[1], len(gate_mask))
    chroma_voc = chroma_voc[:, :n]
    chroma_acc = chroma_acc[:, :n]
    gate = gate_mask[:n]

    win_f = max(10, int(round(window_s * fps)))
    step_f = max(5, int(round(step_s * fps)))

    sims = []
    for st in range(0, max(1, n - win_f + 1), step_f):
        ed = min(n, st + win_f)
        if np.mean(gate[st:ed]) < 0.40:
            continue
        v_vec = np.mean(chroma_voc[:, st:ed][:, gate[st:ed]], axis=1)
        a_vec = np.mean(chroma_acc[:, st:ed], axis=1)
        nv = float(np.linalg.norm(v_vec))
        na = float(np.linalg.norm(a_vec))
        if nv < 1e-6 or na < 1e-6:
            continue
        sims.append(float(np.dot(v_vec, a_vec) / (nv * na)))

    if not sims:
        return 0.5, 0.5, 50.0

    sims_arr = np.asarray(sims, dtype=np.float32)
    mean_sim = float(np.mean(sims_arr))
    conflict_rate = float(np.mean(sims_arr < 0.45))

    # 實務上同調人聲與伴奏的 2s Chroma 餘弦相似度約在 0.55~0.85；跑調常掉到 <0.45
    tonality_score = float(np.clip((mean_sim - 0.30) / 0.50 * 100.0 - conflict_rate * 25.0, 0.0, 100.0))
    return mean_sim, conflict_rate, tonality_score


def compute_rhythm_metrics(
    y_voc: np.ndarray,
    y_acc: np.ndarray,
    gate_mask: np.ndarray,
    sr: int,
    hop_length: int,
    fps: float = 100.0,
    percussive_thresh: float = 0.28,
) -> Tuple[float, float, bool, float, float]:
    """
    節奏評分（保護流行唱法搶／拖拍與抒情歌 Rubato 彈性速度）：
    1. 歌唱內部律動一致性：計算有效歌段內的 IOI (Inter-Onset Interval) 變異係數 (CV = std / mean)。
    2. 鼓點能量門檻：對伴奏做 HPSS 拆出打擊樂能量比例，僅在鼓點比例 >= percussive_thresh 時
       才啟用拍點細分對齊一致性輔助評分。
    """
    import librosa

    # 1. 計算人聲咬字起點 (Vocal Onsets) 與句內 IOI 變異係數
    onset_frames = librosa.onset.onset_detect(
        y=y_voc, sr=sr, hop_length=hop_length, backtrack=False, units="frames"
    )
    valid_onsets = [f for f in onset_frames if f < len(gate_mask) and gate_mask[f]]
    onset_times = np.asarray(valid_onsets, dtype=np.float32) / fps

    iois = np.diff(onset_times)
    # 僅統計同一樂句內的合理字距 (0.15s ~ 2.0s)，排除換氣長停頓與雜訊雙擊
    phrase_iois = iois[(iois >= 0.15) & (iois <= 2.0)]
    if len(phrase_iois) >= 8:
        # 使用穩健變異係數 (去頭尾 5% 極端值後算 std/mean)
        lo, hi = np.percentile(phrase_iois, [5, 95])
        trimmed = phrase_iois[(phrase_iois >= lo) & (phrase_iois <= hi)]
        ioi_cv = float(np.std(trimmed) / max(np.mean(trimmed), 1e-6)) if len(trimmed) >= 5 else 0.75
    else:
        ioi_cv = 0.75

    # IOI CV 典型值：穩健歌唱約 0.35~0.55，極度不熟/忽快忽慢常 > 0.70
    ioi_score = float(np.clip(125.0 - 95.0 * ioi_cv, 0.0, 100.0))

    # 2. 伴奏鼓點能量門檻 (HPSS)
    _, y_perc = librosa.effects.hpss(y_acc)
    acc_rms = float(np.sqrt(np.mean(y_acc ** 2))) + 1e-8
    perc_rms = float(np.sqrt(np.mean(y_perc ** 2)))
    percussive_ratio = float(np.clip(perc_rms / acc_rms, 0.0, 1.0))
    beat_gated = percussive_ratio >= percussive_thresh

    beat_align_score = float("nan")
    if beat_gated and len(onset_times) >= 10:
        tempo, beat_frames = librosa.beat.beat_track(y=y_perc, sr=sr, hop_length=hop_length)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
        if len(beat_times) >= 8:
            # 將每個咬字起點映射至其所屬拍內的相對相位 [0, 1)，計算 4 細分拍（16分音符）之圓周集中度 (Mean Resultant Length)
            # 採用「相位集中度」而非「絕對 0ms 距離」，因此允許歌手穩定地微拖拍 (Groove/Lay-back) 不會被誤殺！
            phases = []
            idx_arr = np.searchsorted(beat_times, onset_times) - 1
            for t, bi in zip(onset_times, idx_arr):
                if 0 <= bi < len(beat_times) - 1:
                    span = beat_times[bi + 1] - beat_times[bi]
                    if 0.25 <= span <= 1.5:
                        rel = (t - beat_times[bi]) / span
                        phases.append(rel)
            if len(phases) >= 10:
                ph = np.asarray(phases, dtype=np.float32)
                # 對齊 4 細分 (16th notes) 與 2 細分 (8th notes) 取較佳相位集中度 R ∈ [0, 1]
                r_8th = float(np.abs(np.mean(np.exp(1j * 2.0 * np.pi * 2.0 * ph))))
                r_16th = float(np.abs(np.mean(np.exp(1j * 2.0 * np.pi * 4.0 * ph))))
                concentration = max(r_8th, r_16th)
                beat_align_score = float(np.clip(concentration * 140.0 + 25.0, 0.0, 100.0))

    if beat_gated and not math.isnan(beat_align_score):
        rhythm_score = 0.60 * ioi_score + 0.40 * beat_align_score
    else:
        rhythm_score = ioi_score

    return ioi_cv, percussive_ratio, beat_gated, (0.0 if math.isnan(beat_align_score) else beat_align_score), float(rhythm_score)


def compute_stability_metrics(
    f0_hz: np.ndarray,
    gate_mask: np.ndarray,
    voc_rms_db: np.ndarray,
    notes: List[Tuple[int, int]],
    fps: float = 100.0,
) -> Tuple[float, float, float]:
    """
    氣息與翻車穩定度（放棄醫學 Jitter，避免誤殺刻意氣聲、怒音、哭腔）：
    1. 高音區突發性八度破音跳變 (Pitch Break)：
       在該曲個人高音區（>= 70 分位 MIDI）內，40ms 內無換氣直接突變 >= 7.0 半音且能量未休止。
    2. 長音音量驟降斷氣 (Breath Drop)：
       持續 >= 0.50 秒的長音中段（15%~80%，排除自然尾音漸弱），100ms 內音量突然驟降 >= 12 dB。
    """
    active_s = float(np.sum(gate_mask)) / fps
    if active_s < 5.0:
        return 0.0, 0.0, 50.0

    active_minutes = active_s / 60.0
    n = len(f0_hz)
    midi = np.zeros(n, dtype=np.float32)
    valid = f0_hz > 60.0
    midi[valid] = 69.0 + 12.0 * np.log2(f0_hz[valid] / 440.0)

    gated_midis = midi[gate_mask]
    high_thresh = float(np.percentile(gated_midis, 70)) if len(gated_midis) > 10 else 65.0

    # 1. 高音區突發性破音跳變偵測
    max_gap_f = max(1, int(round(0.04 * fps)))   # <= 40ms
    refractory_f = int(round(0.30 * fps))        # 300ms 不應期避免重複計數
    pitch_breaks = 0
    last_break = -refractory_f

    for i in range(1, n):
        if not gate_mask[i] or (i - last_break < refractory_f):
            continue
        # 往回找 40ms 內最近一個有效音高幀
        prev_idx = -1
        for k in range(1, max_gap_f + 1):
            if i - k >= 0 and gate_mask[i - k]:
                prev_idx = i - k
                break
        if prev_idx < 0:
            continue
        if max(midi[i], midi[prev_idx]) >= high_thresh:
            jump = abs(midi[i] - midi[prev_idx])
            # 確認跳變過程沒有主動收聲（能量維持在較高水準）
            if jump >= 7.0 and abs(voc_rms_db[i] - voc_rms_db[prev_idx]) < 15.0:
                pitch_breaks += 1
                last_break = i

    # 2. 長音中段音量驟降斷氣偵測
    min_sustained_f = int(round(0.50 * fps))
    drop_win_f = max(2, int(round(0.10 * fps)))  # 100ms
    breath_drops = 0

    for st, ed in notes:
        dur = ed - st
        if dur < min_sustained_f:
            continue
        inner_st = st + int(dur * 0.15)
        inner_ed = st + int(dur * 0.80)
        if inner_ed - inner_st <= drop_win_f:
            continue
        seg_db = voc_rms_db[inner_st:inner_ed]
        # 100ms 內急降 >= 12 dB
        drops = seg_db[:-drop_win_f] - seg_db[drop_win_f:]
        if float(np.max(drops)) >= 12.0:
            breath_drops += 1

    breaks_pm = pitch_breaks / active_minutes
    drops_pm = breath_drops / active_minutes

    # 每次破音扣分權重較高，每次長音斷氣次之
    stability_score = float(np.clip(100.0 - 12.0 * breaks_pm - 6.0 * drops_pm, 0.0, 100.0))
    return float(breaks_pm), float(drops_pm), stability_score


def build_windows_safe_llc_tag(
    pitch_score: float,
    rhythm_score: float,
    stability_score: float,
    pitch_breaks_pm: float,
    clip_name: str,
) -> str:
    """
    生成嚴格符合 Windows 檔名規範的 .llc 預覽標籤：
    嚴禁包含半形 / : | 等非法字元，並可被下游正則 ^[✅🟡🔴🎵]\\s*\\[.*?\\]\\s* 完整剝離。
    """
    p = int(round(pitch_score))
    r = int(round(rhythm_score))
    s = int(round(stability_score))
    overall = 0.45 * pitch_score + 0.30 * rhythm_score + 0.25 * stability_score

    if overall >= 80 and p >= 75 and s >= 75:
        icon = "✅"
    elif overall < 68 or p < 68 or s < 65:
        icon = "🔴"
    else:
        icon = "🟡"

    extra = "_破音" if pitch_breaks_pm >= 1.5 else ""
    clean_base = LLC_PREFIX_STRIP_RE.sub("", clip_name)
    return f"{icon} [音準{p}_節奏{r}_穩{s}{extra}] {clean_base}"


def evaluate_clip(
    group: str,
    clip_path: Path,
    voc_path: Path,
    acc_path: Path,
    f0_100fps: np.ndarray,
    energy_gate_db: float,
    percussive_thresh: float,
) -> ClipMetrics:
    sr = 22050
    fps = 100.0
    hop_length = int(round(sr / fps))  # 221 samples ≈ 10ms

    y_voc = _load_mono_audio(voc_path, sr=sr)
    y_acc = _load_mono_audio(acc_path, sr=sr)
    min_len = min(len(y_voc), len(y_acc))
    y_voc = y_voc[:min_len]
    y_acc = y_acc[:min_len]
    duration_s = float(min_len) / sr

    voc_rms_db = _compute_rms_db(y_voc, hop_length=hop_length)
    acc_rms_db = _compute_rms_db(y_acc, hop_length=hop_length)
    n_frames = min(len(voc_rms_db), len(acc_rms_db))
    voc_rms_db = voc_rms_db[:n_frames]
    acc_rms_db = acc_rms_db[:n_frames]
    f0_hz = _align_frames(f0_100fps, n_frames)

    # 1. RMS 能量門檻（防原唱墊音）
    gate_mask = build_energy_gate_mask(f0_hz, voc_rms_db, acc_rms_db, energy_gate_db=energy_gate_db)
    active_singing_s = float(np.sum(gate_mask)) / fps
    gated_ratio = float(np.mean(gate_mask)) if n_frames > 0 else 0.0

    # 2. 穩態音符切分 + 中位數 Cents 偏差
    notes = segment_notes(f0_hz, gate_mask, voc_rms_db, fps=fps)
    steady_cents_err, pitch_acc_score, valid_notes = compute_steady_state_pitch_metrics(f0_hz, notes, fps=fps)

    # 3. 2 秒滑動視窗 Chroma 調性匹配
    chroma_sim, key_conflict, tonality_score = compute_sliding_chroma_metrics(
        y_voc, y_acc, gate_mask, sr=sr, hop_length=hop_length, fps=fps
    )
    pitch_total = 0.65 * pitch_acc_score + 0.35 * tonality_score

    # 4. IOI 律動一致性 + 伴奏鼓點門檻
    ioi_cv, perc_ratio, beat_gated, beat_align_score, rhythm_score = compute_rhythm_metrics(
        y_voc, y_acc, gate_mask, sr=sr, hop_length=hop_length, fps=fps, percussive_thresh=percussive_thresh
    )

    # 5. 高音破音跳變 + 長音驟降斷氣
    breaks_pm, drops_pm, stability_score = compute_stability_metrics(
        f0_hz, gate_mask, voc_rms_db, notes, fps=fps
    )

    overall = 0.45 * pitch_total + 0.30 * rhythm_score + 0.25 * stability_score
    llc_tag = build_windows_safe_llc_tag(pitch_total, rhythm_score, stability_score, breaks_pm, clip_path.stem)

    return ClipMetrics(
        group=group,
        filename=clip_path.name,
        duration_s=round(duration_s, 1),
        active_singing_s=round(active_singing_s, 1),
        gated_voiced_ratio=round(gated_ratio, 3),
        valid_notes_count=valid_notes,
        steady_cents_err=round(steady_cents_err, 2),
        chroma_sim_mean=round(chroma_sim, 3),
        key_conflict_rate=round(key_conflict, 3),
        pitch_accuracy_score=round(pitch_acc_score, 1),
        tonality_score=round(tonality_score, 1),
        pitch_total_score=round(pitch_total, 1),
        ioi_cv=round(ioi_cv, 3),
        percussive_ratio=round(perc_ratio, 3),
        beat_gated=beat_gated,
        beat_align_score=round(beat_align_score, 1),
        rhythm_score=round(rhythm_score, 1),
        pitch_breaks_per_min=round(breaks_pm, 2),
        breath_drops_per_min=round(drops_pm, 2),
        stability_score=round(stability_score, 1),
        overall_score=round(overall, 1),
        llc_preview_tag=llc_tag,
    )


# ==============================================================================
# Stage 4: 組間統計 (Cohen's d) 與報表輸出
# ==============================================================================

def cohens_d(g_vals: List[float], b_vals: List[float]) -> float:
    if len(g_vals) < 2 or len(b_vals) < 2:
        return float("nan")
    a = np.asarray(g_vals, dtype=np.float64)
    b = np.asarray(b_vals, dtype=np.float64)
    va = float(np.var(a, ddof=1))
    vb = float(np.var(b, ddof=1))
    pooled = math.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / max(len(a) + len(b) - 2, 1))
    if pooled < 1e-9:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


def print_report(results: List[ClipMetrics], pitch_backend_used: str) -> None:
    good = [r for r in results if r.group == "good"]
    bad = [r for r in results if r.group == "bad"]

    print("\n" + "=" * 98)
    print(f"Step 0 盲測單曲明細表（F0 後端: {pitch_backend_used.upper()}）")
    print("=" * 98)
    header = f"{'組別':<5} {'綜合':>5} {'音準':>5} {'Cents':>6} {'調性':>5} {'節奏':>5} {'IOI_CV':>6} {'穩定':>5} {'破音/m':>6} {'斷氣/m':>6}  檔名"
    print(header)
    print("-" * 98)
    for r in sorted(results, key=lambda x: (0 if x.group == "good" else 1, -x.overall_score)):
        tag = "GOOD" if r.group == "good" else "BAD "
        short_name = (r.filename[:32] + "...") if len(r.filename) > 35 else r.filename
        print(
            f"{tag:<5} {r.overall_score:5.1f} {r.pitch_total_score:5.1f} {r.steady_cents_err:6.1f} "
            f"{r.chroma_sim_mean:5.2f} {r.rhythm_score:5.1f} {r.ioi_cv:6.3f} {r.stability_score:5.1f} "
            f"{r.pitch_breaks_per_min:6.2f} {r.breath_drops_per_min:6.2f}  {short_name}"
        )

    if good and bad:
        print("\n" + "=" * 98)
        print("兩組統計分離度分析（Cohen's d 絕對值 >= 0.8 代表該指標具有極佳區分力）")
        print("=" * 98)
        print(f"{'指標名稱':<26} {'GOOD 均值':>10} {'BAD 均值':>10} {'組間均差':>10} {'Cohen d':>10}  判定")
        print("-" * 98)

        metrics_spec = [
            ("綜合客觀分 (↑越高越好)", "overall_score", True),
            ("音準總分 (↑越高越好)", "pitch_total_score", True),
            ("穩態 Cents 誤差 (↓越低越好)", "steady_cents_err", False),
            ("2s Chroma 調性 (↑越高越好)", "chroma_sim_mean", True),
            ("節奏評分 (↑越高越好)", "rhythm_score", True),
            ("IOI 變異係數 (↓越低越好)", "ioi_cv", False),
            ("穩定度評分 (↑越高越好)", "stability_score", True),
            ("高音破音次數/分 (↓越低越好)", "pitch_breaks_per_min", False),
            ("長音斷氣次數/分 (↓越低越好)", "breath_drops_per_min", False),
        ]

        for label, field, higher_is_better in metrics_spec:
            gv = [float(getattr(r, field)) for r in good]
            bv = [float(getattr(r, field)) for r in bad]
            gm = float(np.mean(gv))
            bm = float(np.mean(bv))
            diff = gm - bm
            d = cohens_d(gv, bv)

            # 判斷方向是否正確
            correct_dir = (diff > 0) if higher_is_better else (diff < 0)
            if math.isnan(d):
                verdict = "樣本數不足(每組需>=2)"
            elif not correct_dir:
                verdict = "❌ 方向相反（請檢查是否混入不同曲風）"
            elif abs(d) >= 0.8:
                verdict = "🔥 強分離（高度有效）"
            elif abs(d) >= 0.5:
                verdict = "⚠️ 中分離（有參考價值）"
            else:
                verdict = "· 弱分離"

            d_str = f"{d:+9.2f}" if not math.isnan(d) else "      N/A"
            print(f"{label:<26} {gm:10.2f} {bm:10.2f} {diff:+10.2f} {d_str}  {verdict}")

    print("\n[Windows 安全 .llc 標籤預覽（已杜絕 / : | 非法字元）]")
    for r in results:
        print(f"  [{r.group.upper():<4}] {r.llc_preview_tag}")


def export_csv(results: List[ClipMetrics], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(results[0]).keys())
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"\n[✓] 盲測明細報表已匯出至: {out_csv.resolve()} (UTF-8 with BOM，可用 Excel 直接開啟)")


# ==============================================================================
# Main Entry
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoSlicer-v2 Step 0 同曲風歌切客觀評分盲測驗證腳本 (RTX 5060 8GB 友善)"
    )
    parser.add_argument("--good", type=Path, default=Path("./good_clips"), help="滿意歌切資料夾路徑 (預設: ./good_clips)")
    parser.add_argument("--bad", type=Path, default=Path("./bad_clips"), help="不想留歌切資料夾路徑 (預設: ./bad_clips)")
    parser.add_argument("--cache-dir", type=Path, default=Path("./step0_cache_stems"), help="分離音軌快取目錄")
    parser.add_argument("--out-csv", type=Path, default=Path("./blind_test_report.csv"), help="輸出 CSV 報表路徑")
    parser.add_argument("--sep-model", type=str, default=DEFAULT_SEP_MODEL, help="audio-separator RoFormer 模型檔名")
    parser.add_argument("--skip-separation", action="store_true", help="跳過分離階段，直接讀取快取或同目錄已分離音軌")
    parser.add_argument(
        "--pitch-backend",
        choices=["auto", "rmvpe", "fcpe", "pyin"],
        default="auto",
        help="基頻 F0 提取後端 (auto 依序嘗試 rmvpe -> fcpe -> pyin)",
    )
    parser.add_argument(
        "--rmvpe-ckpt",
        type=Path,
        default=ROOT / "pretrained" / "rmvpe.pt",
        help="本地 RMVPE 權重路徑 (預設: pretrained/rmvpe.pt)",
    )
    parser.add_argument("--energy-gate-db", type=float, default=12.0, help="防原唱墊音 RMS 能量門檻 (預設 12.0 dB)")
    parser.add_argument("--percussive-thresh", type=float, default=0.28, help="伴奏鼓點能量比例門檻 (預設 0.28)")
    args = parser.parse_args()

    print("==========================================================================")
    print("  AutoSlicer-v2 Step 0 客觀唱功評分盲測驗證腳本 (v2.0 工程升級版)")
    print("==========================================================================")
    print("⚠️  變因控制提醒：請確保 good_clips 與 bad_clips 屬於【同一位主播、同一曲風】！")

    good_clips = discover_clips(args.good)
    bad_clips = discover_clips(args.bad)

    if not good_clips and not bad_clips:
        print(f"\n[!] 尚未在目標資料夾找到任何歌切檔案：")
        print(f"    - 滿意組: {args.good.resolve()} ({len(good_clips)} 首)")
        print(f"    - 淘汰組: {args.bad.resolve()} ({len(bad_clips)} 首)")
        print("\n請先建立上述兩個資料夾，各放入約 5 首同曲風歌切（支援 .mp4/.wav/.flac/.mp3）後重新執行。")
        return 1

    print(f"\n[*] 掃描完成：GOOD 組 {len(good_clips)} 首 | BAD 組 {len(bad_clips)} 首")
    if len(good_clips) < 2 or len(bad_clips) < 2:
        print("  [提示] 建議每組至少放入 3~5 首同曲風歌切，才能計算有效的 Cohen's d 分離度。")

    # 檢查核心 DSP 相依套件
    try:
        import librosa  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        print("\n[x] 缺少必要音訊處理套件 (librosa / soundfile)。請先於虛擬環境執行：")
        print('    pip install "audio-separator[gpu]" librosa soundfile torchfcpe')
        return 1

    all_clips = good_clips + bad_clips
    group_lookup = {p: "good" for p in good_clips}
    group_lookup.update({p: "bad" for p in bad_clips})

    # Stage 1: 循序人聲分離（用完立即釋放 VRAM）
    stem_map = run_stage1_separation(
        all_clips=all_clips,
        cache_dir=args.cache_dir,
        sep_model=args.sep_model,
        skip_separation=args.skip_separation,
    )

    # Stage 2: 循序 F0 提取（用完立即釋放 VRAM）
    f0_map, backend_used = run_stage2_f0_extraction(
        all_clips=all_clips,
        stem_map=stem_map,
        pitch_backend=args.pitch_backend,
        rmvpe_ckpt=args.rmvpe_ckpt,
    )

    # Stage 3: 純 CPU 物理評分
    print("\n[Stage 3/4] 執行純物理聲學指標計算（穩態音符 / 能量門檻 / 2s Chroma / IOI / 破音斷氣）...")
    results: List[ClipMetrics] = []
    for idx, clip in enumerate(all_clips, 1):
        voc_path, acc_path = stem_map[clip]
        m = evaluate_clip(
            group=group_lookup[clip],
            clip_path=clip,
            voc_path=voc_path,
            acc_path=acc_path,
            f0_100fps=f0_map[clip],
            energy_gate_db=args.energy_gate_db,
            percussive_thresh=args.percussive_thresh,
        )
        results.append(m)
        print(f"  [{idx}/{len(all_clips)}] [{m.group.upper():<4}] 綜合={m.overall_score:5.1f} | 音準={m.pitch_total_score:5.1f} | 節奏={m.rhythm_score:5.1f} | 穩定={m.stability_score:5.1f}  ({clip.name})")

    # Stage 4: 輸出對照表、Cohen's d 與 CSV
    print_report(results, backend_used)
    export_csv(results, args.out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
