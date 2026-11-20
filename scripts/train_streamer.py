import os
import sys
import time
import subprocess
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

def train_streamer(streamer: str = "-菫時-", steps: int = 6000, batch_size: int = 16, val_every: int = 500):
    if streamer.lower() == "薇steria":
        streamer = "薇Steria"
    elif streamer in ["隨一", "随一", "随一suiii", "隨一suiii", "随一Suiii"]:
        streamer = "随一Suiii"
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    
    print("=" * 60, flush=True)
    print(f"       【{streamer}】 自動化模型訓練流水線", flush=True)
    print("=" * 60, flush=True)
    
    # Step 1: Prepare dataset (Mel extraction + labels + manifests)
    print("\n[階段 1/3] 準備特徵快取與標籤...", flush=True)
    prep_script = ROOT / "scripts" / "prepare_dataset.py"
    r = subprocess.run([str(venv_python), "-u", str(prep_script), streamer])
    if r.returncode != 0:
        print("❌ 特徵提取失敗，終止訓練。", flush=True)
        return False
        
    train_manifest = ROOT / "data" / "processed" / f"{streamer}_train.json"
    val_manifest = ROOT / "data" / "processed" / f"{streamer}_val.json"
    
    if not train_manifest.exists() or not val_manifest.exists():
        print(f"❌ 找不到訓練清單: {train_manifest}", flush=True)
        return False

    # Step 2: Run training
    print("\n[階段 2/3] 啟動 PyTorch 模型訓練...", flush=True)
    train_script = ROOT / "src" / "train.py"
    cmd = [
        str(venv_python), "-u", str(train_script),
        "--model", "crnn",
        f"--run-name={streamer}",
        f"--manifest={train_manifest}",
        f"--val-manifest={val_manifest}",
        f"--steps={steps}",
        f"--batch-size={batch_size}",
        f"--val-every={val_every}",
        "--num-workers=0"
    ]
    
    t0 = time.time()
    r = subprocess.run(cmd)
    train_time = time.time() - t0
    
    # Step 3: Verify outputs
    best_pt = ROOT / "runs" / streamer / "best.pt"
    if best_pt.exists():
        size_mb = best_pt.stat().st_size / (1024 * 1024)
        print("\n" + "=" * 60)
        print(f"🎉 訓練全部完成！耗時: {train_time/60:.1f} 分鐘")
        print(f"專屬模型已儲存至: {best_pt} ({size_mb:.1f} MB)")
        print("=" * 60)
        return True
    else:
        print(f"❌ 訓練過程異常 (Return Code: {r.returncode})，未能找到生成的 best.pt。")
        return False

if __name__ == "__main__":
    streamer_name = "-菫時-"
    if len(sys.argv) > 1:
        streamer_name = sys.argv[1]
    train_streamer(streamer_name)
