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


if __name__ == "__main__":
    unittest.main()
