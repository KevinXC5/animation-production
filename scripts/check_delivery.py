"""只读检查动画时间线、连续帧号和成片基础信息；支持单语、双语与无声作品。"""

import argparse
import json
import math
import re
import subprocess
from pathlib import Path


class Report:
    """累积检查结果，不因单个字段错误放弃后续独立检查。"""

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.facts = {}

    def error(self, text):
        self.errors.append(text)

    def warn(self, text):
        self.warnings.append(text)

    def as_dict(self):
        return {"ok": not self.errors, "facts": self.facts,
                "errors": self.errors, "warnings": self.warnings}


def number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def text_key(text):
    """比较文字覆盖时忽略空白和标点，但保留汉字、字母和数字。"""
    return "".join(ch for ch in text if ch.isalnum())


def validate_timeline(data, report, require_bilingual=False):
    if not isinstance(data, dict):
        report.error("时间线根节点必须是对象")
        return None
    duration = data.get("duration")
    if not number(duration) or duration <= 0:
        report.error("duration 必须是正的有限数值")
        return None
    tolerance = 0.05
    report.facts["duration"] = duration

    def rows(key):
        value = data.get(key, [])
        if not isinstance(value, list):
            report.error(f"{key} 必须是数组")
            return []
        return value

    def interval(row, label, start_key="start", end_key="end"):
        start, end = row.get(start_key), row.get(end_key)
        if not number(start) or not number(end):
            report.error(f"{label} 的起止时间必须是有限数值")
            return None
        if start < 0 or end <= start or end > duration + tolerance:
            report.error(f"{label} 时间范围无效：{start}—{end}")
            return None
        return start, end

    def identity(row, seen, label):
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            report.error(f"{label} 缺少有效 ID")
            return None
        if rid in seen:
            report.error(f"{label} ID 重复：{rid}")
            return None
        seen.add(rid)
        return rid

    chapters, chapter_ids = {}, set()
    chapter_rows = rows("chapters")
    if not chapter_rows:
        report.error("没有章节")
    previous_end = 0.0
    for index, row in enumerate(chapter_rows):
        label = f"章节[{index}]"
        if not isinstance(row, dict):
            report.error(f"{label} 必须是对象")
            continue
        rid = identity(row, chapter_ids, label)
        bounds = interval(row, label)
        if not bounds:
            continue
        start, end = bounds
        if abs(start - previous_end) > tolerance:
            report.error(f"{label} 与前一章节不连续：上段结束 {previous_end}，本段开始 {start}")
        previous_end = end
        if rid:
            chapters[rid] = bounds
    if chapter_rows and abs(previous_end - duration) > tolerance:
        report.error("最后一章结束时间与总时长不一致")

    lines, line_ids = {}, set()
    line_rows = rows("lines")
    if not line_rows and require_bilingual:
        report.error("要求双语字幕，但时间线没有句子")
    previous_end = 0.0
    for index, row in enumerate(line_rows):
        label = f"句子[{index}]"
        if not isinstance(row, dict):
            report.error(f"{label} 必须是对象")
            continue
        rid = identity(row, line_ids, label)
        label = rid or label
        bounds = interval(row, label)
        if not bounds:
            continue
        start, end = bounds
        if start < previous_end - tolerance:
            report.error(f"{label} 与前一句重叠或顺序倒置")
        previous_end = end
        ch = row.get("ch")
        if not isinstance(ch, str) or ch not in chapters:
            report.error(f"{label} 引用了不存在或无效的章节")
        elif start < chapters[ch][0] - tolerance or end > chapters[ch][1] + tolerance:
            report.error(f"{label} 超出所属章节时间范围")
        cn, en = row.get("cn"), row.get("en")
        source_text = row.get("text", cn if cn is not None else en)
        if not isinstance(source_text, str) or not source_text.strip():
            report.error(f"{label} 原文为空或类型错误（需 text、cn 或 en 字段）")
        if require_bilingual:
            if not isinstance(cn, str) or not cn.strip():
                report.error(f"{label} 中文文本为空或类型错误")
            if not isinstance(en, str) or not en.strip():
                report.error(f"{label} 英文文本为空或类型错误")
        words = row.get("words", [])
        if not isinstance(words, list):
            report.error(f"{label} words 必须是数组")
            words = []
        if not words:
            report.warn(f"{label} 无字级时间戳，只能核对句级范围")
        word_end, pieces = start, []
        for wi, word in enumerate(words):
            wl = f"{label}/字[{wi}]"
            if not isinstance(word, dict):
                report.error(f"{wl} 必须是对象")
                continue
            text = word.get("w")
            if not isinstance(text, str) or not text:
                report.error(f"{wl} 文本无效")
            else:
                pieces.append(text)
            wb = interval(word, wl, "s", "e")
            if not wb:
                continue
            ws, we = wb
            if ws < start - tolerance or we > end + tolerance:
                report.error(f"{wl} 超出句子范围")
            if ws < word_end - tolerance:
                report.error(f"{wl} 时间重叠或倒置")
            word_end = we
        # 字级文本对照该句原文，单语 text 字段同样校验
        if words and isinstance(source_text, str) and text_key("".join(pieces)) != text_key(source_text):
            report.error(f"{label} 字幕文字未完整覆盖原文")
        if rid:
            lines[rid] = bounds

    cues = rows("sfx")
    for index, cue in enumerate(cues):
        label = f"音效[{index}]"
        if not isinstance(cue, dict):
            report.error(f"{label} 必须是对象")
            continue
        when = cue.get("t")
        if not number(when) or not 0 <= when < duration:
            report.error(f"{label} 触发时间无效")
        if not isinstance(cue.get("name"), str) or not cue["name"]:
            report.error(f"{label} 缺少名称")
        if "line" in cue:
            lid = cue["line"]
            if not isinstance(lid, str) or lid not in lines:
                report.error(f"{label} 引用了无效句子")
            elif number(when) and not lines[lid][0] - tolerance <= when <= lines[lid][1] + tolerance:
                report.error(f"{label} 不在关联句子范围内")
    report.facts.update(chapters=len(chapter_rows), lines=len(line_rows), sfx=len(cues))
    return duration


def validate_frames(directory, duration, fps, report, ext="jpg"):
    """只检查约定的 f00000.<ext> 序列，图像内容仍需解码验证。"""
    expected = math.ceil(duration * fps)
    if not directory.is_dir():
        report.error(f"帧目录不存在：{directory}")
        return
    actual, empty = set(), []
    for path in directory.iterdir():
        match = re.fullmatch(rf"f(\d{{5,}})\.{re.escape(ext)}", path.name)
        if not match:
            continue
        if not path.is_file() or path.stat().st_size == 0:
            empty.append(path.name)
            continue
        index = int(match[1])
        if path.name != f"f{index:05d}.{ext}":
            report.error(f"非标准帧名：{path.name}")
        actual.add(index)
    missing = sorted(set(range(expected)) - actual)
    extra = sorted(actual - set(range(expected)))
    if empty:
        report.error(f"空文件或非文件帧：{empty[:10]}")
    if missing:
        report.error(f"缺失 {len(missing)} 帧，前十项：{missing[:10]}")
    if extra:
        report.error(f"多余 {len(extra)} 帧，前十项：{extra[:10]}")
    report.facts.update(expected_frames=expected, numbered_frames=len(actual))
    report.warn("帧检查仅核对编号与非空；尚未核对解码、尺寸、视觉内容和缓存指纹")


def validate_media(info, duration, fps, width, height, report, silent=False):
    """width/height 为 None 时只记录实际尺寸不做比对。"""
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        report.error("成片缺少视频轨")
        return
    if not audio and not silent:
        report.error("成片缺少音轨；无声作品请加 --silent")
        return
    for key, expected in (("width", width), ("height", height)):
        if expected is not None and video.get(key) != expected:
            report.error(f"成片 {key} 不符：{video.get(key)}，期望 {expected}")
    try:
        numerator, denominator = video["avg_frame_rate"].split("/")
        actual_fps = float(numerator) / float(denominator)
        if abs(actual_fps - fps) > 0.01:
            report.error(f"成片帧率不符：{actual_fps}，期望 {fps}")
        tolerance = max(2 / fps, 0.08)
        # WebM/MKV 等容器常把时长只写在容器层，轨道缺失时回退到 format.duration
        fallback = info.get("format", {}).get("duration")
        vd = float(video.get("duration", fallback))
        ad = float(audio.get("duration", fallback)) if audio else vd
        if not math.isfinite(vd) or not math.isfinite(ad):
            raise ValueError("时长非有限值")
        if abs(vd - duration) > tolerance or abs(ad - duration) > tolerance:
            report.error(f"成片时长不符：视频 {vd}，音频 {ad if audio else '无'}，期望 {duration}")
        if audio and abs(vd - ad) > tolerance:
            report.error("音视频长度差超过两帧或编码容差")
        report.facts.update(video_duration=vd, audio_duration=ad if audio else None, fps=actual_fps)
    except (KeyError, ValueError, ZeroDivisionError):
        report.error("无法从 ffprobe 信息中验证帧率或轨道时长")
    report.facts.update(video_codec=video.get("codec_name"), audio_codec=audio.get("codec_name") if audio else None,
                        width=video.get("width"), height=video.get("height"), pix_fmt=video.get("pix_fmt"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeline", type=Path)
    parser.add_argument("--frames", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--fps", type=float, help="检查帧序列或成片时必填")
    parser.add_argument("--width", type=int, help="期望宽度，不填则只记录实际值")
    parser.add_argument("--height", type=int, help="期望高度，不填则只记录实际值")
    parser.add_argument("--frame-ext", default="jpg", help="帧序列扩展名，如 jpg、png")
    parser.add_argument("--silent", action="store_true", help="无声作品，不要求音轨")
    parser.add_argument("--require-bilingual", action="store_true", help="强制每句都有 cn 与 en")
    args = parser.parse_args()
    report = Report()
    if (args.frames or args.video) and args.fps is None:
        parser.error("检查帧序列或成片时必须提供 --fps")
    if args.fps is not None and (not number(args.fps) or args.fps <= 0):
        parser.error("fps 必须大于零且为有限值")
    if any(v is not None and v <= 0 for v in (args.width, args.height)):
        parser.error("画面尺寸必须大于零")
    try:
        data = json.loads(args.timeline.read_text(encoding="utf-8"))
        duration = validate_timeline(data, report, args.require_bilingual)
        if duration and args.frames:
            validate_frames(args.frames, duration, args.fps, report, args.frame_ext.lstrip("."))
        if duration and args.video:
            result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(args.video)],
                                    capture_output=True, text=True, check=True, timeout=60)
            validate_media(json.loads(result.stdout), duration, args.fps, args.width, args.height, report, args.silent)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report.error(f"检查未能完成：{exc}")
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
