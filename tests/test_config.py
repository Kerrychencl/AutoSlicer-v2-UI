"""Unit tests for config.json generation and ConfigManager backward-compatibility."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = Path(self.test_dir) / "config.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_config_schema(self):
        """Verify default config structure adheres strictly to Section 5."""
        from gui.config import ConfigManager

        cm = ConfigManager(config_path=self.config_path)
        cfg = cm.data

        self.assertEqual(cfg.get("version"), 1)
        self.assertIn("paths", cfg)
        self.assertIn("streamer_model_map", cfg)
        self.assertIn("inference_defaults", cfg)
        self.assertIn("metadata_defaults", cfg)
        self.assertIn("bilibili", cfg)
        self.assertIn("ui", cfg)

        # Paths checks
        paths = cfg["paths"]
        self.assertIn("watch_dirs", paths)
        self.assertIsInstance(paths["watch_dirs"], list)
        self.assertIn("losslesscut_exe", paths)
        self.assertEqual(paths.get("runs_dir"), "runs")
        self.assertEqual(paths.get("annotations_dir"), "data/annotations")

        # Inference defaults checks
        infer = cfg["inference_defaults"]
        self.assertEqual(infer.get("mode"), "llc")
        self.assertEqual(infer.get("on_threshold"), 0.6)
        self.assertEqual(infer.get("off_threshold"), 0.4)
        self.assertEqual(infer.get("merge_gap_s"), 90)
        self.assertEqual(infer.get("min_song_s"), 50)
        self.assertEqual(infer.get("pad_s"), 5)

        # Metadata defaults checks
        meta = cfg["metadata_defaults"]
        self.assertTrue(meta.get("sync_creation_time"))
        self.assertTrue(meta.get("strip_qc_tags"))

        # Bilibili defaults checks
        bili = cfg["bilibili"]
        self.assertIn("sessdata", bili)
        self.assertFalse(bili.get("prefer_video"))

        # UI defaults checks
        ui = cfg["ui"]
        self.assertEqual(ui.get("window_width"), 1200)
        self.assertEqual(ui.get("window_height"), 780)
        self.assertFalse(ui.get("log_drawer_expanded"))
        self.assertEqual(ui.get("last_streamer_filter"), "全部")

    def test_backward_compatibility_auto_fill(self):
        """Verify missing fields in legacy config are automatically backfilled."""
        from gui.config import ConfigManager

        # Create incomplete legacy config
        legacy_data = {
            "version": 1,
            "paths": {
                "watch_dirs": [{"label": "舊錄播", "path": "D:/OldRecordings", "enabled": True}]
            }
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f, ensure_ascii=False, indent=2)

        cm = ConfigManager(config_path=self.config_path)
        cfg = cm.data

        # Custom watch_dirs preserved
        self.assertEqual(len(cfg["paths"]["watch_dirs"]), 1)
        self.assertEqual(cfg["paths"]["watch_dirs"][0]["path"], "D:/OldRecordings")

        # Missing sections auto-filled
        self.assertEqual(cfg["paths"]["runs_dir"], "runs")
        self.assertIn("inference_defaults", cfg)
        self.assertEqual(cfg["inference_defaults"]["on_threshold"], 0.6)
        self.assertIn("metadata_defaults", cfg)
        self.assertTrue(cfg["metadata_defaults"]["sync_creation_time"])
        self.assertIn("bilibili", cfg)
        self.assertIn("ui", cfg)
        self.assertEqual(cfg["ui"]["window_width"], 1200)

    def test_save_and_reload(self):
        """Verify saving persists updates to disk."""
        from gui.config import ConfigManager

        cm = ConfigManager(config_path=self.config_path)
        cm.data["ui"]["window_width"] = 1440
        cm.data["bilibili"]["sessdata"] = "test_cookie_123"
        cm.save()

        cm2 = ConfigManager(config_path=self.config_path)
        self.assertEqual(cm2.data["ui"]["window_width"], 1440)
        self.assertEqual(cm2.data["bilibili"]["sessdata"], "test_cookie_123")

    def test_set_losslesscut_exe(self):
        """Verify set_losslesscut_exe sets path and persists if auto_save."""
        from gui.config import ConfigManager

        cm = ConfigManager(config_path=self.config_path)
        test_exe = "D:/Tools/LosslessCut.exe"
        cm.set_losslesscut_exe(test_exe, auto_save=True)

        cm2 = ConfigManager(config_path=self.config_path)
        self.assertEqual(cm2.data["paths"]["losslesscut_exe"], test_exe)

    def test_dirty_keys_cleanup(self):
        """Verify legacy flat dirty keys like 'ui.window_width' are migrated into nested dict and removed."""
        from gui.config import ConfigManager

        dirty_json = {
            "version": 1,
            "ui.window_width": 1600,
            "ui.window_height": 900,
            "ui.log_drawer_expanded": True,
            "paths": {"watch_dirs": []},
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(dirty_json, f, indent=2)

        cm = ConfigManager(config_path=self.config_path)
        cfg = cm.data

        # Top-level dirty keys must be purged
        self.assertNotIn("ui.window_width", cfg)
        self.assertNotIn("ui.window_height", cfg)
        self.assertNotIn("ui.log_drawer_expanded", cfg)

        # Values migrated into nested 'ui' dict
        self.assertEqual(cfg["ui"]["window_width"], 1600)
        self.assertEqual(cfg["ui"]["window_height"], 900)
        self.assertTrue(cfg["ui"]["log_drawer_expanded"])

    def test_update_ui_config(self):
        """Verify update_ui_config writes and persists changes."""
        from gui.config import ConfigManager

        cm = ConfigManager(config_path=self.config_path)
        cm.update_ui_config({"window_width": 1366, "window_height": 768, "log_drawer_expanded": True})

        cm2 = ConfigManager(config_path=self.config_path)
        ui = cm2.get_ui_config()
        self.assertEqual(ui["window_width"], 1366)
        self.assertEqual(ui["window_height"], 768)
        self.assertTrue(ui["log_drawer_expanded"])


if __name__ == "__main__":
    unittest.main()
