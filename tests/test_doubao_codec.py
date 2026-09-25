"""豆包 TTS 帧编解码与逐句校验的离线测试，不访问网络。"""

import importlib.util
import json
import struct
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/doubao_tts.py"
SPEC = importlib.util.spec_from_file_location("doubao_tts", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def server_frame(mtype, event, payload=b"", session="s1"):
    """按协议构造一条服务端帧。"""
    out = bytes([0x11, (mtype << 4) | M.FLAG_WITH_EVENT, 0x10, 0]) + struct.pack(">i", event)
    if event not in M.NO_SESSION:
        out += struct.pack(">I", len(session)) + session.encode()
    if event in M.HAS_CONNECT_ID:
        out += struct.pack(">I", 3) + b"cid"
    return out + struct.pack(">I", len(payload)) + payload


class CodecTests(unittest.TestCase):
    def test_pack_start_connection_has_no_session(self):
        f = M.pack(M.EV["StartConnection"])
        self.assertEqual(f[:4], bytes([0x11, 0x14, 0x10, 0]))
        self.assertEqual(struct.unpack(">i", f[4:8])[0], 1)
        self.assertEqual(struct.unpack(">I", f[8:12])[0], 2)   # 载荷为 {}

    def test_pack_session_event(self):
        f = M.pack(M.EV["StartSession"], {"a": 1}, "abc")
        self.assertEqual(struct.unpack(">I", f[8:12])[0], 3)
        self.assertEqual(f[12:15], b"abc")
        n = struct.unpack(">I", f[15:19])[0]
        self.assertEqual(json.loads(f[19:19 + n]), {"a": 1})

    def test_unpack_audio_frame_reads_session_id(self):
        m = M.unpack(server_frame(M.MSG_AUDIO_SERVER, M.EV["TTSResponse"], b"ID3xyz", "session-1234"))
        self.assertEqual(m["session_id"], "session-1234")
        self.assertEqual(m["payload"], b"ID3xyz")

    def test_unpack_connection_started(self):
        m = M.unpack(server_frame(M.MSG_FULL_SERVER, M.EV["ConnectionStarted"], b"{}"))
        self.assertEqual(m["event"], 50)
        self.assertEqual(m["payload"], b"{}")

    def test_unpack_error_frame(self):
        body = b'{"error":"auth"}'
        raw = bytes([0x11, (M.MSG_ERROR << 4), 0x10, 0]) + struct.pack(">I", 45000001) + struct.pack(">I", len(body)) + body
        m = M.unpack(raw)
        self.assertEqual(m["error_code"], 45000001)
        self.assertEqual(m["payload"], body)

    def test_validate(self):
        pcm = b"\0" * M.BYTES_PER_SEC
        ok = [{"w": "你", "s": .1, "e": .3}, {"w": "好！", "s": .3, "e": .6}]
        self.assertEqual(M.validate("你好！", pcm, ok), [])
        self.assertTrue(M.validate("你好呀", pcm, ok))                     # 缺字
        self.assertTrue(M.validate("你好！", pcm, [{"w": "你好", "s": .1, "e": 1.5}]))  # 超出音频
        self.assertTrue(M.validate("你好！", b"", ok))                      # 空音频

    def test_load_env_formats(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("APIKEY: abc\nVOICE=zh_voice\n# 注释\n")
            saved = {k: os.environ.pop(k, None) for k in ("APIKEY", "VOICE")}
            try:
                self.assertEqual(M.load_env(p), {"APIKEY": "abc", "VOICE": "zh_voice"})
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
