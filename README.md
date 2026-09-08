# JxlForge Converter

基于 **PySide6** 的 [libjxl](https://github.com/libjxl/libjxl)（cjxl / djxl）图形前端，把 JPEG XL 的编码 / 解码做成可视化的桌面操作工具。

> 本项目全部代码均以自然语言形式在 AI 辅助下完成。

---

## 功能特性

- **支持输入格式（编码为 JXL）**：PNG / APNG / GIF / JPEG / WebP / AVIF / EXR / PPM / PGM / PFM / PAM / PGX / JXL
  - WebP / AVIF 经 Pillow 中转为 PNG 后交给 cjxl（保留 ICC 配置）
  - PFM / PAM / PGX 经内置轻量解码器中转为 PPM（零外部依赖）
  - EXR 为浮点 HDR，仅解析头部元数据、不渲染像素缩略图
- **编码（→ JXL）**
  - 有损（距离 distance）/ 无损（含 JPEG 无损重编码 `--lossless_jpeg=1`）
  - 压缩级别 `effort` 1–10、更快解码等级 `--faster_decoding` 0–4 等高级参数
  - 支持自定义命令
- **解码（JXL → PNG/PNM）** 与基本信息查看（jxlinfo）
- **动作操作链**：上移 / 下移 / 移除、单操作双行布局、批量转换
- **保持源文件时间戳**：修改时间 + 创建时间
- **五标签页设计**：输入 / 动作 / 输出 / 状态 / 设置；底部常驻 转换 / 停止 / 关闭
- **语言**：简体中文 / 繁體中文 / English（重启生效）
- **主题**：跟随系统 / 浅色 / 深色
- **控件样式**：原生及其变体 / Fusion
- **设置持久化**：窗口位置、输出目录、文件名模板、自定义历史、主题等设置存于 QSettings（ini）

---

## ⚠️ 依赖：需自行安装 libjxl 命令行工具

本程序**不自带 cjxl / djxl / jxlinfo 引擎**，由你自行选择 libjxl 版本：

1. 从 [libjxl 官方发布页](https://github.com/libjxl/libjxl) 安装 libjxl（建议 **v0.12.0**，本版在此环境下测试通过）。
2. 把 `cjxl.exe` / `djxl.exe` / `jxlinfo.exe` 所在目录加入系统 **PATH**。
3. 重启本程序。设置页会显示 cjxl/djxl「已找到」；缺失时转换前会弹窗提示、预览 JXL 时也会引导。

> 这样设计是为了让你自由选用任意 libjxl 版本，不受 GUI 打包版本束缚。

---

## 下载与运行（发布版）

1. 在 [Releases](../../releases) 下载 `JxlForge-Converter_vX.X.X_win64.zip`，解压到任意目录。
2. 装好 libjxl 并加入 PATH。
3. 运行 `JxlForge Converter\JxlForge Converter.exe`。

- 发布包未做代码签名，首次运行 Windows SmartScreen 可能提示「Windows 已保护你的电脑」，点击「仍要运行」即可；部分杀软对 PyInstaller 打包的程序可能误报，属正常现象。

---

## 目录结构

```
JxlForge-Converter/
├── main.py              # 开发态启动入口（import jxlforge.__main__:run）
├── run.bat             # 开发态一键启动（pythonw 无控制台）
├── run.pyw             # 无控制台启动入口（Python Launcher 加载）
├── setup.py            # 环境与 libjxl 检测 / 安装脚本
├── install_deps.sh     # 跨平台（macOS / Linux / WSL）依赖安装脚本
├── requirements.txt    # Python 依赖清单
├── packaging/          # 打包配置
│   ├── JxlForge Converter.spec   # PyInstaller 打包规格（Tier2 精简版）
│   ├── build_dist.bat           # 一键打包 + 自检门禁
│   └── _launch_app.py           # 生产打包入口（调用 run()）
├── jxlforge/           # 主程序包
│   ├── __main__.py     # 入口 run()、--selftest 自检
│   ├── main_window.py  # 主窗口与全部交互逻辑
│   ├── converter.py    # cjxl / djxl / jxlinfo 调用与参数构造
│   ├── processor.py    # 转换任务处理
│   ├── calibrate.py    # 功耗校准
│   ├── power.py        # 电源状态
│   ├── formats.py      # 格式支持判定
│   ├── combo_switch.py / no_flicker_combo.py  # 自绘无闪烁下拉控件
│   ├── i18n.py         # 国际化加载
│   └── i18n/           # 翻译字典（en_US.json / zh_TW.json）
├── tools/              # 测试、探针与脚本
│   ├── run_tests.py    # 回归测试入口
│   ├── test_*.py       # headless 回归测试
│   ├── probe_*.py      # 行为 / 性能探针
│   ├── extract_i18n.py / classify_i18n.py / check_i18n_coverage.py / i18n_wrap_source.py / apply_i18n_batch.py  # i18n 工具链
│   └── test_packaged_e2e.py  # 打包产物端到端自检驱动
├── docs/
│   ├── design-decisions.md   # 设计决策记录
│   └── i18n-workflow.md      # i18n 工作流程
├── LICENSE             # GPL v3 全文
└── README.md
```

---

## 协议

**GPL v3**（详见 [LICENSE](LICENSE)）。

### 依赖

- [libjxl](https://github.com/libjxl/libjxl)（cjxl/djxl，运行时由用户自备，BSD-3-Clause）
- [PySide6](https://doc.qt.io/qtforpython/)（Qt 6 绑定，LGPL v3）
- [Pillow](https://python-pillow.org/)（MIT）
- Python 3.12（PSF License）

---

## 已知限制

- 未自带 libjxl 引擎（设计如此，见上文）。
- 未做代码签名。
- 本人首个项目的首次发布版本，欢迎在 Issues 反馈问题。

---

## 致谢

- 界面布局与设计思路部分参考了 [XnConvert](https://www.xnconvert.com/)，特此致谢；本项目与 XnConvert 无任何隶属或背书关系。
- 本程序为 [libjxl](https://github.com/libjxl/libjxl) 的图形封装，核心编解码能力来自 libjxl 项目。

---

## 贡献

欢迎提 Issue / PR。开发态请保持 `tools/` 下 headless 回归测试绿灯（PySide6 offscreen 平台运行）。

### 面向开发者（从源码构建）

```bat
git clone <your-repo-url> JxlForge-Converter
cd JxlForge-Converter
pip install -r requirements.txt
python main.py          REM 或双击 run.bat 无控制台启动
```

打包发布版：

```bat
packaging\build_dist.bat
```

> 打包产物输出到仓库外的 `JxlForge-Build/`，不进 git。

它会：① 安全挪走旧 dist（避免触发安全守卫）② 用 spec 打包（已把 `jxlforge/i18n/*.json` 作为数据带进包）③ 跑 `exe --selftest` 自检，漏打资源会红字报警。
