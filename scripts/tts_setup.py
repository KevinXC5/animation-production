"""
豆包 TTS 配置助手：查看配置状态、筛选音色、写入 .env、生成试听样音。

  status   查看 .env 中的 APIKEY / VOICE 是否已配置（APIKEY 只显示掩码）
  voices   按场景、性别、语种、关键词筛选官方音色目录
  set      写入 VOICE，APIKEY 从标准输入读取（避免出现在命令行参数和进程列表中）
  preview  用候选音色各合成一句试听样音，同时验证 APIKEY 可用

示例：
  python3 tts_setup.py status --env .env
  python3 tts_setup.py voices --scene 教育 --gender female --limit 10
  printf '%s' "$KEY" | python3 tts_setup.py set --env .env --apikey-stdin --voice zh_female_vv_uranus_bigtts
  python3 tts_setup.py preview --env .env --voices zh_female_vv_uranus_bigtts,zh_male_m191_uranus_bigtts --out tts_preview
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parent / "references" / "doubao-voices.json"
sys.path.insert(0, str(HERE))


def load_catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["voices"]


def mask(key: str) -> str:
    """只露出首尾各 4 位。"""
    return "（未设置）" if not key else (key[:4] + "…" + key[-4:] if len(key) > 10 else "****")


def read_env_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def parse_env(lines: list[str]) -> dict:
    """兼容 KEY=VALUE 与 KEY: VALUE。"""
    env = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        sep = "=" if "=" in s else ":" if ":" in s else None
        if sep:
            k, v = s.split(sep, 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def git_ignore_status(env_path: Path) -> str:
    """返回 'ignored' / 'not-ignored' / 'no-repo'。"""
    d = env_path.resolve().parent
    if subprocess.run(["git", "-C", str(d), "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode:
        return "no-repo"
    r = subprocess.run(["git", "-C", str(d), "check-ignore", "-q", str(env_path.resolve())], capture_output=True)
    return "ignored" if r.returncode == 0 else "not-ignored"


def cmd_status(a):
    env = parse_env(read_env_lines(a.env))
    by_id = {v["id"]: v for v in load_catalog()}
    voice = env.get("VOICE", "")
    print(json.dumps({
        "env_file": str(a.env.resolve()), "env_exists": a.env.exists(),
        "apikey_set": bool(env.get("APIKEY")), "apikey": mask(env.get("APIKEY", "")),
        "voice": voice or None, "voice_name": by_id.get(voice, {}).get("name"),
        "voice_in_catalog": voice in by_id if voice else None,
        "git": git_ignore_status(a.env),
    }, ensure_ascii=False, indent=1))


def gender_of(v: dict) -> str:
    vid = v["id"].lower()
    return "female" if "_female_" in vid else "male" if "_male_" in vid else "unknown"


def cmd_voices(a):
    out = []
    for v in load_catalog():
        blob = " ".join([v["name"], v["scene"], v["lang"], v["tags"], v["id"]])
        if a.scene and a.scene not in v["scene"]:
            continue
        if a.gender and gender_of(v) != a.gender:
            continue
        if a.lang and a.lang not in (v["lang"] + v["scene"]):
            continue
        if a.group and v["group"] != a.group:
            continue
        if a.search and not all(k in blob for k in a.search.split()):
            continue
        out.append(v)
    if a.json:
        print(json.dumps(out[: a.limit], ensure_ascii=False, indent=1))
        return
    print(f"共 {len(out)} 个匹配，显示前 {min(len(out), a.limit)} 个：")
    for v in out[: a.limit]:
        extra = f"  [{v['tags']}]" if v["tags"] else ""
        print(f"  {v['name']:<14} {v['id']:<48} {v['scene']} | {v['lang']}{extra}")


def upsert_env(path: Path, updates: dict):
    """更新或追加键值，保留其他行和注释；原子写入并设置 600 权限。"""
    lines, done = read_env_lines(path), set()
    for i, line in enumerate(lines):
        s = line.strip()
        for k, v in updates.items():
            if s.startswith(k) and s[len(k):].lstrip()[:1] in ("=", ":"):
                lines[i] = f"{k}={v}"
                done.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def cmd_set(a):
    updates = {}
    if a.apikey_stdin:
        key = sys.stdin.read().strip()
        if not key or any(c.isspace() for c in key):
            sys.exit("APIKEY 为空或包含空白字符，未写入")
        updates["APIKEY"] = key
    if a.voice:
        ids = {v["id"] for v in load_catalog()}
        if a.voice not in ids and not a.allow_unlisted:
            sys.exit(f"音色 {a.voice} 不在官方 2.0 目录中；若是复刻或新上线音色，加 --allow-unlisted")
        updates["VOICE"] = a.voice
    if not updates:
        sys.exit("没有要写入的内容")
    upsert_env(a.env, updates)
    note = ""
    if git_ignore_status(a.env) == "not-ignored":
        gi = a.env.resolve().parent / ".gitignore"
        with gi.open("a", encoding="utf-8") as f:
            f.write(("\n" if gi.exists() and gi.read_text().strip() else "") + ".env\n")
        note = f"；已将 .env 加入 {gi}"
    shown = {k: (mask(v) if k == "APIKEY" else v) for k, v in updates.items()}
    print(f"已写入 {a.env.resolve()}：{shown}（权限 600）{note}")


async def _preview(a):
    from doubao_tts import BYTES_PER_SEC, FatalError, synthesize, validate, write_audio
    env = parse_env(read_env_lines(a.env))
    key = os.environ.get("APIKEY") or env.get("APIKEY", "")
    if not key:
        sys.exit("未配置 APIKEY")
    by_id = {v["id"]: v for v in load_catalog()}
    a.out.mkdir(parents=True, exist_ok=True)
    results = []
    for vid in [x.strip() for x in a.voices.split(",") if x.strip()]:
        name = by_id.get(vid, {}).get("name", vid)
        path = a.out / f"{vid}.mp3"
        try:
            pcm, words = await synthesize(a.text, a.tone, key, vid, a.rate)
            problems = validate(a.text, pcm, words)
            write_audio(pcm, path, "mp3")
            results.append({"voice": vid, "name": name, "file": str(path.resolve()),
                            "duration": round(len(pcm) / BYTES_PER_SEC, 2), "warnings": problems})
            print(f"  ✓ {name}（{vid}）→ {path}")
        except FatalError as e:
            sys.exit(f"APIKEY 或权限不可用，已停止：{e}")
        except Exception as e:
            results.append({"voice": vid, "name": name, "error": str(e)})
            print(f"  ✗ {name}（{vid}）：{e}")
    (a.out / "preview.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    if not any("file" in r for r in results):
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="豆包 TTS 配置助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("--env", type=Path, default=Path(".env")); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("voices")
    p.add_argument("--scene", help="通用 / 教育 / 视频配音 / 有声阅读 / 角色扮演 / 客服 …")
    p.add_argument("--gender", choices=["female", "male"])
    p.add_argument("--lang", help="语种关键词，如 英语、日语、粤语")
    p.add_argument("--group", choices=["中文", "外语"])
    p.add_argument("--search", help="名称或标签关键词，空格表示同时满足")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_voices)
    p = sub.add_parser("set")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--apikey-stdin", action="store_true", help="从标准输入读取 APIKEY")
    p.add_argument("--voice")
    p.add_argument("--allow-unlisted", action="store_true")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("preview")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--voices", required=True, help="逗号分隔的音色 ID")
    p.add_argument("--text", default="你好呀！接下来，我来给你讲一个有趣的故事。")
    p.add_argument("--tone", default="")
    p.add_argument("--rate", type=int, default=0)
    p.add_argument("--out", type=Path, default=Path("tts_preview"))
    p.set_defaults(fn=lambda a: asyncio.run(_preview(a)))
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
