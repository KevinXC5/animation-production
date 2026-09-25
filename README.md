# animation-production

通用动画制作技能（Claude Code Skill）。覆盖二维/三维短片、动态插画、科普解说、叙事动画、品牌与产品动画、音乐可视化和循环动画，从需求、技术选型、分镜、动画实现、可选 TTS 与字幕、配乐音效，到渲染与成片验收。

## 安装

将本目录放到 `~/.claude/skills/animation-production/`（或由 cc-switch 管理），在 Claude Code 中输入：

```text
/animation-production 你的动画需求
```

## 目录

| 路径 | 内容 |
|---|---|
| `SKILL.md` | 技能入口：六阶段流程与各阶段门槛 |
| `references/design.md` | 技术选型、视觉参考拆解、运动设计 |
| `references/engineering.md` | 工程结构、时间线数据协议、缓存失效规则 |
| `references/audio.md` | TTS、时间戳校验、配乐与混音 |
| `references/rendering.md` | 样片审阅、后台长任务、编码与成片验收 |
| `references/collaboration.md` | 子代理分工、中断恢复、跨项目复用 |
| `scripts/check_delivery.py` | 时间线、帧序列与成片参数检查 |
| `tests/` | 检查脚本的回归测试 |

## 成片检查

```bash
python3 scripts/check_delivery.py timeline.json --frames frames/ --video final.mp4
python3 scripts/check_delivery.py timeline.json --video silent.mp4 --silent
python3 -m unittest discover -s tests -v
```

检查脚本只核对结构（帧序列连续、时长与参数一致），画面质量与听感仍需人工审阅。
