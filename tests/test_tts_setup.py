"""配置助手的离线测试：.env 更新、掩码与音色目录。"""

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/tts_setup.py"
SPEC = importlib.util.spec_from_file_location("tts_setup", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class SetupTests(unittest.TestCase):
    def test_upsert_keeps_other_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# 注释\nAPIKEY: old\nOTHER=1\n")
            M.upsert_env(p, {"APIKEY": "new", "VOICE": "v1"})
            self.assertEqual(p.read_text(), "# 注释\nAPIKEY=new\nOTHER=1\nVOICE=v1\n")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            M.upsert_env(p, {"VOICE": "v2"})
            self.assertEqual(M.parse_env(M.read_env_lines(p))["VOICE"], "v2")

    def test_prefix_key_not_clobbered(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("VOICE_BACKUP=x\n")
            M.upsert_env(p, {"VOICE": "y"})
            self.assertEqual(M.parse_env(M.read_env_lines(p)), {"VOICE_BACKUP": "x", "VOICE": "y"})

    def test_mask(self):
        self.assertEqual(M.mask(""), "（未设置）")
        self.assertEqual(M.mask("abcdefghijklmnop"), "abcd…mnop")
        self.assertNotIn("efgh", M.mask("abcdefghijklmnop"))

    def test_catalog(self):
        voices = M.load_catalog()
        ids = [v["id"] for v in voices]
        self.assertGreater(len(voices), 300)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("zh_female_vv_uranus_bigtts", ids)
        # 推荐音色必须都在官方目录中
        for vid, _, _ in M.RECOMMENDED:
            self.assertIn(vid, ids)

    def test_edge_voice_written(self):
        # EDGE_VOICE 与豆包 VOICE 共存，互不覆盖
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("VOICE=v1\n")
            M.upsert_env(p, {"EDGE_VOICE": "zh-CN-XiaoxiaoNeural"})
            env = M.parse_env(M.read_env_lines(p))
            self.assertEqual(env["VOICE"], "v1")
            self.assertEqual(env["EDGE_VOICE"], "zh-CN-XiaoxiaoNeural")

    def test_filter_catalog(self):
        # 按语种筛选只返回该语种音色，关键词可进一步缩小范围
        en = M.filter_catalog(lang="美式英语")
        self.assertTrue(en)
        self.assertTrue(all("美式英语" in v["lang"] for v in en))
        self.assertLessEqual(len(M.filter_catalog(lang="美式英语", keyword=en[0]["name"])), len(en))


if __name__ == "__main__":
    unittest.main()
