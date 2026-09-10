# JxlForge Converter

![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg) ![Platform](https://img.shields.io/badge/platform-Windows-blue) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

一个基于 **PySide6** 的 [libjxl](https://github.com/libjxl/libjxl) GUI，把 JPEG XL 的编码 / 解码做成可视化的桌面操作工具。

**简体中文** | [繁體中文](README_TW.md) | [English](README_EN.md)

> 本项目全部代码均以自然语言形式在 AI 辅助下完成。

> **平台**：当前仅提供 **Windows** 的打包与测试。代码层面基于跨平台的 PySide6 与 cjxl/djxl，理论可在 macOS / Linux 运行，但未经打包与验证；相关平台请自行安装依赖后执行 `python main.py`。

---

## 功能特性

- **支持输入格式（编码为 JXL）**：PNG / APNG / GIF / JPEG / JPE / JFIF / EXR / PPM / PGM / PFM / PAM / PGX / PBM / JXL / WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF
  - WebP / AVIF / BMP / TIFF / ICO / HEIC / HEIF 经 Pillow 中转解码为临时 PNG 后交给 cjxl，保留 ICC 配置（HEIC/HEIF 需先装 pi-heif 插件，见依赖）
  - 其余格式由 cjxl 原生直转
  - EXR 为浮点 HDR，仅解析头部元数据、不渲染像素缩略图
- **编码（→ JXL）**
  - 有损（距离 distance）/ 无损（含 JPEG 无损重编码 `--lossless_jpeg=1`）
  - 压缩级别 `effort` 1–10、更快解码等级 `--faster_decoding` 0–4 等高级参数
  - 支持自定义命令
- **解码（JXL → PNG/PNM）** 与基本信息查看（jxlinfo）
- **动作操作链**：上移 / 下移 / 移除、单操作双行布局、批量转换
- **双队列并发调度**：按像素数把任务分为「大图 / 小图」——超大图独占全部核心逐个处理，其余小图并行利用剩余核心，整批转换不空转、互不拖慢；判定阈值首次启动自动校准，无需手动配置。
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

1. 前往 [Releases 最新版](../../releases/latest)，下载 `JxlForge-Converter_v<版本号>_win64.zip` 并解压到任意目录。
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
├── packaging/          # 打包与发布配置
│   ├── JxlForge Converter.spec   # PyInstaller 打包规格（Tier2 精简版，随包带 LICENSE 与 i18n 字典）
│   ├── build_dist.bat           # 一键打包 + 自检门禁（英文输出，构建过程实时流式打印）
│   ├── build_dist_zh.bat/.py    # 同上，中文输出版
│   ├── make_release.bat/.py     # dist → 发布压缩包 ZIP + 7Z（英文输出）
│   ├── make_release_zh.bat/.py  # 同上，中文输出版
│   └── _launch_app.py           # 生产打包入口（调用 run()）
├── jxlforge/           # 主程序包
│   ├── __main__.py     # 入口 run()、--selftest 自检
│   ├── main_window.py  # 主窗口与全部交互逻辑
│   ├── converter.py    # cjxl / djxl / jxlinfo 调用与参数构造
│   ├── processor.py    # 转换任务处理
│   ├── calibrate.py    # 功耗校准
│   ├── power.py        # 电源状态
│   ├── formats.py      # 格式解码实现（PFM/PAM/PGX/PBM → PPM 等中转解码）
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

---

## 依赖

- [libjxl](https://github.com/libjxl/libjxl)（cjxl/djxl/jxlinfo，运行时由用户自备，BSD-3-Clause）
- [PySide6](https://doc.qt.io/qtforpython/)（Qt 6 绑定，LGPL v3）
- [Pillow](https://python-pillow.org/)（MIT）
- [pi-heif](https://github.com/bigcat88/pillow_heif/tree/master/pi-heif)（**可选，但强烈推荐**：用于支持 HEIC/HEIF 输入；不装仅该格式不可用。pillow-heif 的解码专用精简版，BSD-3-Clause）
- Python 3.12（PSF License）

---

## 已知限制

- 未自带 libjxl 引擎（设计如此，见上文）。
- 未做代码签名。
- 暂不支持 JPEG XR（.jxr / HD Photo）：Pillow 当前未编译对应解码器，需引入额外依赖且收益低，暂不纳入。
- 多页 TIFF 仅转换第 0 页（其余页被忽略）。
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
python main.py          REM 或双击 run.bat/run.pyw（推荐） 无控制台启动
```

打包发布版（英文 / 中文输出二选一）：

```bat
packaging\build_dist.bat        REM 英文输出
packaging\build_dist_zh.bat     REM 中文输出
```

> 打包产物输出到仓库外、与仓库**同级**的 `JxlForge-Build/`（具体位置由 `packaging/build_dist.bat` 按仓库实际所在盘符/路径自动推算，例如仓库在 `D:\x\JxlForge-Converter` 则落到 `D:\x\JxlForge-Build`），不进 git。Python 解释器同样自动解析（`E:\Python\Python312\python.exe` → `py` → PATH 上的 `python`），无需固定盘符。

它会：① 安全挪走旧 dist（避免触发安全守卫）② 用 spec 打包（已把 `jxlforge/i18n/*.json` 与 LICENSE 作为数据带进包）③ 跑 `exe --selftest` 自检，漏打资源会红字报警。

打包成分发压缩包（须先完成上一步）：

```bat
packaging\make_release.bat      REM 英文输出
packaging\make_release_zh.bat   REM 中文输出
```

输出到 `JxlForge-Build\release\`，文件名自动带版本号 `JxlForge-Converter_v<版本>_win64.zip` / `.7z`（版本号从 `jxlforge/__init__.py` 读取，不用手改）。打包前有门禁：dist 或 exe 缺失即中止，`selftest_report.txt` 非 PASS 会警告。

- **ZIP**：优先调用 7-Zip（`-tzip -mx=9`），未装 7-Zip 时回退 Python 标准库 `zipfile`（零依赖，始终可用）。
- **7Z**：需要 7-Zip（`winget install 7zip.7zip`）或 `pip install py7zr`；两者皆无则跳过 7Z、只出 ZIP。
- **7Z 明显更小**：实测 90 MB 的 dist → ZIP 约 36 MB、7Z 约 24 MB。原因是 ZIP 用 deflate、逐文件独立压缩，无法跨文件复用重复内容（格式限制，非脚本问题）。建议主推 7Z，ZIP 作兼容兜底。

发布包内置 `selftest` 自检：命令行 `JxlForge Converter.exe --selftest` 可验证 i18n 资源与端到端转码管线（仅当 libjxl 在 PATH 时执行转码用例，缺失则标记 SKIP）。
