"""Unit tests for CLI script argument parsing and non-interactive handling.
Tests:
- src/infer.py (--merge-gap-s, --min-song-s, --pad-s)
- scripts/sync_clip_metadata.py (--no-creation-time, --keep-qc-tags, EOFError non-interactive)
- scripts/convert_llc_to_annotations.py (--streamer override)
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestCLIScripts(unittest.TestCase):
    def test_infer_help_contains_new_args(self):
        """Verify src/infer.py includes new post-processing parameters in CLI."""
        cmd = [sys.executable, str(PROJECT_ROOT / "src" / "infer.py"), "--help"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0)
        self.assertIn("--merge-gap-s", res.stdout)
        self.assertIn("--min-song-s", res.stdout)
        self.assertIn("--pad-s", res.stdout)

    def test_sync_metadata_flags_and_non_interactive_eof(self):
        """Verify scripts/sync_clip_metadata.py handles flags and EOF without hanging."""
        # Non-interactive invocation with empty stdin (EOF)
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "sync_clip_metadata.py"),
            "--no-creation-time",
            "--keep-qc-tags",
        ]
        res = subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        # Should exit gracefully with message about no input
        self.assertEqual(res.returncode, 0)
        self.assertIn("未輸入任何檔案或目錄", res.stdout)

    def test_convert_llc_streamer_flag(self):
        """Verify scripts/convert_llc_to_annotations.py parses --streamer without error."""
        # Pass non-existent file with --streamer
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "convert_llc_to_annotations.py"),
            "--streamer",
            "秋葵",
            "non_existent_file.llc",
        ]
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        # It should process targets and report file not found instead of unrecognized argument
        self.assertIn("找不到指定路徑: non_existent_file.llc", res.stdout)
        self.assertNotIn("unrecognized", res.stderr.lower())


if __name__ == "__main__":
    unittest.main()
