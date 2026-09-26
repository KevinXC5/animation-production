"""覆盖有效工程、时间轴损坏、缺帧和成片参数异常，不依赖浏览器或外部服务。"""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_delivery.py"
SPEC = importlib.util.spec_from_file_location("check_delivery", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

BASE = {
    "duration": 3.0,
    "chapters": [{"id": "c0", "start": 0.0, "end": 3.0}],
    "lines": [{"id": "l0", "ch": "c0", "cn": "你好！", "en": "Hello!",
               "start": 0.6, "end": 1.6,
               "words": [{"w": "你", "s": 0.6, "e": 1.0}, {"w": "好！", "s": 1.0, "e": 1.6}]}],
    "sfx": [{"name": "chime", "t": 0.6, "line": "l0"}],
}


class DeliveryTests(unittest.TestCase):
    def check(self, change=None, bilingual=False):
        data = copy.deepcopy(BASE)
        if change:
            change(data)
        report = MODULE.Report()
        MODULE.validate_timeline(data, report, bilingual)
        return report

    def test_no_narration(self):
        self.assertFalse(self.check(lambda d: (d.update(lines=[], sfx=[]))).errors)

    def test_single_language_text(self):
        def mono(d):
            row = d["lines"][0]
            row.pop("cn"); row.pop("en"); row["text"] = "你好！"
        self.assertFalse(self.check(mono).errors)
        self.assertTrue(self.check(mono, bilingual=True).errors)

    def test_single_language_words_must_cover_text(self):
        # 单语 text 字段也要校验字级文本是否覆盖原文
        def mono_mismatch(d):
            row = d["lines"][0]
            row.pop("cn"); row.pop("en"); row["text"] = "你好呀！"
        self.assertTrue(self.check(mono_mismatch).errors)

    def test_silent_video(self):
        info = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                             "avg_frame_rate": "24/1", "duration": "3.0"}]}
        report = MODULE.Report()
        MODULE.validate_media(info, 3, 24, 1920, 1080, report)
        self.assertTrue(report.errors)
        report = MODULE.Report()
        MODULE.validate_media(info, 3, 24, 1920, 1080, report, silent=True)
        self.assertFalse(report.errors)

    def test_valid(self):
        self.assertEqual(self.check().errors, [])

    def test_duplicate_id(self):
        report = self.check(lambda d: d["lines"].append(copy.deepcopy(d["lines"][0])))
        self.assertTrue(any("重复" in x for x in report.errors))

    def test_chapter_gap(self):
        self.assertTrue(self.check(lambda d: d["chapters"][0].update(start=0.3)).errors)

    def test_timestamp_outside(self):
        self.assertTrue(self.check(lambda d: d["lines"][0]["words"][1].update(e=2.4)).errors)

    def test_missing_text(self):
        self.assertTrue(self.check(lambda d: d["lines"][0]["words"].pop()).errors)

    def test_bad_chapter_reference(self):
        self.assertTrue(self.check(lambda d: d["lines"][0].update(ch=[])).errors)

    def test_missing_words_is_warning(self):
        report = self.check(lambda d: d["lines"][0].update(words=[]))
        self.assertFalse(report.errors)
        self.assertTrue(report.warnings)

    def test_invalid_duration(self):
        for value in (True, float("nan"), float("inf"), -1, "3"):
            with self.subTest(value=value):
                self.assertTrue(self.check(lambda d: d.update(duration=value)).errors)

    def test_invalid_cue(self):
        self.assertTrue(self.check(lambda d: d["sfx"][0].update(t=4)).errors)

    def test_frames_use_ceil(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 检查器只负责序号，模拟非空文件即可。
            for i in range(3):
                (root / f"f{i:05d}.jpg").write_bytes("测试".encode("utf-8"))
            report = MODULE.Report()
            MODULE.validate_frames(root, 1.01, 2, report)
            self.assertEqual(report.facts["expected_frames"], 3)
            self.assertFalse(report.errors)
            (root / "f00001.jpg").unlink()
            missing = MODULE.Report()
            MODULE.validate_frames(root, 1.01, 2, missing)
            self.assertTrue(any("缺失" in x for x in missing.errors))

    def test_media_length(self):
        info = {"streams": [
            {"codec_type": "video", "width": 1920, "height": 1080,
             "avg_frame_rate": "24/1", "duration": "3.0", "codec_name": "h264"},
            {"codec_type": "audio", "duration": "3.0", "codec_name": "aac"},
        ]}
        report = MODULE.Report()
        MODULE.validate_media(info, 3, 24, 1920, 1080, report)
        self.assertFalse(report.errors)
        info["streams"][1]["duration"] = "1.0"
        report = MODULE.Report()
        MODULE.validate_media(info, 3, 24, 1920, 1080, report)
        self.assertTrue(report.errors)

    def test_png_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i in range(2):
                (root / f"f{i:05d}.png").write_bytes(b"png")
            report = MODULE.Report()
            MODULE.validate_frames(root, 1.0, 2, report, "png")
            self.assertFalse(report.errors)

    def test_media_size_optional_and_format_duration(self):
        # 竖屏、无轨道时长的 WebM：不传尺寸不报错，时长取容器层
        info = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                             "avg_frame_rate": "30/1", "codec_name": "vp9"}],
                "format": {"duration": "3.0"}}
        report = MODULE.Report()
        MODULE.validate_media(info, 3, 30, None, None, report, silent=True)
        self.assertFalse(report.errors)
        self.assertEqual(report.facts["height"], 1920)


if __name__ == "__main__":
    unittest.main()
