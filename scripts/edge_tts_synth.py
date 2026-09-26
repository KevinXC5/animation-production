"""
edge-tts 免费合成（微软 Edge 朗读服务）：逐句合成音频，输出格式与 doubao_tts.py 一致。

特点：
- 无需 API Key 和浏览器，依赖 `pip install edge-tts`；合成在微软服务器完成，本机只收发数据
- 请求 WordBoundary 得到词级时间戳（按词切分，不含标点）
- 以输入指纹做缓存；逐句原子落盘；网络错误有限重试
- 非官方接口，可能随时变更或限流；不支持语气指令，lines.json 中的 tone 字段会被忽略

用法：
  python3 edge_tts_synth.py --list-voices [--locale zh-CN]
  python3 edge_tts_synth.py --text "你好。" --out out/tts --voice zh-CN-XiaoxiaoNeural
  python3 edge_tts_synth.py --lines lines.json --out out/tts [--env .env] [--rate +0%] [--format mp3]
  lines.json：[{"id": "l01", "text": "……"}, ...]
输出：<out>/<id>.<mp3|wav>、<out>/words.json、<out>/manifest.json
音色：--voice，或环境变量 / .env 中的 EDGE_VOICE
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doubao_tts import load_env, text_key  # noqa: E402  与豆包脚本共用 .env 解析和文字归一化

CLIENT_VERSION = "1"        # 修改请求逻辑时递增，使旧缓存失效
MP3_BITRATE = 48000         # 服务端固定返回 24kHz、48kbps 单声道 MP3
TICKS_PER_SEC = 10_000_000  # WordBoundary 偏移单位为 100 纳秒


def boundary_to_word(chunk: dict) -> dict:
    """把 WordBoundary 事件转换成 {w, s, e}，单位秒，从该句音频开头算起。"""
    s = chunk["offset"] / TICKS_PER_SEC
    return {"w": chunk["text"], "s": round(s, 3), "e": round(s + chunk["duration"] / TICKS_PER_SEC, 3)}


def mp3_duration(path: Path, size: int) -> float:
    """优先用 ffprobe 读取时长；没有 ffprobe 时按固定码率估算。"""
    if shutil.which("ffprobe"):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            pass
    return size * 8 / MP3_BITRATE


def validate(text: str, duration: float, words: list) -> tuple[list[str], list[str]]:
    """逐句校验，返回 (错误, 警告)。缺少时间戳只警告，因为部分语种不返回词边界。"""
    problems, warnings = [], []
    if duration < 0.2:
        problems.append("音频过短或为空")
    if not words:
        warnings.append("没有词级时间戳，字幕按句级时间处理")
    last = 0.0
    for w in words:
        if w["e"] < w["s"] or w["s"] < last - 0.05:
            problems.append(f"时间戳倒置或重叠：{w}")
            break
        last = w["e"]
    if words and words[-1]["e"] > duration + 0.1:
        problems.append(f"时间戳超出音频长度 {duration:.2f}s")
    if words and text_key("".join(w["w"] for w in words)) != text_key(text):
        problems.append("字幕文字未完整覆盖原文")
    return problems, warnings


def fingerprint(voice: str, rate: str, pitch: str, fmt: str, text: str) -> str:
    key = [CLIENT_VERSION, "edge-tts", voice, rate, pitch, fmt, text]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]


async def synthesize(text: str, voice: str, rate: str, pitch: str, proxy: str | None):
    """合成一句，返回 (mp3 字节, 词级时间戳列表)。"""
    import edge_tts

    com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary", proxy=proxy)
    audio, words = bytearray(), []
    async for chunk in com.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            words.append(boundary_to_word(chunk))
    return bytes(audio), words


def write_audio(mp3: bytes, path: Path, fmt: str) -> float:
    """先写临时文件再改名，保证中途失败不留半截文件；返回音频时长。"""
    tmp_mp3 = path.with_suffix(".tmp.mp3")
    tmp_mp3.write_bytes(mp3)
    duration = mp3_duration(tmp_mp3, len(mp3))
    if fmt == "mp3":
        tmp_mp3.replace(path)
        return duration
    tmp_wav = path.with_suffix(".tmp.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_mp3), str(tmp_wav)], check=True)
    tmp_mp3.unlink()
    tmp_wav.replace(path)
    return duration


async def list_voices(locale: str, proxy: str | None):
    import edge_tts

    for v in await edge_tts.list_voices(proxy=proxy):
        if locale and not v["Locale"].startswith(locale):
            continue
        tag = v.get("VoiceTag", {})
        cats = ", ".join(tag.get("ContentCategories", []))
        pers = ", ".join(tag.get("VoicePersonalities", []))
        print(f"  {v['ShortName']:<36} {v['Gender']:<7} {cats:<24} {pers}")


async def main():
    ap = argparse.ArgumentParser(description="edge-tts 逐句合成（免费备选方案）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--lines", type=Path, help="句子列表 JSON")
    src.add_argument("--text", help="单句文本（id 为 line）")
    src.add_argument("--list-voices", action="store_true", help="列出可用音色")
    ap.add_argument("--locale", default="", help="配合 --list-voices 按语种筛选，如 zh-CN、en-US")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--env", type=Path, default=Path(".env"))
    ap.add_argument("--voice", help="覆盖 .env 中的 EDGE_VOICE")
    ap.add_argument("--rate", default="+0%", help="语速，如 +10%%、-15%%")
    ap.add_argument("--pitch", default="+0Hz", help="音调，如 +5Hz、-5Hz")
    ap.add_argument("--format", choices=["mp3", "wav"], default="mp3")
    ap.add_argument("--only", default="", help="只合成这些 id（逗号分隔）")
    ap.add_argument("--force", action="store_true", help="忽略缓存")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or None, help="HTTP 代理，默认读取 HTTPS_PROXY")
    a = ap.parse_args()

    if a.list_voices:
        await list_voices(a.locale, a.proxy)
        return
    if not a.out:
        ap.error("合成时必须提供 --out")
    if not re.fullmatch(r"[+-]\d+%", a.rate) or not re.fullmatch(r"[+-]\d+Hz", a.pitch):
        ap.error("--rate 形如 +10%，--pitch 形如 +5Hz")

    env = load_env(a.env)
    voice = a.voice or os.environ.get("EDGE_VOICE") or env.get("EDGE_VOICE", "")
    if not voice:
        sys.exit("缺少音色：用 --voice 指定，或在 .env 中写入 EDGE_VOICE")
    lines = [{"id": "line", "text": a.text}] if a.text else json.loads(a.lines.read_text(encoding="utf-8"))
    ids = [l["id"] for l in lines]
    if len(set(ids)) != len(ids):
        sys.exit("句子 id 重复")
    if any(l.get("tone") for l in lines):
        print("提示：edge-tts 不支持语气指令，tone 字段已忽略")
    only = set(filter(None, a.only.split(",")))

    a.out.mkdir(parents=True, exist_ok=True)
    words_path, man_path = a.out / "words.json", a.out / "manifest.json"
    all_words = json.loads(words_path.read_text()) if words_path.exists() else {}
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    lock, sem, failed = asyncio.Lock(), asyncio.Semaphore(max(1, a.concurrency)), []

    def save():
        for p, obj in ((words_path, all_words), (man_path, manifest)):
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)

    async def job(ln):
        lid = ln["id"]
        if only and lid not in only:
            return
        fp = fingerprint(voice, a.rate, a.pitch, a.format, ln["text"])
        path = a.out / f"{lid}.{a.format}"
        if not a.force and manifest.get(lid, {}).get("fp") == fp and path.exists() and lid in all_words:
            print(f"  · {lid} 缓存命中")
            return
        async with sem:
            for attempt in range(3):
                try:
                    mp3, words = await synthesize(ln["text"], voice, a.rate, a.pitch, a.proxy)
                    duration = write_audio(mp3, path, a.format)
                    problems, warnings = validate(ln["text"], duration, words)
                    if problems:
                        path.unlink(missing_ok=True)
                        raise RuntimeError("；".join(problems))
                    break
                except Exception as e:
                    print(f"  ! {lid} 第 {attempt + 1} 次失败：{e}")
                    await asyncio.sleep(1.5 * (attempt + 1))
            else:
                failed.append((lid, "重试 3 次仍失败"))
                return
        for w in warnings:
            print(f"  ? {lid} {w}")
        async with lock:
            all_words[lid] = words
            manifest[lid] = {"fp": fp, "text": ln["text"], "duration": round(duration, 3), "file": path.name}
            save()
        print(f"  ✓ {lid}  {duration:.2f}s  {len(words)} 词  {ln['text']}")

    await asyncio.gather(*(job(l) for l in lines))
    if failed:
        print("失败：" + "；".join(f"{i}（{r}）" for i, r in failed))
        sys.exit(1)
    print(f"完成：{a.out}")


if __name__ == "__main__":
    asyncio.run(main())
