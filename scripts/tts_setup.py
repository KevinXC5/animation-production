"""
豆包 TTS 配置助手：查看配置状态、列出推荐音色、写入 .env。

  status   查看 .env 中的 APIKEY / VOICE 是否已配置（APIKEY 只显示掩码）
  voices   列出豆包控制台推荐音色
  set      写入 VOICE，APIKEY 从标准输入读取（避免出现在命令行参数和进程列表中）

示例：
  python3 tts_setup.py status --env .env
  python3 tts_setup.py voices
  printf '%s' "$KEY" | python3 tts_setup.py set --env .env --apikey-stdin --voice zh_female_vv_uranus_bigtts
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parent / "references" / "doubao-voices.json"

# 豆包语音控制台“推荐音色”列表，供用户直接选择
RECOMMENDED = [
    ("zh_female_vv_uranus_bigtts", "Vivi 2.0", "语调平稳、咬字柔和、自带治愈安抚力的女声"),
    ("zh_female_xiaohe_uranus_bigtts", "小何 2.0", "声线甜美有活力的妹妹，活泼开朗，笑容明媚"),
    ("zh_male_m191_uranus_bigtts", "云舟 2.0", "声音磁性的男生，成熟理性，做事有条理，让人信赖"),
    ("zh_male_taocheng_uranus_bigtts", "小天 2.0", "眉目清朗男大，清澈温润有朝气，开朗真诚"),
    ("zh_male_shaonianzixin_uranus_bigtts", "少年梓辛 2.0", "少年感十足的清爽男生，温柔亲切，阳光开朗"),
    ("zh_female_meilinvyou_uranus_bigtts", "魅力女友 2.0", "性感妩媚的御姐，成熟有魅力，风情十足"),
    ("zh_male_liufei_uranus_bigtts", "刘飞 2.0", "逻辑清晰、理性稳重的男性"),
    ("zh_female_yingyujiaoxue_uranus_bigtts", "Tina老师 2.0", "磁性知性的青年讲师，温柔耐心，专业靠谱"),
]


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


def cmd_voices(a):
    if a.json:
        print(json.dumps([{"id": i, "name": n, "desc": d} for i, n, d in RECOMMENDED], ensure_ascii=False, indent=1))
        return
    for i, n, d in RECOMMENDED:
        print(f"  {n:<10} {i:<42} {d}")


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


def main():
    ap = argparse.ArgumentParser(description="豆包 TTS 配置助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("--env", type=Path, default=Path(".env")); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("voices"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_voices)
    p = sub.add_parser("set")
    p.add_argument("--env", type=Path, default=Path(".env"))
    p.add_argument("--apikey-stdin", action="store_true", help="从标准输入读取 APIKEY")
    p.add_argument("--voice")
    p.add_argument("--allow-unlisted", action="store_true")
    p.set_defaults(fn=cmd_set)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
