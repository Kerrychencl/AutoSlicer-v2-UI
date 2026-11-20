"""Verification script for AutoSlicer-v2 Windows installation."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def check_ascii_bat():
    bat_path = ROOT / "run_slice.bat"
    assert bat_path.exists(), "run_slice.bat does not exist"
    data = bat_path.read_bytes()
    non_ascii = [b for b in data if b > 127]
    assert len(non_ascii) == 0, f"run_slice.bat contains non-ASCII bytes ({len(non_ascii)}), which causes CP950/Big5 cmd corruption"
    print("[PASS] run_slice.bat is 100% pure 7-bit ASCII.")

def check_ps1_syntax():
    ps1_path = ROOT / "run_slice.ps1"
    assert ps1_path.exists(), "run_slice.ps1 does not exist"
    cmd = ["powershell.exe", "-NoProfile", "-Command", f"& {{ [void][System.Management.Automation.Language.Parser]::ParseFile('{ps1_path}', [ref]$null, [ref]$errors); if ($errors) {{ exit 1 }} }}"]
    res = subprocess.run(cmd, capture_output=True)
    assert res.returncode == 0, "run_slice.ps1 has PowerShell syntax errors"
    print("[PASS] run_slice.ps1 syntax is valid.")

def check_checkpoint():
    ckpt_path = ROOT / "best.pt"
    assert ckpt_path.exists(), "best.pt does not exist"
    import torch
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    assert "model" in ckpt, "Checkpoint missing 'model' key"
    print(f"[PASS] best.pt is valid (model: {ckpt['args'].get('model', 'unknown')}).")

def check_end_to_end():
    test_audio = ROOT / "test_sample.mp4"
    if not test_audio.exists():
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "aac", str(test_audio)], check=True, capture_output=True)
    out_dir = ROOT / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    bat_file = str(ROOT / "run_slice.bat")
    cmd = f'cmd.exe /s /c ""{bat_file}" "{test_audio}""'
    res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    assert res.returncode == 0, f"run_slice.bat execution failed: {res.stderr}\n{res.stdout}"
    assert (out_dir / "test_sample.segments.json").exists(), "Missing segments.json output"
    try:
        if test_audio.exists():
            test_audio.unlink()
        (out_dir / "test_sample.segments.json").unlink(missing_ok=True)
        (out_dir / "test_sample.probs.npy").unlink(missing_ok=True)
        (ROOT / "test_sample-proj.llc").unlink(missing_ok=True)
    except Exception:
        pass
    print("[PASS] End-to-end execution of run_slice.bat via cmd.exe succeeded.")

def check_unicode_handling():
    unicode_audio = ROOT / "【歌杂】稍微唱会儿【老鼠干活动持续中】.mp4"
    if not unicode_audio.exists():
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(unicode_audio)], check=True, capture_output=True)
    out_dir = ROOT / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    bat_file = str(ROOT / "run_slice.bat")
    cmd = f'cmd.exe /s /c ""{bat_file}" "{unicode_audio}""'
    res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    assert res.returncode == 0, f"run_slice.bat Unicode execution failed: {res.stderr}\n{res.stdout}"
    
    json_path = out_dir / f"{unicode_audio.stem}.segments.json"
    assert json_path.exists(), f"Missing Unicode segments.json output: {json_path}"
    content = json_path.read_text(encoding="utf-8")
    assert "稍微唱会儿" in content, "Unicode content in segments.json was corrupted or missing"
    try:
        if unicode_audio.exists():
            unicode_audio.unlink()
        json_path.unlink(missing_ok=True)
        (out_dir / f"{unicode_audio.stem}.probs.npy").unlink(missing_ok=True)
        (ROOT / f"{unicode_audio.stem}-proj.llc").unlink(missing_ok=True)
    except Exception:
        pass
    print("[PASS] Unicode (non-CP950 Simplified Chinese) file handling succeeded.")

if __name__ == "__main__":
    passed = 0
    total = 5
    try:
        check_ascii_bat()
        passed += 1
        check_ps1_syntax()
        passed += 1
        check_checkpoint()
        passed += 1
        check_end_to_end()
        passed += 1
        check_unicode_handling()
        passed += 1
        print(f"\nAll verification tests passed: {passed}/{total} (Failed: 0)")
        sys.exit(0)
    except Exception as e:
        print(f"\nVerification test failed: {e}", file=sys.stderr)
        sys.exit(1)
