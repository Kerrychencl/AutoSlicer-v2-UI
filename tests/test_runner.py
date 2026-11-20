"""Unit tests for ProcessRunner, CommandBuilder, and process tree termination."""
import os
import sys
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

# Ensure QCoreApplication exists for Qt signals
app = QCoreApplication.instance() or QCoreApplication(sys.argv)

from gui.runner import CommandBuilder, ProcessRunner, sanitize_text


class TestRunnerAndCommands(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(r"E:\Bilibili livestream\auto-slicer")

    def test_sanitize_text(self):
        """Verify SESSDATA and cookies are masked properly."""
        raw_log = "Processing with cookie: SESSDATA=secret_token_12345; Path=/; Domain=.bilibili.com"
        clean = sanitize_text(raw_log)
        self.assertNotIn("secret_token_12345", clean)
        self.assertIn("SESSDATA=***", clean)

        raw_cmd = "python src/infer.py --input test.mp4 --cookie SESSDATA=my_secret_token"
        clean_cmd = sanitize_text(raw_cmd)
        self.assertNotIn("my_secret_token", clean_cmd)
        self.assertIn("--cookie ***", clean_cmd)

    def test_build_infer_command(self):
        """Verify CommandBuilder matches src/infer.py CLI options."""
        cmd = CommandBuilder.build_infer_command(
            repo_root=self.repo_root,
            input_path="E:/test/vod.mp4",
            checkpoint="runs/best.pt",
            out_dir="out_dir",
            clip_out_dir="clip_dir",
            cut_video=True,
            on_threshold=0.65,
            off_threshold=0.35,
            device="cpu",
            sessdata="sess_val",
        )

        self.assertIn("-u", cmd)
        self.assertTrue(any("infer.py" in arg for arg in cmd))
        self.assertIn("--input", cmd)
        self.assertEqual(cmd[cmd.index("--input") + 1], "E:/test/vod.mp4")
        self.assertIn("--checkpoint", cmd)
        self.assertEqual(cmd[cmd.index("--checkpoint") + 1], "runs/best.pt")
        self.assertIn("--out-dir", cmd)
        self.assertEqual(cmd[cmd.index("--out-dir") + 1], "out_dir")
        self.assertIn("--clip-out-dir", cmd)
        self.assertEqual(cmd[cmd.index("--clip-out-dir") + 1], "clip_dir")
        self.assertIn("--cut-video", cmd)
        self.assertIn("--on-threshold", cmd)
        self.assertEqual(cmd[cmd.index("--on-threshold") + 1], "0.65")
        self.assertIn("--off-threshold", cmd)
        self.assertEqual(cmd[cmd.index("--off-threshold") + 1], "0.35")
        self.assertIn("--device", cmd)
        self.assertEqual(cmd[cmd.index("--device") + 1], "cpu")
        self.assertIn("--cookie", cmd)
        self.assertEqual(cmd[cmd.index("--cookie") + 1], "SESSDATA=sess_val")

    def test_build_infer_command_extended_args(self):
        """Verify new optional args: merge_gap_s, min_song_s, pad_s."""
        cmd = CommandBuilder.build_infer_command(
            repo_root=self.repo_root,
            input_path="E:/test/vod.mp4",
            checkpoint="runs/best.pt",
            merge_gap_s=75.0,
            min_song_s=40.0,
            pad_s=3.5,
        )
        self.assertIn("--merge-gap-s", cmd)
        self.assertEqual(cmd[cmd.index("--merge-gap-s") + 1], "75.0")
        self.assertIn("--min-song-s", cmd)
        self.assertEqual(cmd[cmd.index("--min-song-s") + 1], "40.0")
        self.assertIn("--pad-s", cmd)
        self.assertEqual(cmd[cmd.index("--pad-s") + 1], "3.5")

    def test_build_bilibili_download_command(self):
        """Verify CommandBuilder matches src/bilibili.py positional input and options."""
        cmd = CommandBuilder.build_bilibili_download_command(
            repo_root=self.repo_root,
            input_url_or_bv="BV1xx411c7md",
            out_dir="download_dir",
            video=True,
            page=2,
            sessdata="sess_cookie",
        )

        self.assertTrue(any("bilibili.py" in arg for arg in cmd))
        # BV is positional right after script path
        script_idx = [i for i, a in enumerate(cmd) if "bilibili.py" in a][0]
        self.assertEqual(cmd[script_idx + 1], "BV1xx411c7md")

        self.assertIn("--out-dir", cmd)
        self.assertEqual(cmd[cmd.index("--out-dir") + 1], "download_dir")
        self.assertIn("--video", cmd)
        self.assertIn("--page", cmd)
        self.assertEqual(cmd[cmd.index("--page") + 1], "2")
        self.assertIn("--cookie", cmd)
        self.assertEqual(cmd[cmd.index("--cookie") + 1], "SESSDATA=sess_cookie")

    def test_build_sync_metadata_command(self):
        """Verify CommandBuilder matches scripts/sync_clip_metadata.py positional targets."""
        targets = ["E:/clips/clip1.mp4", "E:/clips/clip2.mp4", "E:/ref.xml"]
        cmd = CommandBuilder.build_sync_metadata_command(
            repo_root=self.repo_root,
            target_paths=targets,
        )

        self.assertTrue(any("sync_clip_metadata.py" in arg for arg in cmd))
        script_idx = [i for i, a in enumerate(cmd) if "sync_clip_metadata.py" in a][0]
        self.assertEqual(cmd[script_idx + 1:], targets)

    def test_build_sync_metadata_flags(self):
        """Verify sync_creation_time=False and strip_qc_tags=False append respective flags."""
        cmd = CommandBuilder.build_sync_metadata_command(
            repo_root=self.repo_root,
            target_paths=["clip.mp4"],
            sync_creation_time=False,
            strip_qc_tags=False,
        )
        self.assertIn("--no-creation-time", cmd)
        self.assertIn("--keep-qc-tags", cmd)
        self.assertIn("clip.mp4", cmd)

    def test_build_import_llc_command(self):
        """Verify CommandBuilder matches scripts/convert_llc_to_annotations.py positional targets."""
        targets = ["E:/proj1.llc", "E:/proj2-proj.llc"]
        cmd = CommandBuilder.build_import_llc_command(
            repo_root=self.repo_root,
            target_paths=targets,
        )

        self.assertTrue(any("convert_llc_to_annotations.py" in arg for arg in cmd))
        script_idx = [i for i, a in enumerate(cmd) if "convert_llc_to_annotations.py" in a][0]
        self.assertEqual(cmd[script_idx + 1:], targets)

    def test_build_import_llc_streamer_flag(self):
        """Verify streamer override flag."""
        cmd = CommandBuilder.build_import_llc_command(
            repo_root=self.repo_root,
            target_paths=["proj.llc"],
            streamer="秋葵",
        )
        self.assertIn("--streamer", cmd)
        self.assertEqual(cmd[cmd.index("--streamer") + 1], "秋葵")
        self.assertIn("proj.llc", cmd)

    def test_incremental_utf8_decoding(self):
        """Verify multi-byte UTF-8 bytes split across chunks decode without mojibake."""
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        line_buffer = ""
        emitted_lines = []

        # "歌回直播 -菫時-" encoded in UTF-8
        full_text = "歌回直播 -菫時-\n第二行 45%\n"
        full_bytes = full_text.encode("utf-8")

        # Split intentionally in the middle of a 3-byte Chinese character
        chunk1 = full_bytes[:5]
        chunk2 = full_bytes[5:]

        d1 = decoder.decode(chunk1, final=False)
        line_buffer += d1
        while "\n" in line_buffer:
            idx = line_buffer.find("\n")
            emitted_lines.append(line_buffer[:idx])
            line_buffer = line_buffer[idx + 1:]

        d2 = decoder.decode(chunk2, final=True)
        line_buffer += d2
        while "\n" in line_buffer:
            idx = line_buffer.find("\n")
            emitted_lines.append(line_buffer[:idx])
            line_buffer = line_buffer[idx + 1:]

        self.assertEqual(len(emitted_lines), 2)
        self.assertEqual(emitted_lines[0], "歌回直播 -菫時-")
        self.assertEqual(emitted_lines[1], "第二行 45%")

    def test_progress_parsing(self):
        """Verify regex progress parser correctly parses various outputs."""
        runner = ProcessRunner()
        parsed_pcts = []
        runner.progress_changed.connect(lambda p: parsed_pcts.append(p))

        runner._parse_progress("Processing video: 45%")
        self.assertEqual(parsed_pcts[-1], 45)

        runner._parse_progress("[3/10] Slicing chunks...")
        self.assertEqual(parsed_pcts[-1], 30)

        runner._parse_progress("100%|██████████| 50/50 [00:15<00:00, 3.20it/s]")
        self.assertEqual(parsed_pcts[-1], 100)


if __name__ == "__main__":
    unittest.main()
