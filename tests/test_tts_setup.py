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
        self.assertEqual(M.gender_of({"id": "zh_male_m191_uranus_bigtts"}), "male")


if __name__ == "__main__":
    unittest.main()
