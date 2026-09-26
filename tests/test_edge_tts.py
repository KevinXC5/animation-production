"""edge-tts 合成脚本的离线测试：词边界换算、校验与指纹，不访问网络。"""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/edge_tts_synth.py"
SPEC = importlib.util.spec_from_file_location("edge_tts_synth", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class EdgeTests(unittest.TestCase):
    def test_boundary_to_word(self):
        # 偏移单位为 100 纳秒：1_000_000 即 0.1 秒
        w = M.boundary_to_word({"text": "你好", "offset": 1_000_000, "duration": 4_620_000})
        self.assertEqual(w, {"w": "你好", "s": 0.1, "e": 0.562})

    def test_validate(self):
        ok = [{"w": "你好", "s": .1, "e": .5}, {"w": "欢迎", "s": .8, "e": 1.1}]
        self.assertEqual(M.validate("你好，欢迎。", 1.5, ok), ([], []))
        self.assertTrue(M.validate("你好，欢迎收看。", 1.5, ok)[0])    # 缺词
        self.assertTrue(M.validate("你好，欢迎。", 0.8, ok)[0])        # 超出音频
        self.assertTrue(M.validate("你好，欢迎。", 0.0, ok)[0])        # 空音频
        problems, warnings = M.validate("สวัสดี", 1.0, [])              # 无词边界只警告
        self.assertEqual(problems, [])
        self.assertTrue(warnings)

    def test_fingerprint_changes_with_inputs(self):
        base = M.fingerprint("zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", "mp3", "你好")
        self.assertEqual(base, M.fingerprint("zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", "mp3", "你好"))
        self.assertNotEqual(base, M.fingerprint("zh-CN-YunxiNeural", "+0%", "+0Hz", "mp3", "你好"))
        self.assertNotEqual(base, M.fingerprint("zh-CN-XiaoxiaoNeural", "+10%", "+0Hz", "mp3", "你好"))


if __name__ == "__main__":
    unittest.main()
