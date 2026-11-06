"""本地文件抽帧与测量的时限回归（analyze-content-video/scripts/extract_local_frames.py）。

生产上 4 vCPU 宿主单条命令 60 秒硬上限：旧版把测量和抽帧挤在一条命令里，测量对整条视频做三遍
全分辨率解码，大文件必超时，宿主什么都不收，已抽好的帧一起丢。这里钉住修法：测量两遍、各只解码
自己读的那一路；命令到 --deadline-s 自己停下并写出已有结果；帧在测量前落盘；--signals-only 单独测量。
mock 用例不依赖媒体工具链；端到端用例在本机没有 ffmpeg/ffprobe 时跳过。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "analyze-content-video" / "scripts" / "extract_local_frames.py"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def load() -> object:
    spec = importlib.util.spec_from_file_location("extract_local_frames", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MeasurementPassTest(unittest.TestCase):
    """What production showed: three full-resolution decodes of a 186 MB file did not fit in the
    minute a host allows one command, and the host kept nothing -- not even frames already written.
    These pin the cure without needing a media toolchain: two passes, each decoding only what it
    reads, and a pass stopped at the deadline kept as far as it got."""

    def setUp(self) -> None:
        self.module = load()
        self.calls: list[list[str]] = []

    def fake_tool(self, outcomes):
        def run(name, args, timeout):
            self.calls.append([name, *args])
            outcome = outcomes[len(self.calls) - 1]
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess([name, *args], 0, "", outcome)
        return run

    def measure(self, outcomes, has_audio=True, deadline_in=60.0):
        clock = types.SimpleNamespace(monotonic=lambda: 1000.0)
        with mock.patch.object(self.module, "run_tool", self.fake_tool(outcomes)), \
                mock.patch.object(self.module, "tool_version", lambda name: "ffmpeg test"), \
                mock.patch.object(self.module, "time", clock):
            return self.module.measure_signals(Path("/clips/cut.mp4"), 60.0, has_audio, 1000.0 + deadline_in)

    def test_sound_and_picture_are_each_decoded_once_and_only_what_they_read(self) -> None:
        signals, steps, _ = self.measure([
            "[silencedetect] silence_start: 2\n[silencedetect] silence_end: 3 | silence_duration: 1\n",
            "lavfi.scd.time: 4.2\nlavfi.freezedetect.freeze_start: 10\nlavfi.freezedetect.freeze_end: 12\n",
        ])
        self.assertEqual(2, len(self.calls), "one sound pass and one picture pass, not three decodes")
        sound, picture = self.calls
        self.assertIn("-vn", sound)
        self.assertEqual("0:a:0", sound[sound.index("-map") + 1])
        self.assertNotIn("-vf", sound)
        self.assertLess(sound.index("-vn"), sound.index("-i"), "-vn must stop the picture being read")
        self.assertIn("-an", picture)
        self.assertLess(picture.index("-an"), picture.index("-i"))
        self.assertEqual("0:v:0", picture[picture.index("-map") + 1])
        chain = picture[picture.index("-vf") + 1]
        # Reduced first, then both detectors on the same decoded frames.
        self.assertTrue(chain.startswith("fps=10,scale="), chain)
        self.assertLess(chain.index("scale="), chain.index("scdet="))
        self.assertIn("scdet=threshold=15", chain)
        self.assertIn("freezedetect=n=-60dB:d=1", chain)
        for call in self.calls:
            self.assertIn("-progress", call)
        self.assertEqual(["shots", "silence", "freeze"], signals["measured"])
        self.assertEqual([4.2], signals["shots"]["cuts_at_s"])
        self.assertEqual([{"start_s": 10.0, "end_s": 12.0, "duration_s": 2.0}], signals["freeze"]["spans"])
        self.assertEqual(["measure_silence", "measure_shots_and_freeze"], [step["operation"] for step in steps])
        self.assertNotIn("measured_until_s", signals)

    def test_a_file_without_sound_skips_the_sound_pass_and_says_why(self) -> None:
        signals, steps, notes = self.measure(["lavfi.scd.time: 4.2\n"], has_audio=False)
        self.assertEqual(1, len(self.calls))
        self.assertIn("-an", self.calls[0])
        self.assertEqual(["shots", "freeze"], signals["measured"])
        self.assertEqual(["silence"], signals["unavailable"])
        self.assertTrue(any("no audio track" in note for note in notes))
        self.assertFalse(any("could not be taken" in note for note in notes),
                         "a file with no sound is not a measurement that failed")

    def test_a_pass_stopped_at_the_deadline_keeps_what_it_read(self) -> None:
        stopped = subprocess.TimeoutExpired(
            ["ffmpeg"], 30,
            output=b"frame=10\nout_time_us=20000000\nprogress=continue\nout_time_us=24000000\n",
            stderr=(b"lavfi.scd.time: 4.2\nlavfi.freezedetect.freeze_start: 22\n"
                    b"lavfi.scd.time: 25.1\nlavfi.scd.ti"))
        signals, steps, notes = self.measure(["", stopped])
        self.assertEqual(["shots", "silence", "freeze"], signals["measured"])
        # The detectors had reached 25.1s although progress last said 24s; the cut-off line is dropped.
        self.assertEqual({"shots": 25.1, "freeze": 25.1}, signals["measured_until_s"])
        self.assertEqual([4.2, 25.1], signals["shots"]["cuts_at_s"])
        self.assertEqual(25.1, signals["shots"]["shots"][-1]["end_s"])
        self.assertEqual([{"start_s": 22.0, "end_s": 25.1, "duration_s": 3.1}], signals["freeze"]["spans"])
        self.assertEqual("completed_partial", steps[-1]["status"])
        self.assertIn("25.1s", steps[-1]["note"])
        note = next(text for text in notes if "Cuts and held frames" in text)
        self.assertIn("first 25.1s of 60.0s", note)
        self.assertIn("unmeasured, not as found to have none", note)

    def test_a_pass_with_no_time_left_is_not_started_and_reads_as_unread(self) -> None:
        signals, steps, notes = self.measure([], deadline_in=0.5)
        self.assertEqual([], self.calls)
        self.assertEqual(["shots", "silence", "freeze"], signals["unavailable"])
        self.assertEqual({"failed"}, {step["status"] for step in steps})
        note = next(text for text in notes if "time ran out" in text)
        self.assertIn("unread rather than as measured-and-found-nothing", note)

    def test_a_stopped_pass_that_read_nothing_is_unavailable(self) -> None:
        stopped = subprocess.TimeoutExpired(["ffmpeg"], 30, output=b"out_time_us=N/A\n", stderr=b"")
        signals, _, _ = self.measure(["", stopped])
        self.assertEqual(["silence"], signals["measured"])
        self.assertEqual(["shots", "freeze"], signals["unavailable"])

    def test_progress_lines_give_the_furthest_second(self) -> None:
        self.assertEqual(0.0, self.module.progress_seconds("out_time_us=N/A\n"))
        self.assertEqual(12.5, self.module.progress_seconds("out_time_us=3000000\nout_time_us=12500000\n"))


class DeadlineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load()

    def test_frames_out_of_time_are_skipped_opening_and_close_first(self) -> None:
        """Out of time is treated like out of bytes: the hook and the ending are already written."""
        plan = self.module.plan_frames(20.0, 3.0, 1.0, 2, 600)
        written: list[float] = []

        def run(name, args, timeout):
            target = Path(args[-1])
            target.write_bytes(b"jpeg")
            written.append(float(args[args.index("-ss") + 1]))
            return subprocess.CompletedProcess(args, 0, "", "")

        ticks = iter([0.0] * 8 + [99.0] * 100)  # eight frames fit, then the time is gone
        clock = types.SimpleNamespace(monotonic=lambda: next(ticks))
        with mock.patch.object(self.module, "run_tool", run), \
                mock.patch.object(self.module, "tool_version", lambda name: "ffmpeg test"), \
                mock.patch.object(self.module, "file_digest", lambda path: "0" * 64), \
                mock.patch.object(self.module, "time", clock):
            step, items, skipped = self.module.extract(Path("/clips/cut.mp4"), Path(tempfile.mkdtemp()),
                                                       plan, "cut", 3, deadline=10.0)
        self.assertEqual(8, len(items))
        self.assertIn(plan["closing_frame_s"], [item["t_seconds"] for item in items])
        self.assertEqual(plan["hook_frames"], sum(1 for item in items if item["t_seconds"] <= 3.0))
        self.assertTrue(skipped)
        self.assertEqual({self.module.TIME_BUDGET_REASON}, {entry["reason"] for entry in skipped})
        self.assertEqual("completed_partial", step["status"])

    def test_a_measurement_that_dies_leaves_the_frames_on_disk(self) -> None:
        """The artifact is written before measuring starts. Simulate the measurement being killed
        mid-way and check the frames were already delivered."""
        folder = Path(tempfile.mkdtemp())
        media = folder / "cut.mp4"
        media.write_bytes(b"not really a video")
        artifact = folder / "analysis" / "local-media.json"

        def run(name, args, timeout):
            target = Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"jpeg")
            return subprocess.CompletedProcess(args, 0, "", "")

        def killed(*_args, **_kwargs):
            raise KeyboardInterrupt("stopped from outside")

        probe = ({"step": "local_extraction", "operation": "probe_media", "tool": "ffprobe",
                  "tool_version": "ffprobe test", "invocation_sha256": "x", "started_at": "t",
                  "status": "completed"},
                 {"format": {"duration": "6"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]})
        argv = ["extract_local_frames.py", "--media", str(media), "--out-dir", str(folder / "analysis" / "frames"),
                "--artifact", str(artifact), "--measure-signals"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(self.module, "probe", lambda path: probe), \
                mock.patch.object(self.module, "run_tool", run), \
                mock.patch.object(self.module, "tool_version", lambda name: "ffmpeg test"), \
                mock.patch.object(self.module, "measure_signals", killed):
            with self.assertRaises(KeyboardInterrupt):
                self.module.main()
        document = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual("available", document["frames"]["status"])
        self.assertTrue(document["frames"]["items"])
        self.assertNotIn("signals", document["media"])
        for item in document["frames"]["items"]:
            self.assertTrue((artifact.parent / item["path"]).is_file())

    def test_a_deadline_must_be_positive(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--media", str(SCRIPT), "--out-dir", "o", "--artifact", "a.json",
             "--deadline-s", "0"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(2, result.returncode)
        self.assertIn("--deadline-s positive", result.stderr)

    def test_signals_only_does_not_need_a_frame_directory_but_frames_do(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--media", str(SCRIPT), "--artifact", "a.json"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(2, result.returncode)
        self.assertIn("--out-dir is required", result.stderr)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = Path(tempfile.mkdtemp())
        cls.media = cls.dir / "a draft -- v2.mp4"  # 空格和前导短横都是普通文件名
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.media)],
            check=True, capture_output=True, timeout=300)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_signals_only_adds_the_measurement_to_the_frames_already_written(self) -> None:
        media = self.dir / "two shots.mp4"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=red:size=160x120:duration=3:rate=10",
             "-f", "lavfi", "-i", "testsrc=size=160x120:duration=3:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2",
             "-f", "lavfi", "-i", "sine=frequency=500:duration=2",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a][4:a]concat=n=3:v=0:a=1[a]",
             "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             str(media)],
            check=True, capture_output=True, timeout=300)
        artifact = self.dir / "separate.json"
        subprocess.run(
            [sys.executable, str(SCRIPT), "--media", str(media),
             "--out-dir", str(self.dir / "separate"), "--artifact", str(artifact)],
            check=True, capture_output=True, timeout=600)
        frames_before = json.loads(artifact.read_text(encoding="utf-8"))["frames"]

        for _ in range(2):  # measuring twice replaces the measurement, it does not stack it
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--media", str(media), "--artifact", str(artifact),
                 "--signals-only"],
                capture_output=True, text=True, timeout=600)
            self.assertEqual(0, result.returncode, result.stderr)
        document = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(frames_before, document["frames"], "measuring must not touch the frames")
        signals = document["media"]["signals"]
        self.assertEqual(["shots", "silence", "freeze"], signals["measured"])
        self.assertEqual(2, signals["shots"]["count"])
        self.assertAlmostEqual(3.0, signals["shots"]["cuts_at_s"][0], delta=0.2)
        self.assertAlmostEqual(2.0, signals["silence"]["spans"][0]["start_s"], delta=0.2)
        operations = [step["operation"] for step in document["steps"]]
        self.assertEqual(1, operations.count("measure_shots_and_freeze"))
        self.assertEqual(1, operations.count("measure_silence"))
        summary = json.loads(result.stdout)
        self.assertEqual(frames_before["status"], summary["frames_status"])
        self.assertEqual(2, summary["signals"]["shots"])

    def test_signals_only_refuses_an_artifact_written_for_another_file(self) -> None:
        artifact = self.dir / "other.json"
        subprocess.run(
            [sys.executable, str(SCRIPT), "--media", str(self.media),
             "--out-dir", str(self.dir / "other"), "--artifact", str(artifact)],
            check=True, capture_output=True, timeout=600)
        before = artifact.read_bytes()
        other = self.dir / "another cut.mp4"
        shutil.copyfile(self.media, other)
        with other.open("ab") as handle:
            handle.write(b"\0")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--media", str(other), "--artifact", str(artifact),
             "--signals-only"],
            capture_output=True, text=True, timeout=600)
        self.assertEqual(2, result.returncode)
        self.assertIn("different file", result.stderr)
        self.assertEqual(before, artifact.read_bytes())

    def test_ffmpeg_progress_reports_how_far_a_pass_read(self) -> None:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-progress", "pipe:1", "-an",
             "-i", str(self.media), "-map", "0:v:0", "-f", "null", "-"],
            check=True, capture_output=True, text=True, timeout=120)
        self.assertAlmostEqual(4.0, load().progress_seconds(result.stdout), delta=0.25)



if __name__ == "__main__":
    unittest.main()
