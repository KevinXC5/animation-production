# 引擎落地要点

按 design.md 选定路线后只读对应小节，混合路线读取所有相关小节。各节只列容易出错的关键点，API 细节以项目锁定版本的官方文档为准。

## 通用：逐帧确定性出图

- 离线出片不走实时播放：由渲染脚本按帧号 `i` 计算 `t = i / fps`，把画面设置到该时刻，等渲染完成后截图或导出。实时预览可以另写，不能混用同一套时钟。
- 浏览器类引擎（p5、Canvas、SVG、Three.js 等）常用 puppeteer-core 驱动：页面暴露 `renderAt(t)` 之类的接口，渲染完成后用 `canvas.toDataURL()` 或元素截图取帧。每帧确认已真正绘制完成，不用固定延时等待。
- 透明输出：导出带 alpha 的 PNG 序列，再编码为 ProRes 4444（`prores_ks`，`-pix_fmt yuva444p10le`）或 WebM VP9（`libvpx-vp9`，`-pix_fmt yuva420p`）；H.264 MP4 不支持透明。
- 混合路线：各层用相同的 fps 与时间线分别出帧（需要叠加的层带 alpha），最后在页面内或用 FFmpeg `overlay` 合成；先用几秒样片确认各层时间对齐。

## p5.js / p5.brush / Canvas 2D

- 用 instance mode 或模块隔离命名，避免全局模式下变量、函数重名。
- `noLoop()` 关闭自动循环，由渲染脚本设置时刻后调用 `redraw()` 出帧；`randomSeed()`、`noiseSeed()` 固定随机。
- `pixelDensity()` 固定为 1 或明确的值，避免在不同屏幕上输出尺寸不一致。
- p5.js 默认 2D 渲染器与 WEBGL 模式是两套渲染路径，像素数组操作不自动等于 GPU 加速；p5.brush 按当前版本文档确认所需的画布模式。
- p5.brush 若延迟合成图层，要用当前版本支持的合成机制验证文字、图片和笔刷的前后顺序，不把一次巧合的视觉结果当成可靠接口。复杂笔刷和渗色开销大，先实测单帧耗时再估算工期。

## Three.js

- 所有运动由 `t` 计算：不用 `THREE.Clock` 或 `requestAnimationFrame` 的实时增量；骨骼与关键帧动画用 `AnimationMixer.setTime(t)` 定位。
- 截图前确保当前帧已渲染：创建渲染器时设 `preserveDrawingBuffer: true`，或在同一次调用里先 `render` 再读取画布。
- 色彩：输出色彩空间保持 sRGB（`renderer.outputColorSpace`），颜色贴图设置 `texture.colorSpace = THREE.SRGBColorSpace`，数据贴图（法线、粗糙度等）保持线性；色调映射按画面风格选择，品牌色要求准确时核对最终像素。
- 抗锯齿：`antialias: true` 在使用后期处理（`EffectComposer`）时会失效，需要改用多重采样渲染目标或 SMAA/FXAA 通道；也可以高倍分辨率渲染后缩小。
- 透明背景：`alpha: true` 并 `setClearColor(color, 0)`，导出 PNG 序列后按通用小节编码。
- 资源：模型（glTF 等）、纹理、字体全部加载完成后，先 `renderer.compile()` 预热着色器，再出第一帧。
- 粒子和随机效果使用带种子的随机函数；物理引擎（cannon-es、Rapier 等）固定步长，并按帧烘焙结果，乱序渲染时直接读取缓存。
- 无头浏览器的 WebGL 依赖 GPU 后端，按平台实测可用性与速度；软件渲染（SwiftShader）能出图但很慢，估时要按实测。

## SVG / GSAP / Lottie

- GSAP 时间线创建后暂停（`paused: true`），每帧用 `timeline.seek(t)` 或 `progress()` 定位，不依赖 `gsap.ticker` 的实时播放；使用前核对当前许可。
- Lottie（lottie-web）用 `goToAndStop(帧号, true)` 按帧定位，注意 Lottie 文件自身的帧率与项目 fps 的换算。
- SVG 文本的字体要内嵌或确认已加载，否则截图时会回退到系统字体。

## React + Remotion

- 画面由 `useCurrentFrame()` 和 `useVideoConfig()` 驱动，插值用 `interpolate()`、弹性用 `spring()`；随机数用 Remotion 的 `random(seed)`，不用 `Math.random()`。
- 异步资源（字体、数据、模型）加载完成前用 Remotion 的延迟渲染机制挂起，避免空白帧。
- Three.js 场景可用 `@remotion/three`，此时动画同样由当前帧计算，不用 `useFrame` 的实时增量。
- 用官方 CLI 或渲染 API 导出；透明输出选择支持 alpha 的图像格式和编码参数。
- Remotion 对一定规模以上的公司需要付费许可，商用前核对当前许可条款。

## Blender

- 用 Python（`bpy`）脚本生成或修改场景，便于复现；在场景设置里固定 fps、起止帧和分辨率。
- 后台渲染：`blender -b 工程.blend -P 脚本.py` 或 `-a` 渲染动画，输出 PNG/EXR 序列后用 FFmpeg 编码，不直接依赖 Blender 内置的视频编码。
- 引擎取舍：EEVEE 快，适合风格化和样片；Cycles 慢但光照真实，先测单帧耗时再决定。
- 色彩管理：默认视图变换（新版本为 AgX）会改变高饱和颜色；品牌色、界面等需要准确颜色时改用 Standard 并核对。
- 物理、布料、粒子先烘焙缓存再渲染；透明背景开启 Film 的 Transparent 并输出 RGBA PNG。

## Manim

- 使用 Manim Community 版本，按其文档安装；`MathTex` 等公式对象需要 LaTeX 环境，中文文本用 `Text` 并显式指定已安装的中文字体。
- 用命令行参数或配置固定分辨率与 fps；需要透明背景时使用透明输出选项并导出 PNG 序列或 MOV。
- 与旁白同步：按 `audio/timeline.json` 中每句的时长安排 `self.play(..., run_time=...)` 和 `self.wait(...)`，不靠估计。
- 场景较长时拆成多个 `Scene` 分别渲染，按时间线顺序拼接，便于局部重渲。

## 文生视频 / 图生视频

- 只在用户明确授权服务、预算和上传范围后使用；生成结果不可逐帧控制，需要预留重试和挑选的时间与费用。
- 角色一致性依赖参考图和固定提示词，逐镜头检查；生成片段统一帧率、分辨率和色彩后再剪辑合成。
- 记录每个片段的生成参数、服务版本和授权信息，便于复现和说明来源。
