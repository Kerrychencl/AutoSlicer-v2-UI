"""Unit tests for scanner rules, file classification, and state machine determination."""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from gui.models import VodStatus, WatchDirConfig
from gui.scanner import (
    is_clip_filename,
    strip_clip_suffixes,
    mark_clips_synced,
    load_metadata_cache,
    VodScanner,
)
from gui.config import ConfigManager


class TestScannerRules(unittest.TestCase):
    def setUp(self):
        self.test_root = tempfile.mkdtemp()
        self.watch_dir = Path(self.test_root) / "recordings"
        self.streamer_dir = self.watch_dir / "秋葵"
        self.streamer_dir.mkdir(parents=True)
        self.annotations_dir = Path(self.test_root) / "data" / "annotations"
        self.annotations_dir.mkdir(parents=True)
        self.cache_file = Path(self.test_root) / ".autoslicer_gui_cache.json"

        # Mock config
        self.config_path = Path(self.test_root) / "config.json"
        self.cm = ConfigManager(config_path=self.config_path)
        self.cm.set_watch_dirs([WatchDirConfig(label="測試", path=str(self.watch_dir), enabled=True)])
        self.cm.data["paths"]["annotations_dir"] = str(self.annotations_dir)
        self.cm.save()

    def tearDown(self):
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_clip_classification_rules(self):
        """Test classification of clip vs original VOD filename patterns according to Section 4.1."""
        clip_names = [
            "【秋葵AKIAOI】2026年08月16日-00.38.41.493-00.41.49.763.mp4",
            "【秋葵】2026-08-16_song01.mp4",
            "【秋葵】2026-08-16_song12.flv",
            "【秋葵】2026-08-16-seg01.mp4",
            "【秋葵】2026-08-16_cut_01.mkv",
            "[✅ 91分] 【秋葵】2026-08-16-00.38.41.493-00.41.49.763.mp4",
            "[🟡 疑忘詞@01m42s] 【秋葵】2026-08-16_song02.mp4",
            "【秋葵】2026-08-16 [✅ 88分].mp4",
        ]

        vod_names = [
            "【秋葵AKIAOI】2026年08月16日163044 【歌杂】稍微唱会儿.mp4",
            "2026-08-16-秋葵歌回.flv",
            "【-菫時-】2026年05月01日200000 歌回.mp4",
            "【随一Suiii】2026年03月12日193000 【全熟】生日歌回.mp4",
        ]

        for name in clip_names:
            self.assertTrue(
                is_clip_filename(name),
                f"Expected '{name}' to be classified as a clip",
            )

        for name in vod_names:
            self.assertFalse(
                is_clip_filename(name),
                f"Expected '{name}' to be classified as an original VOD",
            )

    def test_strip_clip_suffixes(self):
        """Test stripping clip timecodes and QC tags."""
        raw = "[✅ 91分] 【秋葵】2026-08-16-00.38.41.493-00.41.49.763"
        stripped = strip_clip_suffixes(raw)
        self.assertEqual(stripped, "【秋葵】2026-08-16")

    def test_state_machine_transitions(self):
        """Test state machine determinations for states 1 to 5."""
        vod_name = "【秋葵】2026年08月16日163044 歌回"
        vod_file = self.streamer_dir / f"{vod_name}.mp4"
        vod_file.write_bytes(b"\x00" * 1024)

        scanner = VodScanner(config_manager=self.cm, cache_file=self.cache_file)

        # State 1: 待打軸 (VOD exists, no .llc)
        items = scanner.scan_all()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, VodStatus.WAITING_LLC)
        self.assertEqual(item.status, 1)

        # State 2: 已生 .llc (待剪輯) (.llc exists, no clips, not imported)
        llc_file = self.streamer_dir / f"{vod_name}-proj.llc"
        llc_file.write_text('{"version":2,"cutSegments":[]}', encoding="utf-8")

        items = scanner.scan_all()
        self.assertEqual(items[0].status, VodStatus.LLC_GENERATED)
        self.assertEqual(items[0].status, 2)

        # State 3: 有切片待修 Metadata (1+ clips exist, not in cache)
        clip_file = self.streamer_dir / f"{vod_name}-00.10.00.000-00.13.00.000.mp4"
        clip_file.write_bytes(b"\x00" * 256)

        items = scanner.scan_all()
        self.assertEqual(items[0].status, VodStatus.HAS_CLIPS_NEED_METADATA)
        self.assertEqual(items[0].status, 3)

        # Update cache to mark clip as synced
        mark_clips_synced([clip_file], cache_path=self.cache_file)
        import gui.scanner
        gui.scanner.CACHE_FILE = self.cache_file

        # State 4: 待匯入標註 (.llc exists, clips synced, but not in annotations_dir)
        items = scanner.scan_all()
        self.assertEqual(items[0].status, VodStatus.WAITING_IMPORT_ANNOTATION)
        self.assertEqual(items[0].status, 4)

        # Mark as imported by creating annotation record in annotations_dir
        time.sleep(0.05)
        anno_streamer_dir = self.annotations_dir / "秋葵"
        anno_streamer_dir.mkdir(parents=True, exist_ok=True)
        anno_json = anno_streamer_dir / f"{vod_name}.segments.json"
        anno_json.write_text('{"segments":[]}', encoding="utf-8")

        # State 5: 已完成
        items = scanner.scan_all()
        self.assertEqual(items[0].status, VodStatus.COMPLETED)
        self.assertEqual(items[0].status, 5)


if __name__ == "__main__":
    unittest.main()
