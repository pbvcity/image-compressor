# 图片批量压缩工具

把待处理的文件夹放进 `input/`，运行脚本后到 `compressed/` 取图片，到 `reports/` 查看本次日志与报告。**保留输入目录内的相对目录结构、像素尺寸和精确透明度，原图不覆盖。**

## 目录安排

```text
./
├── input/                         # 待压缩区，可随时新增、删除或更换批次
│   └── 任意批次/任意子目录/图片.png
├── compressed/                    # 仅存放压缩图片，保留 input/ 内部层级
│   └── 任意批次/任意子目录/图片.png 或 图片.jpg
├── reports/                       # 仅保留最近一次任务的记录，固定文件名覆盖
│   ├── compression-summary.md     # 本次统计摘要
│   ├── compression-report.csv     # Excel 可打开的逐文件报告
│   ├── compression-report.json    # 完整结果、相对路径及文件校验值
│   ├── compression-run.log        # 本次运行日志
│   └── verification.log           # 运行复核脚本后生成
├── scripts/
│   ├── compress_images.py         # 压缩入口
│   ├── run_default.py             # 各系统共用的一键运行流程
│   └── verify_results.py          # 独立复核入口
├── 一键压缩-Windows.bat           # Windows 双击入口
├── 一键压缩-macOS.command         # macOS 双击入口
├── 一键压缩-Linux.sh              # Linux 启动入口
├── tests/                        # 自动化验证
├── requirements.txt
├── AGENTS.md                      # 必须遵守的简要项目规则
└── README.md
```

`input/` 内的素材都只是当次输入，不是工具的组成部分；没有任何预设批次名称或必须长期存在的素材文件夹。只扫描当前放入的 PNG，支持中文、空格和多层子目录。

例如 `input/批次A/详情/主图.png` 会生成 `compressed/批次A/详情/主图.png`（含透明像素），或 `compressed/批次A/详情/主图.jpg`（全部不透明）。目录层级与文件名主体不变，仅不透明图片的扩展名改为 `.jpg`；`input/` 这一层不会套进输出路径。

## 一键使用（推荐）

先安装 **Python 3.10 或以上**。Windows 安装时勾选 **Add python.exe to PATH**。将 PNG 图片或图片文件夹放进 `input/`，然后运行对应启动文件：

| 系统 | 启动文件 | 使用方式 |
| --- | --- | --- |
| Windows | `一键压缩-Windows.bat` | 双击 |
| macOS | `一键压缩-macOS.command` | 双击，在终端中运行 |
| Linux | `一键压缩-Linux.sh` | 文件管理器选择“在终端中运行”，或执行 `sh 一键压缩-Linux.sh` |

三个入口共用同一套 Python 流程。不同系统的文件关联和执行权限不同，因此分别提供启动文件。Linux 能否双击在终端中运行取决于桌面环境和文件管理器设置。

Git 仓库不包含 `input/`、`compressed/`、`reports/` 和 `.venv/` 中的本机文件。首次下载后可自行创建 `input/` 并放入图片，也可先运行一次启动文件，让它自动创建输入目录；压缩结果、报告及虚拟环境按需自动生成。

默认选项已配置好，无需输入参数：

- 自动建立并复用项目内的 `.venv/`，按 `requirements.txt` 安装依赖；首次安装依赖需要联网，此后依赖满足时不再安装。
- 扫描整个 `input/`，输出到 `compressed/`，保留目录层级、像素尺寸和透明度；不修改原图。
- 含透明像素保留 PNG；不透明图片转质量 90、4:4:4 JPG；检测到 `pngquant` 时自动使用，未安装也能运行。
- 默认带 `--overwrite`，覆盖同名结果，保留输出区其他旧图片。
- 压缩后自动运行 `scripts/verify_results.py`；部分图片失败时也会复核本批次并显示失败。报告和复核记录覆盖写入 `reports/`。
- 运行结束后保留终端窗口，按提示关闭。输入目录不存在时自动建立；没有 PNG 时提示放入图片，不替换上一批压缩报告。

从其他工作目录启动也可以；按启动文件所在位置寻找工具目录。也可跨平台在终端执行 `python scripts/run_default.py`（macOS / Linux 通常用 `python3`）。

如果 macOS 或 Linux 提示没有执行权限，在工具目录执行一次：

```bash
chmod +x 一键压缩-macOS.command 一键压缩-Linux.sh
```

Linux 若提示无法创建虚拟环境，先通过系统的软件包管理器安装对应 Python 的 `venv` 支持（例如 Debian / Ubuntu 的 `python3-venv`）。若更换系统或 Python 后 `.venv/` 不可用，可删除 `.venv/` 后重新双击，启动器会重建；不要在多台电脑之间共用或同步 `.venv/`。

## 手动安装与运行（可选）

需要 Python 3.10 或以上，在工具所在目录打开终端：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

可选安装 `pngquant`，会多尝试一种高质量压缩候选；未安装时仍能运行：

```bash
brew install pngquant
```

所有图片在本机处理，不上传，不需要 API 密钥。

## 手动使用与参数

将新的图片文件夹放进 `input/`，可以先删除或替换上一次的输入素材。然后运行：

```bash
.venv/bin/python scripts/compress_images.py --overwrite
```

`--overwrite` 允许覆盖 `compressed/` 中的同名图片；不加这个参数，遇到已有同名输出会在开始前停止。不会修改原图，也不会自动清除输出区其他旧图片。若要让输出区只包含新批次，可先自行清空或删除 `compressed/`；脚本会重新创建目录。

报告、摘要和日志**每次任务都覆盖上一批记录，不按日期或批次建立历史文件，不追加旧日志**。开始处理时会清除上次的复核记录、抽样对比图等已知附加记录；这样不会把上一批的验证结果当成本批结果。参数错误、没有输入图片或同名保护拦截等未开始的任务，不替换上一批记录。

记录仅写入独立的 `reports/`，不会写进 `input/` 或 `compressed/`。不要在 `reports/` 保存需要长期保留的手工文件。

若进行人工抽样复核，对比图也放在 `reports/quality-preview.png`；它是当前批次的附加记录，下一次任务开始时清除，不作为永久素材或历史档案。

只处理输入区里的某个文件夹时，同时指定对应输出子目录：

```bash
.venv/bin/python scripts/compress_images.py "input/批次A" -o "compressed/批次A" --overwrite
```

自定义输入目录后，保留的是**相对于所选输入目录**的层级。上例的报告也会替换 `reports/` 中上一任务的记录。

## 移动和改名

脚本根据自身位置寻找 `input/`、`compressed/`、`reports/`，没有写死工具所在位置或外层文件夹名称。整个工具目录可以一起移动或改名，无需改脚本和配置。JSON 中的输入、输出根路径相对于报告所在目录保存，整体移动后仍可复核。

终端示例默认在移动后的工具目录执行；也可以从别处调用脚本，默认工作目录仍按脚本位置确定。手动传入的相对路径按终端当前目录解析。虚拟环境受本机 Python 安装影响；移动后若 `.venv` 不可用，删除它并重新运行一键入口，或重新执行手动安装步骤即可。

## 压缩规则

### 含透明或半透明像素：保留 PNG

- 宽高不变，Alpha 通道逐像素完全一致，包括半透明边缘。
- 比较原文件、无损重新编码、RGB 轻微量化、可选 `pngquant --quality=90-100` 等候选，选择通过校验后字节数最小的结果。
- RGB 轻微量化尝试 7 位、6 位两档，每通道相对原值最多偏移 1 或 2（范围 0–255）；存储格式仍是正常 8 位 PNG。
- `pngquant` 改变 Alpha 时，先恢复原始 Alpha，再编码和校验；不满足质量要求、失败或超时则放弃该候选。
- 黑底、白底分别合成后，可见 RGB 的 PSNR 均须 ≥42 dB，任意通道偏差须 ≤12/255，且 Alpha 必须完全一致。
- 原图始终参与候选比较，因此透明 PNG 的输出不会比原文件大，必要时原样复制。

误差指标用于控制质量，不能证明所有图片在所有屏幕上完全不可分辨。建议按实际展示尺寸检查细小文字、品牌色和渐变。对色值要求严格时可仅对透明 PNG 使用无损模式：

```bash
.venv/bin/python scripts/compress_images.py --lossless-png --overwrite
```

此模式保留透明 PNG 的全部 RGBA 像素；不透明图片仍按下一条规则转 JPG。

### 所有像素完全不透明：转质量 90 JPG

- 按真实像素判断，而非仅检查文件是否带 Alpha 通道；RGBA 但所有 Alpha=255 也会转 JPG。
- 固定使用 `quality=90`、4:4:4 色度采样（`subsampling=0`）、优化及渐进式编码，不缩放。
- “90%质量”是 JPEG 编码器参数 90，不表示保留 90% 信息或体积。
- JPG 为有损编码，按指定参数转换，不使用透明 PNG 的 PSNR 门槛，也不保证所有素材转换后一定更小。
- 网页如引用了原来的 `.png`，使用 JPG 结果时需更新引用路径。

## 复核与报告

```bash
.venv/bin/python scripts/verify_results.py
```

复核脚本根据最新 JSON 报告逐张检查输入和输出的 SHA-256、实际字节数、目录映射、尺寸、格式、Alpha；JPG 额外核对质量 90 的量化表和 4:4:4 采样。结果覆盖 `reports/verification.log`。若输入已删除、更换或输出被修改，旧批次复核会报错；请先重新压缩当前素材。

CSV 使用带 BOM 的 UTF-8，可用 Excel 打开。每张记录包含相对路径、尺寸、处理方式、前后字节数、节省比例、Alpha 是否一致及透明 PNG 的可见误差。JSON 另含总体统计及校验值。

透明 PNG 的 PSNR 为 `null` 且最大误差为 0，表示可见像素完全相同（PSNR 为无穷大）；JPG 的两项误差均为 `null`，表示未执行该项门槛评估。摘要只统计本次成功处理的图片，不把文档、日志或其他旧图片算进压缩体积。

## 参数与边界

| 参数 | 作用 |
| --- | --- |
| 输入目录（可选位置参数） | 默认使用脚本旁的 `input/` |
| `-o` / `--output` | 默认 `compressed/` |
| `--reports` | 默认 `reports/`；必须独立于输入和输出目录，不能互相嵌套 |
| `--overwrite` | 允许覆盖同名输出图片；日志报告始终按本次任务覆盖 |
| `--lossless-png` | 透明 PNG 仅无损处理 |
| `--no-pngquant` | 不调用可选 pngquant |

只扫描 PNG（含大写扩展名），不复制其他文件；跳过隐藏文件、隐藏目录和文件符号链接。支持 RGB、RGBA、灰度、调色板及 tRNS 透明信息。APNG 动画、16 位 PNG 会报错并跳过，避免丢失帧或精度。

保留 ICC、EXIF；PNG 重编码时还保留 gAMA、cHRM、sRGB、cICP 色彩信息，其他文本元数据等可能移除。独立 PNG gamma 信息不能直接等价写进无 ICC 的 JPG，色彩管理要求严格的额外素材应单独检查。

单张失败会记录并继续；全部成功退出码为 0，存在图片失败为 1，参数错误或目标冲突为 2。写入图片前先校验目标路径冲突，每张通过临时文件原子替换。请勿同时运行多个压缩任务，它们共用同一份最新报告。

## 自动验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

覆盖目录与透明度、调色板透明信息、尺寸、JPEG 质量及采样、无损模式、颜色元数据、覆盖保护、异常素材、嵌套输出排除、日志替换及项目移动改名后的报告复核。

技术参考：[Pillow 格式文档](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)、[pngquant 官方说明](https://pngquant.org/)。
