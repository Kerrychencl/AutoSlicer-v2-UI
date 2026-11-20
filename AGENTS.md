# AutoSlicer 專案守則與防錯指南 (AGENTS.md)

本專案為 Windows 本機直播歌回 AI 自動切片、打軸、Metadata 同步與桌面工作台。
詳細演算法與工程路線請參閱 [docs/LABELING_GUIDE.md](file:///e:/Bilibili%20livestream/auto-slicer/docs/LABELING_GUIDE.md) 與 [docs/autoslicer_roadmap.html](file:///e:/Bilibili%20livestream/auto-slicer/docs/autoslicer_roadmap.html)。

## 1. 核心開發與架構規範

1. **唯一出處原則**：編寫新函式或業務邏輯前，先檢索 `src/media_utils.py`、`gui/` 與現有模組，嚴禁複製貼上重複邏輯。
2. **檔案大小約束（Git Hook 攔截）**：單一程式碼/文字檔案嚴格控制在 20 KB（~5,000 tokens）以內。若提交被 `.git/hooks/pre-commit` 拒絕，請依功能拆分檔案後再行提交。
3. **介面無常駐 AI**：桌面端（`gui/`）嚴禁直接 `import torch` 常駐模型；所有推論、剪輯與下載一律經由 `QueueManager` 調度子行程呼叫獨立腳本。

## 2. Windows 環境防錯與自動攔截

| 潛在錯誤 | 核心規範 | 攔截防線 |
|---|---|---|
| CMD 中文編碼碰撞 | Windows `.bat` 啟動批次檔嚴格維持純 7-bit ASCII；複雜互動與 Unicode 輸出交由 `.ps1` 或 Python 負責 | `scripts/verify_install.py` (`check_ascii_bat`) |
| 簡繁字元編碼異常 | 涉及錄播標題/檔案讀寫與輸出時，強制啟用 UTF-8 模式（`PYTHONUTF8=1`、`write_text(..., encoding="utf-8")`） | `scripts/verify_install.py` (`check_unicode_handling`) |
| GPU 顯存爭用 / 無 CUDA 崩潰 | `src/infer.py` 自動探測硬體平滑 fallback 至 CPU；子行程結束立即釋放顯存 | `src/infer.py` (`torch.cuda.is_available()`) |
| 修改後回歸異常 | 每次修改後交差前，必須跑通自動化驗收測試 | `python tests/run_all_tests.py` 與 `python scripts/verify_install.py` |
