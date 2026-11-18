"""Behavioural tests for peakcut-caption-rescue. Stdlib unittest, offline."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin" / "peakcut-caption-rescue.py"
SAMPLE = ROOT / "examples" / "in" / "progressive.ass"
SPEC = importlib.util.spec_from_file_location("rescue", TOOL)
rescue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rescue)

HEADER = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def ass_with(events):
    return "[Script Info]\nScriptType: v4.00+\n" + HEADER + "\n".join(events) + "\n"


def cap(layer, start, end, x, text):
    return (f"Dialogue: {layer},0:00:{start:05.2f},0:00:{end:05.2f},Cap,,0,0,0,,"
            f"{{\\an5\\pos({x},880)}}{text}")


class UnitTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertAlmostEqual(rescue.parse_time("1:02:03.50"), 3723.5)

    def test_parse_time_rejects_garbage(self):
        with self.assertRaises(rescue.InputError):
            rescue.parse_time("00:99")

    def test_strip_tags_removes_override_blocks(self):
        self.assertEqual(rescue.strip_tags(r"{\an5\pos(3,4)}HELLO"), "HELLO")

    def test_three_layers_merge_into_one_word(self):
        text = ass_with([cap(1, 0.0, 0.6, 300, "MAKE"), cap(2, 0.6, 1.2, 300, "MAKE"),
                         cap(1, 0.0, 1.2, 300, "MAKE")])
        words = rescue.merge_words(rescue.parse_ass(text))
        self.assertEqual(len(words), 1)
        self.assertEqual((words[0]["start"], words[0]["end"]), (0.0, 1.2))

    def test_words_ordered_by_x_not_event_order(self):
        text = ass_with([cap(1, 0.0, 1.2, 500, "IT"), cap(1, 0.0, 1.2, 300, "MAKE")])
        cues = rescue.build_cues(rescue.parse_ass(text))
        self.assertEqual(cues[0]["text"], "MAKE IT")

    def test_hook_drawing_is_ignored(self):
        text = ass_with([r"Dialogue: 3,0:00:00.00,0:00:02.00,Hook,,0,0,0,,"
                         r"{\an7\pos(0,0)\p1}m 10 10 l 90 10 90 90{\p0}"])
        self.assertEqual(rescue.parse_ass(text), [])

    def test_fast_read_and_short_cue_are_warnings(self):
        cues = [{"index": 1, "start": 0.0, "end": 0.3, "text": "THIS LINE IS DEFINITELY FAR TOO LONG TO READ IN TIME"}]
        codes = {d["code"] for d in rescue.find_defects(cues)}
        self.assertEqual(codes, {"W_SHORT_CUE", "W_FAST_READ", "W_LONG_LINE"})


class CliTests(unittest.TestCase):
    def test_progressive_sample_gives_two_whole_lines(self):
        proc = run(SAMPLE, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual([c["text"] for c in data["cues"]], ["MAKE IT", "COUNT NOW"])
        self.assertEqual(data["input"]["caption_events"], 8)
        self.assertEqual(data["summary"]["defects"], 0)

    def test_srt_and_vtt_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt, vtt = Path(tmp) / "out.srt", Path(tmp) / "out.vtt"
            proc = run(SAMPLE, "--srt", srt, "--vtt", vtt, "--json")
            self.assertEqual(proc.returncode, 0)
            srt_text = srt.read_text(encoding="utf-8")
            self.assertIn("1\n00:00:00,000 --> 00:00:01,200\nMAKE IT", srt_text)
            self.assertTrue(vtt.read_text(encoding="utf-8").startswith("WEBVTT"))

    def test_missing_file_is_exit_2_with_json_error(self):
        proc = run("/tmp/definitely-not-here.ass", "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_no_caption_events_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.ass"
            path.write_text(ass_with([]), encoding="utf-8")
            proc = run(path, "--json")
            self.assertEqual(proc.returncode, 2)
            self.assertIn("no caption", json.loads(proc.stdout)["error"])

    def test_overlapping_lines_report_error_exit_1(self):
        text = ass_with([cap(1, 0.0, 2.0, 300, "ONE"), cap(1, 0.0, 2.0, 500, "TWO"),
                         cap(1, 1.0, 3.0, 300, "THREE"), cap(1, 1.0, 3.0, 500, "FOUR")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlap.ass"
            path.write_text(text, encoding="utf-8")
            proc = run(path, "--json")
            self.assertEqual(proc.returncode, 1)
            codes = {d["code"] for d in json.loads(proc.stdout)["defects"]}
            self.assertIn("E_CUE_OVERLAP", codes)

    def test_human_output_is_not_json_but_still_runs(self):
        proc = run(SAMPLE)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("2 lines", proc.stdout)
        self.assertNotIn("{", proc.stdout)


if __name__ == "__main__":
    unittest.main()