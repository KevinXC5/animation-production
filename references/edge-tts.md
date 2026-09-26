# edge-tts 使用文档

免费备选 TTS，调用微软 Edge“朗读”所用的在线语音服务。合成脚本为 `scripts/edge_tts_synth.py`，输出的音频、`words.json`、`manifest.json` 与 `doubao_tts.py` 格式一致，后续时间线、字幕和检查流程无需改动。

## 适用与限制

- **成本**：无需 API Key、账号和浏览器。合成在微软服务器完成，本机只收发文本和 MP3，CPU 与内存占用可以忽略，需要联网。
- **接口性质**：非官方接口，微软可能随时变更、限流或停止服务。商用或正式发布前向用户说明，由用户决定是否改用有服务协议的方案。
- **音质**：服务端固定返回 24kHz、48kbps 单声道 MP3，手机外放和短视频平台足够，耳机细听比豆包略闷。
- **表现力**：只能调语速、音调，不支持语气指令；`lines.json` 中的 `tone` 字段会被忽略，情绪起伏靠文案和标点控制。
- **时间戳**：请求 `WordBoundary` 得到词级时间，按词切分（例如“有没有”为一个词），不含标点。整句字幕和对齐校验够用；逐字高亮需要自行把词拆成字并插值，精度低于豆包。
- **语种**：音色决定语种，覆盖中文（含粤语、台湾口音与方言）、英语等上百个语种。少数语种可能不返回词边界，脚本只给出警告，字幕按句级时间处理。

## 配置交互

在豆包 TTS 使用文档「配置交互」第 2 步选择 edge-tts 后执行：

1. **检查依赖**：`python3 -c "import edge_tts"`，失败时提示用户安装 `pip install edge-tts`（建议装在项目虚拟环境里），装好后再继续。
2. **选音色**：运行 `python3 <skill-dir>/scripts/edge_tts_synth.py --list-voices --locale <语种>`，例如 `zh-CN`、`en-US`。在回复里以表格列出音色、性别和风格标签，再用 AskUserQuestion 问“选哪个音色？”：根据作品的受众、题材和旁白语气挑出最贴合的 4 个作为选项，最贴合的放第一位标“（推荐）”并写明原因，其余音色由用户在“Other”里填写。`.env` 已有 `EDGE_VOICE` 时，把“沿用当前音色”放第一位。
3. **写入**：`python3 <skill-dir>/scripts/tts_setup.py set --env <项目>/.env --edge-voice <音色名>`。
4. **确认**：运行 `tts_setup.py status`，向用户汇报 `.env` 路径与 `edge_voice`，然后进入剧本与合成。

## 脚本用法

```bash
# 列出音色
python3 <skill-dir>/scripts/edge_tts_synth.py --list-voices --locale zh-CN

# 单句试听
python3 <skill-dir>/scripts/edge_tts_synth.py --text "你好，欢迎收看。" --out audio/tts --env .env

# 批量：lines.json = [{"id": "c1_01", "text": "……"}, ...]
python3 <skill-dir>/scripts/edge_tts_synth.py --lines lines.json --out audio/tts --env .env --format wav

# 调整语速与音调 / 只重合成某几句 / 忽略缓存
python3 <skill-dir>/scripts/edge_tts_synth.py --lines lines.json --out audio/tts --rate +10% --pitch -5Hz
python3 <skill-dir>/scripts/edge_tts_synth.py --lines lines.json --out audio/tts --only c1_01,c1_02 --force
```

音色从 `--voice` 或 `.env` 的 `EDGE_VOICE` 读取。需要代理时设置 `HTTPS_PROXY` 或传 `--proxy`。输出 WAV 时需要 ffmpeg。

输出：
- `<out>/<id>.mp3|wav`：每句音频
- `<out>/words.json`：`{id: [{"w","s","e"}]}`，单位为秒，从该句音频开头算起
- `<out>/manifest.json`：每句的输入指纹、时长和文件名；文本、音色、语速、音调或格式改变时自动重新合成

每句都会校验：音频非空、时间戳单调且不超出音频、词级文字完整覆盖原文。不通过就重试，重试仍失败则以非零退出码结束。
