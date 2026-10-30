# 素材包 / 代码资源包图片压缩工具

用于处理整套素材包或代码资源包：递归寻找可压缩的位图，在保留目录结构的前提下优化图片，并把其他文件原样带到输出目录。输入文件始终不改写。

**默认保留图片格式、扩展名大小写和文件名**，例如 `assets/logo.PNG` 处理后仍是 `assets/logo.PNG`。HTML、CSS、JS、配置、矢量图、文档及未知格式文件都按原路径原字节复制，不修改代码中的图片引用。

只有主动选择“压缩并转 JPG”入口或添加 `--convert-opaque-to-jpeg` 参数，才会将可处理的不透明静态位图改为 `.jpg`。此模式适合允许调整图片引用的素材处理场景；用于代码资源包时，需要自行同步修改相关引用。

## 一键使用

1. 安装 Python 3.10 或以上；Windows 安装时勾选 **Add python.exe to PATH**。
2. 把待处理的文件或文件夹放进 `input/`。
3. 运行对应系统、对应模式的启动文件。
4. 到 `compressed/` 取完整结果，到 `reports/` 查看本次统计和复核记录。

| 系统 | 默认：保留图片格式及文件名 | 可选：不透明静态位图转 JPG |
| --- | --- | --- |
| Windows | `一键压缩-Windows.bat` | `一键压缩并转JPG-Windows.bat` |
| macOS | `一键压缩-macOS.command` | `一键压缩并转JPG-macOS.command` |
| Linux | `一键压缩-Linux.sh` | `一键压缩并转JPG-Linux.sh` |

Windows 和 macOS 双击运行；Linux 文件管理器选择“在终端中运行”，也可在终端执行 `sh 一键压缩-Linux.sh`。Linux 的双击行为取决于桌面环境和文件管理器设置。六个启动入口共用同一套处理和复核流程。

首次运行自动建立项目内的 `.venv/` 并按 `requirements.txt` 安装依赖，安装依赖需要联网；之后依赖满足时直接运行。图片和文件都在本机处理，不上传，不需要 API 密钥。启动器会覆盖同名输出，处理后自动运行独立复核，并保留终端窗口显示结果。

输入目录不存在时会自动创建；完全为空时提示放入文件，不替换上一批报告。只包含其他文件或空目录的输入也能完整输出。

如果 macOS / Linux 提示没有执行权限，在工具目录执行一次：

```bash
chmod +x *.command *.sh
```

Linux 若无法建立虚拟环境，先安装系统提供的 Python venv 支持，例如 Debian / Ubuntu 的 `python3-venv`。更换系统或 Python 后若 `.venv/` 不可用，删除它并重新运行启动文件即可重建；不要在多台电脑间共用或同步 `.venv/`。

## 目录约定

```text
./
├── input/                         # 当前素材包 / 代码资源包，可随时更换
│   └── 任意资源包/assets/logo.PNG
├── compressed/                    # 完整处理结果，保持 input/ 内部层级
│   └── 任意资源包/assets/logo.PNG
├── reports/                       # 仅保存最近一次任务的记录
│   ├── compression-summary.md
│   ├── compression-report.csv
│   ├── compression-report.json
│   ├── compression-run.log
│   └── verification.log
├── scripts/                       # 压缩、复核及共用启动逻辑
├── tests/                         # 自动化验证
├── 一键压缩-…                     # 三个平台的默认入口
├── 一键压缩并转JPG-…              # 三个平台的可选转换入口
├── requirements.txt
├── AGENTS.md
└── README.md
```

`input/` 中的内容只属于当前批次，不依赖任何固定包名或永久素材。Git 仓库忽略 `input/`、`compressed/`、`reports/`、`.venv/` 中的本机内容；它们按需创建。

输出保留相对于输入根目录的层级，不会额外套入 `input/` 这一层。隐藏文件、隐藏目录和空目录都会保留；文件和目录符号链接保留原链接目标，不跟随目标遍历；失效链接也会复制。创建链接需要操作系统允许，无法创建时会明确记为输出失败。

默认入口允许覆盖同名结果，但不会清除输出区其他旧文件。若需要让输出区只包含当前资源包，可在运行前自行清空或删除 `compressed/`。

## 支持的图片格式

以下扩展名不区分大小写。是否重编码取决于能否安全处理并获得更小的结果；未列出的格式也会原样输出。

| 类型 | 扩展名 |
| --- | --- |
| PNG、JPEG | `.png`、`.apng`、`.jpg`、`.jpeg`、`.jpe`、`.jfif` |
| WebP、BMP、GIF、TIFF | `.webp`、`.bmp`、`.dib`、`.gif`、`.tif`、`.tiff` |
| 图标、AVIF、HEIC / HEIF | `.ico`、`.avif`、`.heic`、`.heif`、`.hif` |
| TGA、PCX、Netpbm、QOI | `.tga`、`.pcx`、`.ppm`、`.pgm`、`.pbm`、`.pnm`、`.qoi` |
| JPEG 2000 | `.jp2`、`.j2k`、`.jpc`、`.jpf`、`.jpx`、`.j2c` |

HEIC / HEIF 使用 `pillow-heif`，一键入口会自动安装项目要求的版本；其他格式使用 Pillow 位图解码器。如果本机编解码器不支持某个文件，仍会原样复制并在报告中说明原因。

SVG / SVGZ、EPS、AI、PDF、WMF / EMF 等只复制，不压缩、不栅格化；即使被改成位图扩展名，也不会启用矢量解码器。代码、文本、压缩包及其他未知格式文件同样按原字节复制。

## 默认压缩规则：保留格式和文件名

所有图片保留像素尺寸，Alpha 通道逐像素不变。同格式优化只接受体积更小且通过校验的结果，否则保留原文件。

- **PNG**：比较原文件、无损重新编码、轻微 RGB 量化及可选 pngquant 候选。不透明 PNG 仍输出 PNG。RGB 量化尝试每通道 7 位、6 位精度，最大偏移 1 或 2/255；黑底、白底合成后的 PSNR 均须 ≥42 dB、最大通道偏差 ≤12/255，Alpha 必须完全一致。`--lossless-png` 可禁用 PNG 的 RGB 量化和 pngquant，只做无损优化。
- **JPEG**：尝试质量 90、4:4:4 采样、优化及渐进式编码，更小时采用，否则保留原字节。JPEG 编码有损；质量 90 是编码参数，不表示保留 90% 信息。原有 `.jpg`、`.jpeg`、`.JPG` 等文件名保持不变。
- **WebP、静态 GIF、TIFF、TGA、JPEG 2000 等**：尝试同格式的无损优化或压缩编码，逐像素验证后才采用更小的结果。BMP、DIB、PCX、Netpbm、QOI、单尺寸 ICO 等也按原格式尝试重编码；没有收益则保留原文件。
- **AVIF / HEIC / HEIF 静态图片**：默认原样保留，避免对已有高压缩率的有损图片再次有损编码；显式开启转 JPG 模式后，可转换其中不透明的静态图片。

保留解码器提供的 ICC、EXIF；带 ICC 的 CMYK JPEG 重编码时转为 sRGB 并更新色彩配置。PNG 重编码还保留 gAMA、cHRM、sRGB、cICP 色彩信息；部分非显示元数据可能移除。HEIF 方向由解码器处理，其他格式不额外旋转像素，保留 EXIF 方向。

误差指标不能保证所有图片在所有屏幕上完全不可分辨，建议按实际展示尺寸检查文字、品牌色和渐变。

### 动画、多页和无法安全压缩的文件

动画 GIF / APNG / WebP、多页 TIFF 保留原格式并尝试无损重编码。检查全部帧/页面的数量、尺寸、解码后的 RGBA 像素、播放时长、循环设置及可读取的色彩信息；只有完整校验通过且体积更小才采用候选。

多帧 AVIF / HEIF、带独立默认封面的 APNG、高位深页面、多尺寸 ICO，以及无法完整校验、重编码失败或没有变小的多帧文件，均原样复制。多帧重编码最多累计解码 2500 万像素，超过预算则原样复制。

16 位 PNG、高位深 TIFF / HEIF、整数或浮点高精度图片原样复制，保留精度；不执行 HDR 转换。损坏或无法识别的位图也会保留原字节并记录原因，复制成功不代表源文件本身能够正常显示。

不能压缩但成功保留原文件视为成功。只有实际读取或写入失败才记为输出失败，处理其他文件继续进行。原样复制采用流式读写和 SHA-256 复核，并尽可能保留文件时间与权限；符号链接核对链接类型与目标。

## 可选模式：不透明静态位图转 JPG

使用“压缩并转 JPG”入口，或显式添加参数：

```bash
.venv/bin/python scripts/compress_images.py --overwrite --convert-opaque-to-jpeg
```

仅将可安全处理、所有 Alpha 都为 255 的静态位图转为质量 90、4:4:4 JPG。透明图片继续保留自己的格式，动画、多页、高位深和其他文件沿用完整保留规则。跨格式转换按指定参数执行，不保证体积一定更小。

例如 `assets/logo.png` 会变为 `assets/logo.jpg`，引用它的代码不会自动修改。因此处理代码资源包时应优先使用默认模式。

为避免丢文件，原始文件路径优先预留。若 `主图.png` 转换后会占用另一来源的 `主图.jpg`，改用 `主图.png.jpg`；必要时再加 `-2` 等序号。报告记录每个文件的实际输出路径，复制文件保留原名。

## 命令行与参数

手动安装依赖（macOS / Linux 示例）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

默认保留格式，处理整个输入目录：

```bash
.venv/bin/python scripts/compress_images.py --overwrite
```

只处理某个资源包时，同时指定相应输出目录：

```bash
.venv/bin/python scripts/compress_images.py "input/资源包" -o "compressed/资源包" --overwrite
```

跨平台的一键 Python 入口是 `python scripts/run_default.py`，转换模式加 `--convert-opaque-to-jpeg`；macOS / Linux 通常使用 `python3`。Windows 虚拟环境解释器路径为 `.venv\Scripts\python.exe`。

| 参数 | 作用 |
| --- | --- |
| 输入目录（可选位置参数） | 默认 `input/` |
| `-o` / `--output` | 默认 `compressed/` |
| `--reports` | 默认 `reports/`，必须与输入、输出独立，不能互相嵌套 |
| `--overwrite` | 允许覆盖同名输出文件；一键入口默认启用 |
| `--convert-opaque-to-jpeg` | 显式将不透明静态位图转 JPG，默认关闭 |
| `--lossless-png` | PNG 仅做无损优化 |
| `--no-pngquant` | 禁用可选 pngquant 候选 |

pngquant 为可选工具，未安装也能运行；macOS 可使用 `brew install pngquant` 安装。请勿同时运行多个任务，它们共用最新报告。

仅大小写不同的同名输入路径会在开始前报错，以免在 Windows / macOS 上丢文件。旧输出文件若占用了本批次需要的目录路径，也会提示先移走该文件或更换输出目录。

## 复核与报告

```bash
.venv/bin/python scripts/verify_results.py
```

复核检查当前输入文件清单、输出路径唯一性、目录完整性、SHA-256 和字节数。静态位图检查尺寸、格式、Alpha；实际采用质量 90 JPEG 编码的结果还核对量化表和 4:4:4 采样；多帧结果检查全部帧/页面及播放信息，原样复制文件核对内容哈希完全相同。

CSV 使用带 BOM 的 UTF-8，可用 Excel 打开。记录包含输入和输出相对路径、格式、尺寸、帧数/页数、处理方式、体积及说明。`action` 区分 `compressed`（体积减少）、`converted`（显式跨格式转换但未变小）、`copied`（原字节保留）；JSON 另含模式选项、目录清单、总体统计与校验值。

报告、摘要、日志和复核记录固定写入 `reports/`，每次覆盖，不追加历史。任务开始时清除上一批复核和抽样预览等已知附加记录；未开始的任务不替换上一批报告。人工抽样图统一放 `reports/quality-preview.png`，下一次任务会清除。

统计包含当前批次全部成功输出文件，不计入输出区其他旧文件。输入新增、删除、更换或输出被修改后，旧批次复核会报错，应重新处理当前资源包。读取或写入失败会返回退出码 1；全部文件成功输出返回 0；参数错误或路径冲突返回 2。

## 移动与测试

所有默认路径根据脚本位置计算，没有写死外层工具目录、绝对路径或素材批次名称。整个目录可以移动或改名；JSON 根路径相对报告目录保存。手动指定的相对路径按当前终端目录解析。

```bash
.venv/bin/python -m unittest discover -s tests -v
```

自动验证覆盖格式和文件名保留、代码引用不变、多格式真实编码、透明度、JPEG 参数、无损优化、动画时序、多页压缩和丢页检测、高位深及多尺寸图标保留、其他文件复制、隐藏文件与空目录、转换重名、失败回退、覆盖保护，以及移动改名后的复核。
