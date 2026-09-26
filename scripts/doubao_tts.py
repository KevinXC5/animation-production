"""
豆包 TTS 双向流式合成（seed-tts-2.0）：逐句合成音频，并输出校验过的字级时间戳。

特点：
- 自带二进制帧编解码，无需下载官方 protocols 包
- 默认关闭结尾节奏水印（aigc_watermark=False）
- 以 PCM 接收音频，句段偏移按实际音频字节精确计算，最后写出 WAV 或 MP3
- 以输入指纹做缓存；逐句原子落盘；鉴权/配额错误立即停止，网络错误有限重试

用法：
  python3 doubao_tts.py --text "你好，小朋友。" --out out/tts
  python3 doubao_tts.py --lines lines.json --out out/tts [--env .env] [--rate 0] [--format mp3]
  lines.json：[{"id": "l01", "text": "……", "tone": "开心地说"}, ...]
输出：<out>/<id>.<wav|mp3>、<out>/words.json、<out>/manifest.json
凭据：环境变量或 .env 中的 APIKEY 与 VOICE（兼容 KEY=VALUE 与 KEY: VALUE）
"""

import argparse
import asyncio
import hashlib
import json
import os
import struct
import subprocess
import sys
import uuid
import wave
from pathlib import Path

URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
RESOURCE_ID = "seed-tts-2.0"
SR = 24000                 # PCM 采样率，16bit 单声道
BYTES_PER_SEC = SR * 2
CLIENT_VERSION = "1"       # 修改请求逻辑时递增，使旧缓存失效

# ---------- 协议常量 ----------
EV = dict(StartConnection=1, FinishConnection=2, ConnectionStarted=50, ConnectionFailed=51,
          ConnectionFinished=52, StartSession=100, CancelSession=101, FinishSession=102,
          SessionStarted=150, SessionCanceled=151, SessionFinished=152, SessionFailed=153,
          TaskRequest=200, TTSSentenceStart=350, TTSSentenceEnd=351, TTSResponse=352,
          TTSEnded=359, TTSSubtitle=364)
EV_NAME = {v: k for k, v in EV.items()}
MSG_FULL_CLIENT, MSG_FULL_SERVER, MSG_AUDIO_SERVER, MSG_ERROR = 0b0001, 0b1001, 0b1011, 0b1111
FLAG_WITH_EVENT = 0b0100
NO_SESSION = {EV["StartConnection"], EV["FinishConnection"], EV["ConnectionStarted"],
              EV["ConnectionFailed"], EV["ConnectionFinished"]}
HAS_CONNECT_ID = {EV["ConnectionStarted"], EV["ConnectionFailed"], EV["ConnectionFinished"]}


class FatalError(RuntimeError):
    """鉴权失败、配额不足等不应重试的错误。"""


def pack(event: int, payload: dict | None = None, session_id: str = "") -> bytes:
    """组装客户端帧：4 字节头 + 事件号 + [会话 ID] + 载荷。"""
    body = json.dumps(payload or {}, ensure_ascii=False).encode()
    out = bytes([0x11, (MSG_FULL_CLIENT << 4) | FLAG_WITH_EVENT, 0x10, 0x00]) + struct.pack(">i", event)
    if event not in NO_SESSION:
        sid = session_id.encode()
        out += struct.pack(">I", len(sid)) + sid
    return out + struct.pack(">I", len(body)) + body


def unpack(data: bytes) -> dict:
    """解析服务端帧，返回 {type, event, session_id, payload, error_code}。"""
    mtype, flag = data[1] >> 4, data[1] & 0x0F
    pos = (data[0] & 0x0F) * 4
    msg = {"type": mtype, "event": None, "session_id": "", "error_code": None}

    def u32():
        nonlocal pos
        v = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        return v

    if mtype in (MSG_FULL_SERVER, MSG_AUDIO_SERVER) and flag in (0b0001, 0b0011):
        pos += 4                                   # 序号，本流程不需要
    elif mtype == MSG_ERROR:
        msg["error_code"] = u32()
    if flag == FLAG_WITH_EVENT:
        msg["event"] = struct.unpack(">i", data[pos:pos + 4])[0]
        pos += 4
        if msg["event"] not in NO_SESSION:         # 音频帧同样带会话 ID，必须读掉
            n = u32(); msg["session_id"] = data[pos:pos + n].decode(); pos += n
        if msg["event"] in HAS_CONNECT_ID:
            n = u32(); pos += n
    n = u32()
    msg["payload"] = data[pos:pos + n]
    return msg


def load_env(path: Path | None) -> dict:
    """读取 .env，兼容 KEY=VALUE 与 KEY: VALUE；环境变量优先。"""
    env = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sep = "=" if "=" in line else ":" if ":" in line else None
            if sep:
                k, v = line.split(sep, 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("APIKEY", "VOICE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def text_key(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


async def synthesize(text: str, tone: str, api_key: str, voice: str, rate: int, debug=False):
    """合成一句，返回 (pcm 字节, 绝对时间戳列表 [{w,s,e}], 调试事件)。"""
    import websockets

    headers = {"X-Api-Key": api_key, "X-Api-Resource-Id": RESOURCE_ID, "X-Api-Connect-Id": str(uuid.uuid4())}
    additions = {"aigc_watermark": False, "explicit_language": "zh-cn", "disable_markdown_filter": True}
    if tone:
        additions["context_texts"] = [tone]
    req = {"speaker": voice,
           "audio_params": {"format": "pcm", "sample_rate": SR, "speech_rate": rate, "enable_subtitle": True},
           "additions": json.dumps(additions, ensure_ascii=False)}
    pcm, words, log = bytearray(), [], []
    seg_start = 0                     # 当前句段起点（音频字节数）
    async with websockets.connect(URL, additional_headers=headers, max_size=32 * 1024 * 1024,
                                  open_timeout=15, close_timeout=5) as ws:
        async def recv():
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(raw, str):
                raise RuntimeError(f"收到意外的文本帧：{raw[:200]}")
            m = unpack(raw)
            if m["type"] == MSG_ERROR or m["event"] in (EV["ConnectionFailed"], EV["SessionFailed"]):
                detail = m["payload"].decode("utf-8", "ignore")[:300]
                fatal = any(k in detail.lower() for k in ("auth", "quota", "permission", "unauthorized", "invalid", "balance")) \
                    or (m["error_code"] or 0) in (45000000, 45000001, 45000002, 45000010)
                raise (FatalError if fatal else RuntimeError)(f"服务端错误 {m['error_code']}：{detail}")
            return m

        async def expect(event):
            m = await recv()
            if m["event"] != event:
                raise RuntimeError(f"期望 {EV_NAME[event]}，实际 {EV_NAME.get(m['event'], m['event'])}")

        await ws.send(pack(EV["StartConnection"]))
        await expect(EV["ConnectionStarted"])
        sid = str(uuid.uuid4())
        await ws.send(pack(EV["StartSession"], {"event": EV["StartSession"], "req_params": req}, sid))
        await expect(EV["SessionStarted"])
        await ws.send(pack(EV["TaskRequest"], {"event": EV["TaskRequest"], "req_params": {**req, "text": text}}, sid))
        await ws.send(pack(EV["FinishSession"], {}, sid))
        while True:
            m = await recv()
            ev = m["event"]
            if m["type"] == MSG_AUDIO_SERVER:
                pcm.extend(m["payload"])
                continue
            if ev == EV["TTSSentenceStart"]:
                seg_start = len(pcm)
            elif ev == EV["TTSSubtitle"] and m["payload"]:
                seg = json.loads(m["payload"]).get("words", [])
                off = seg_start / BYTES_PER_SEC
                seg_len = (len(pcm) - seg_start) / BYTES_PER_SEC
                # 判定时间基准：若首字早于本段音频起点，说明是句段内的相对时间，需要平移
                local = bool(seg) and off > 0.05 and seg[0]["startTime"] < off - 0.05
                log.append({"seg_offset": round(off, 3), "seg_audio": round(seg_len, 3), "local": local,
                            "first": seg[0]["startTime"] if seg else None, "last": seg[-1]["endTime"] if seg else None})
                shift = off if local else 0.0
                for w in seg:
                    words.append({"w": w["word"], "s": round(w["startTime"] + shift, 3), "e": round(w["endTime"] + shift, 3)})
            elif ev == EV["SessionFinished"]:
                break
        await ws.send(pack(EV["FinishConnection"]))
        try:
            await expect(EV["ConnectionFinished"])
        except Exception:
            pass
    if debug:
        print(json.dumps(log, ensure_ascii=False))
    return bytes(pcm), words


def validate(text: str, pcm: bytes, words: list) -> list[str]:
    """逐句校验：音频非空、时间戳单调且在音频范围内、字幕文字完整。"""
    problems, dur = [], len(pcm) / BYTES_PER_SEC
    if dur < 0.2:
        problems.append("音频过短或为空")
    if not words:
        problems.append("没有字级时间戳")
    last = 0.0
    for w in words:
        if w["e"] < w["s"] or w["s"] < last - 0.05:
            problems.append(f"时间戳倒置或重叠：{w}")
            break
        last = w["e"]
    if words and words[-1]["e"] > dur + 0.1:
        problems.append(f"时间戳超出音频长度 {dur:.2f}s")
    if words and text_key("".join(w["w"] for w in words)) != text_key(text):
        problems.append("字幕文字未完整覆盖原文")
    return problems


def write_audio(pcm: bytes, path: Path, fmt: str):
    """先写临时文件再改名，保证中途失败不留半截文件。"""
    tmp_wav = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm)
    if fmt == "wav":
        tmp_wav.replace(path)
        return
    tmp = path.with_suffix(".tmp.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_wav), "-c:a", "libmp3lame", "-b:a", "160k", str(tmp)], check=True)
    tmp_wav.unlink()
    tmp.replace(path)


async def main():
    ap = argparse.ArgumentParser(description="豆包 TTS 逐句合成")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--lines", type=Path, help="句子列表 JSON")
    src.add_argument("--text", help="单句文本（id 为 line）")
    ap.add_argument("--tone", default="", help="--text 模式的语气指令")
    ap.add_argument("--tone-default", default="", help="所有句子前置的通用语气")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--env", type=Path, default=Path(".env"))
    ap.add_argument("--voice", help="覆盖 .env 中的 VOICE")
    ap.add_argument("--rate", type=int, default=0, help="语速 [-50,100]，0 为正常")
    ap.add_argument("--format", choices=["mp3", "wav"], default="mp3")
    ap.add_argument("--only", default="", help="只合成这些 id（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="忽略缓存")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--debug", action="store_true", help="打印每个句段的时间基准判定")
    a = ap.parse_args()

    env = load_env(a.env)
    api_key, voice = env.get("APIKEY", ""), a.voice or env.get("VOICE", "")
    if not api_key or not voice:
        sys.exit("缺少 APIKEY 或 VOICE（环境变量或 --env 指定的 .env）")
    lines = [{"id": "line", "text": a.text, "tone": a.tone}] if a.text else json.loads(a.lines.read_text(encoding="utf-8"))
    ids = [l["id"] for l in lines]
    if len(set(ids)) != len(ids):
        sys.exit("句子 id 重复")
    only = set(filter(None, a.only.split(",")))

    a.out.mkdir(parents=True, exist_ok=True)
    words_path, man_path = a.out / "words.json", a.out / "manifest.json"
    all_words = json.loads(words_path.read_text()) if words_path.exists() else {}
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    lock, sem, failed = asyncio.Lock(), asyncio.Semaphore(max(1, a.concurrency)), []
    fatal = asyncio.Event()

    def save():
        for p, obj in ((words_path, all_words), (man_path, manifest)):
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)

    async def job(ln):
        lid = ln["id"]
        if only and lid not in only:
            return
        tone = (a.tone_default + " " + ln.get("tone", "")).strip()
        fp = hashlib.sha256(json.dumps([CLIENT_VERSION, RESOURCE_ID, voice, a.rate, a.format, ln["text"], tone],
                                       ensure_ascii=False).encode()).hexdigest()[:16]
        path = a.out / f"{lid}.{a.format}"
        if not a.force and manifest.get(lid, {}).get("fp") == fp and path.exists() and lid in all_words:
            print(f"  · {lid} 缓存命中")
            return
        async with sem:
            for attempt in range(3):
                if fatal.is_set():
                    return
                try:
                    pcm, words = await synthesize(ln["text"], tone, api_key, voice, a.rate, a.debug)
                    problems = validate(ln["text"], pcm, words)
                    if problems:
                        raise RuntimeError("；".join(problems))
                    break
                except FatalError as e:
                    fatal.set(); failed.append((lid, str(e))); print(f"  ✗ {lid} 不可重试：{e}")
                    return
                except Exception as e:
                    print(f"  ! {lid} 第 {attempt + 1} 次失败：{e}")
                    await asyncio.sleep(1.5 * (attempt + 1))
            else:
                failed.append((lid, "重试 3 次仍失败"))
                return
        write_audio(pcm, path, a.format)
        async with lock:
            all_words[lid] = words
            manifest[lid] = {"fp": fp, "text": ln["text"], "duration": round(len(pcm) / BYTES_PER_SEC, 3), "file": path.name}
            save()
        print(f"  ✓ {lid}  {len(pcm) / BYTES_PER_SEC:.2f}s  {len(words)} 字  {ln['text']}")

    await asyncio.gather(*(job(l) for l in lines))
    if failed:
        print("失败：" + "；".join(f"{i}（{r}）" for i, r in failed))
        sys.exit(1)
    print(f"完成：{a.out}")


if __name__ == "__main__":
    asyncio.run(main())
