#!/usr/bin/env python3
"""递归压缩 PNG，保留相对目录结构、像素尺寸及精确 Alpha。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageStat, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("image-compression")
REPORT_FIELDS = [
    "source", "output", "format", "width", "height", "has_transparency",
    "method", "original_bytes", "output_bytes", "saved_percent",
    "alpha_identical", "min_composite_psnr_db", "max_composite_channel_error",
    "source_sha256", "output_sha256", "error",
]


def png_metadata(data: bytes) -> dict:
    """保留色彩解释及 EXIF；丢弃文本、缩略图等非显示所需信息。"""
    info = PngImagePlugin.PngInfo()
    pos = 8
    while pos + 12 <= len(data):
        size = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if kind in (b"gAMA", b"cHRM", b"sRGB", b"cICP"):
            info.add(kind, data[pos + 8:pos + 8 + size])
        pos += size + 12
    with Image.open(io.BytesIO(data)) as im:
        options = {"pnginfo": info}
        for key in ("icc_profile", "exif"):
            if im.info.get(key):
                options[key] = im.info[key]
    return options


def encode_png(im: Image.Image, metadata: dict) -> bytes:
    stream = io.BytesIO()
    im.save(stream, format="PNG", optimize=True, compress_level=9, **metadata)
    return stream.getvalue()


def visual_error(original: Image.Image, candidate: Image.Image) -> tuple[float, int]:
    """黑、白底分别合成；只评估实际可见 RGB，透明像素隐藏颜色不计入。"""
    worst_psnr, max_error = math.inf, 0
    for color in (0, 255):
        background = Image.new("RGB", original.size, (color,) * 3)
        a = background.copy()
        b = background.copy()
        a.paste(original.convert("RGB"), mask=original.getchannel("A"))
        b.paste(candidate.convert("RGB"), mask=candidate.getchannel("A"))
        diff = ImageChops.difference(a, b)
        mse = sum(v * v for v in ImageStat.Stat(diff).rms) / 3
        psnr = 10 * math.log10(255 ** 2 / mse) if mse else math.inf
        worst_psnr = min(worst_psnr, psnr)
        max_error = max(max_error, *(high for _, high in diff.getextrema()))
    return worst_psnr, max_error


def compress_transparent(data: bytes, rgba: Image.Image, *, lossless: bool,
                         pngquant: str | None) -> tuple[bytes, str, float, int]:
    metadata = png_metadata(data)
    # 原文件也是候选，因此透明 PNG 永远不会比原文件大。
    best, method, psnr, error = data, "original", math.inf, 0
    alpha = rgba.getchannel("A")

    def consider(blob: bytes, name: str) -> None:
        nonlocal best, method, psnr, error
        if len(blob) >= len(best):
            return
        with Image.open(io.BytesIO(blob)) as decoded:
            candidate = decoded.convert("RGBA")
        if candidate.size != rgba.size or candidate.getchannel("A").tobytes() != alpha.tobytes():
            raise ValueError("候选图片尺寸或 Alpha 不一致")
        score, peak = visual_error(rgba, candidate)
        if score >= 42 and peak <= 12:
            best, method, psnr, error = blob, name, score, peak

    consider(encode_png(rgba, metadata), "png-lossless")
    if lossless:
        return best, method, psnr, error

    # RGB 最多偏移 1 或 2/255；Alpha 完全不变。保留渐变比强制调色板更可靠。
    for bits in (7, 6):
        step = 2 ** (8 - bits)
        lut = [min(255, ((v + step // 2) // step) * step) for v in range(256)]
        reduced = rgba.convert("RGB").point(lut * 3).convert("RGBA")
        reduced.putalpha(alpha)
        consider(encode_png(reduced, metadata), f"png-rgb-{bits}bit")

    if pngquant:
        with tempfile.TemporaryDirectory(prefix="png-compress-") as folder:
            source, target = Path(folder) / "source.png", Path(folder) / "candidate.png"
            source.write_bytes(data)
            command = [pngquant, "--quality=90-100", "--speed=1", "--force",
                       "--output", str(target), "--", str(source)]
            try:
                result = subprocess.run(command, capture_output=True, timeout=180, check=False)
            except subprocess.TimeoutExpired:
                LOGGER.warning("pngquant 超时，使用其他已校验候选")
            else:
                if result.returncode == 0:
                    with Image.open(target) as im:
                        im.load()
                        candidate = im.copy()
                    if candidate.convert("RGBA").getchannel("A").tobytes() != alpha.tobytes():
                        candidate = candidate.convert("RGBA")
                        candidate.putalpha(alpha)
                    consider(encode_png(candidate, metadata), "pngquant-90-100-exact-alpha")
                elif result.returncode not in (98, 99):
                    message = result.stderr.decode("utf-8", errors="replace").strip()
                    LOGGER.warning("pngquant 失败，使用其他已校验候选：%s", message)
    return best, method, psnr, error


def atomic_write(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".compress-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def inspect_source(path: Path) -> tuple[tuple[int, int], bool]:
    with Image.open(path) as im:
        if im.format != "PNG":
            raise ValueError("文件扩展名为 PNG，但实际格式不是 PNG")
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("不支持动画 APNG，避免丢失动画帧")
        with path.open("rb") as handle:
            header = handle.read(25)
        if header[24] == 16:
            raise ValueError("不支持 16 位 PNG，避免静默降低位深")
        return im.size, im.convert("RGBA").getchannel("A").getextrema()[0] < 255


def process_image(source: Path, target: Path, relative: Path, *, lossless: bool,
                  pngquant: str | None) -> dict:
    data = source.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        rgba = im.convert("RGBA")
        transparent = rgba.getchannel("A").getextrema()[0] < 255
        if transparent:
            blob, method, psnr, peak = compress_transparent(
                data, rgba, lossless=lossless, pngquant=pngquant)
            file_format = "PNG"
        else:
            stream = io.BytesIO()
            options = {key: im.info[key] for key in ("icc_profile", "exif") if im.info.get(key)}
            rgba.convert("RGB").save(stream, format="JPEG", quality=90,
                                     subsampling=0, optimize=True, progressive=True, **options)
            blob, method, file_format = stream.getvalue(), "jpeg-q90-444", "JPEG"
            psnr, peak = None, None

    with Image.open(io.BytesIO(blob)) as check:
        check.load()
        if check.size != rgba.size or check.format != file_format:
            raise ValueError("输出尺寸或格式校验失败")
        alpha_identical = check.convert("RGBA").getchannel("A").tobytes() == rgba.getchannel("A").tobytes()
        if not alpha_identical:
            raise ValueError("输出 Alpha 校验失败")
    atomic_write(target, blob)
    return {
        "source": relative.as_posix(), "output": target.name, "format": file_format,
        "width": rgba.width, "height": rgba.height, "has_transparency": transparent,
        "method": method, "original_bytes": len(data), "output_bytes": len(blob),
        "saved_percent": round((1 - len(blob) / len(data)) * 100, 2),
        "alpha_identical": alpha_identical,
        "min_composite_psnr_db": (round(psnr, 3) if psnr is not None and math.isfinite(psnr) else None),
        "max_composite_channel_error": peak,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "output_sha256": hashlib.sha256(blob).hexdigest(), "error": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=ROOT / "input", help="输入目录，默认项目的 input/")
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "compressed", help="输出目录，默认项目的 compressed/")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports", help="本次报告及日志目录，默认项目的 reports/，每次覆盖")
    parser.add_argument("--lossless-png", action="store_true", help="透明 PNG 仅做无损压缩；不透明图仍转 Q90 JPG")
    parser.add_argument("--no-pngquant", action="store_true", help="禁用可选 pngquant 候选")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖输出目录中的同名图片；报告始终覆盖")
    args = parser.parse_args(argv)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    reports = args.reports.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"输入目录不存在：{source}")
    if output == source or output in source.parents:
        parser.error("输出目录不能是输入目录本身或其父目录")
    if any(reports == folder or reports in folder.parents or folder in reports.parents
           for folder in (source, output)):
        parser.error("日志报告目录必须与输入、输出目录独立，不能相同或互相嵌套")

    files = sorted(p for p in source.rglob("*") if p.is_file()
                   and p.suffix.lower() == ".png" and not p.is_symlink()
                   and output not in p.parents
                   and not any(part.startswith(".") for part in p.relative_to(source).parts))
    if not files:
        parser.error("未找到 PNG 图片")
    # 在写入前预检所有目标路径，避免中途发现同名冲突。
    jobs, rows, targets = [], [], set()
    for path in files:
        relative = path.relative_to(source)
        try:
            _, transparent = inspect_source(path)
            destination = relative.with_suffix(".png" if transparent else ".jpg")
            target = output / destination
            key = str(target).casefold()
            if key in targets:
                parser.error(f"多个输入映射到同一目标：{destination}")
            targets.add(key)
            jobs.append((path, relative, destination, target))
        except (OSError, ValueError) as exc:
            rows.append({"source": relative.as_posix(), "error": str(exc).replace(str(path), relative.as_posix())})
    occupied = [target for _, _, _, target in jobs if target.exists()]
    if occupied and not args.overwrite:
        parser.error(f"已有输出文件：{occupied[0]}。请换输出目录或明确使用 --overwrite")
    reports.mkdir(parents=True, exist_ok=True)
    for handler in LOGGER.handlers[:]:
        handler.close()
        LOGGER.removeHandler(handler)
    # 固定文件名，只保留当前任务。复核和抽样图若尚未重新生成，就不能沿用旧批次。
    for name in ("compression-report.json", "compression-report.csv", "compression-summary.md",
                 "verification.log", "quality-preview.png", "tests.log"):
        (reports / name).unlink(missing_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    LOGGER.addHandler(logging.StreamHandler(sys.stdout))
    LOGGER.addHandler(logging.FileHandler(reports / "compression-run.log", mode="w", encoding="utf-8"))
    pngquant = None if args.no_pngquant else shutil.which("pngquant")
    LOGGER.info("PNG 共 %d 张；pngquant：%s", len(files), '已启用' if pngquant else '未启用')
    for row in rows:
        LOGGER.error("失败：%s: %s", row['source'], row['error'])
    for index, (path, relative, destination, target) in enumerate(jobs, 1):
        try:
            row = process_image(path, target, relative, lossless=args.lossless_png, pngquant=pngquant)
            row["output"] = destination.as_posix()
            LOGGER.info("[%d/%d] %s → %s  节省 %.2f%%", index, len(jobs), relative, destination, row['saved_percent'])
        except Exception as exc:
            message = str(exc).replace(str(path), relative.as_posix()).replace(str(target), destination.as_posix())
            row = {"source": relative.as_posix(), "error": f"{type(exc).__name__}: {message}"}
            LOGGER.error("失败：%s: %s", relative, row['error'])
        rows.append(row)
    rows.sort(key=lambda row: row["source"])
    successful = [row for row in rows if not row.get("error")]
    before = sum(row["original_bytes"] for row in successful)
    after = sum(row["output_bytes"] for row in successful)
    summary = {"input_files": len(files), "succeeded": len(successful),
               "failed": len(rows) - len(successful),
               "original_bytes": before, "output_bytes": after,
               "saved_percent": round((1 - after / before) * 100, 2) if before else 0,
               "pngquant_enabled": bool(pngquant), "lossless_png": args.lossless_png}
    # 路径相对报告所在目录保存，项目整体移动或改名后仍能定位。
    report = {"path_reference": "relative-to-report-directory",
              "source_root": os.path.relpath(source, reports), "output_root": os.path.relpath(output, reports),
              "summary": summary, "files": rows}
    atomic_write(reports / "compression-report.json", (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REPORT_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(reports / "compression-report.csv", stream.getvalue().encode("utf-8-sig"))
    summary_lines = [
        "# 本次批次压缩结果", "",
        "本文件每次运行自动更新，仅描述当前输入批次，不要求任何具体素材文件夹永久存在。", "",
        f"- 输入 PNG：{len(files)} 张；成功：{len(successful)} 张；失败：{summary['failed']} 张。",
        f"- 成功图片原始体积：{before:,} 字节；输出：{after:,} 字节。",
        f"- 总体积减少：{summary['saved_percent']}%。",
        f"- 透明 PNG：{sum(row['format'] == 'PNG' for row in successful)} 张。",
        f"- 不透明 PNG 转质量 90 JPG：{sum(row['format'] == 'JPEG' for row in successful)} 张。",
        "- 保留相对目录结构、像素尺寸及精确 Alpha；原始文件不覆盖。", "",
        "逐文件记录见同目录的 compression-report.csv / compression-report.json。",
        "报告中的 source_root / output_root 相对于报告所在目录解析。", "",
        "如果使用 --overwrite，目录中可能保留以前批次未被本次覆盖的旧文件；它们不计入本报告。", "",
    ]
    atomic_write(reports / "compression-summary.md", "\n".join(summary_lines).encode())
    LOGGER.info(json.dumps(summary, ensure_ascii=False, indent=2))
    for handler in LOGGER.handlers[:]:
        handler.close()
        LOGGER.removeHandler(handler)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
