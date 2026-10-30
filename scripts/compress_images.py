#!/usr/bin/env python3
"""压缩常见位图，完整保留动画/多页及其他文件，原图不改写。"""
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

from PIL import Image, ImageChops, ImageCms, ImageStat, PngImagePlugin

from image_formats import RASTER_EXTENSIONS, file_sha256, find_directories, find_files, open_raster, sequence_fingerprint

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("image-compression")
REPORT_FIELDS = [
    "source", "output", "format", "width", "height", "has_transparency",
    "method", "original_bytes", "output_bytes", "saved_percent",
    "alpha_identical", "min_composite_psnr_db", "max_composite_channel_error",
    "source_sha256", "output_sha256", "action", "frames", "link_target", "note", "error",
]


class PreserveOriginal(Exception):
    def __init__(self, note: str, *, optimize: bool = False):
        super().__init__(note)
        self.optimize = optimize


def image_metadata(data: bytes) -> dict:
    """保留色彩解释及 EXIF；丢弃文本、缩略图等非显示所需信息。"""
    info = PngImagePlugin.PngInfo()
    pos = 8
    while data.startswith(b"\x89PNG\r\n\x1a\n") and pos + 12 <= len(data):
        size = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if kind in (b"gAMA", b"cHRM", b"sRGB", b"cICP"):
            info.add(kind, data[pos + 8:pos + 8 + size])
        pos += size + 12
    with open_raster(io.BytesIO(data)) as im:
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
    metadata = image_metadata(data)
    # 仅 PNG 原文件可直接参与候选；其他透明位图先无损编码为 PNG。
    original_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    baseline = data if original_png else encode_png(rgba, metadata)
    best, method = baseline, "original" if original_png else "png-lossless"
    psnr, error = math.inf, 0
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
            source.write_bytes(baseline)
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
    with open_raster(path) as im:
        if im.format == "PNG":
            with path.open("rb") as handle:
                header = handle.read(25)
            if header[24] == 16:
                raise PreserveOriginal("16 位 PNG 原样保留，避免降低位深")
        bits = im.tag_v2.get(258, (8,)) if im.format == "TIFF" else (8,)
        if isinstance(bits, int):
            bits = (bits,)
        if (max(bits) > 8 or im.info.get("bit_depth", 8) > 8
                or im.mode in ("I", "F") or im.mode.startswith("I;16")):
            raise PreserveOriginal("高位深或浮点图片原样保留，避免降低精度")
        if im.format == "ICO" and len(im.ico.sizes()) > 1:
            raise PreserveOriginal("多尺寸 ICO 原样保留全部图标")
        if getattr(im, "n_frames", 1) != 1:
            raise PreserveOriginal("动画或多页图片保留全部帧/页面", optimize=True)
        return im.size, im.convert("RGBA").getchannel("A").getextrema()[0] < 255


def byte_fields(relative: Path, target: Path, data: bytes, blob: bytes) -> dict:
    return {
        "source": relative.as_posix(), "output": target.name,
        "original_bytes": len(data), "output_bytes": len(blob),
        "saved_percent": round((1 - len(blob) / len(data)) * 100, 2) if data else 0,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "output_sha256": hashlib.sha256(blob).hexdigest(), "error": "",
    }


def optimize_sequence(data: bytes) -> tuple[bytes, str]:
    """只接受全部帧/页面解码后完全一致且更小的候选，否则返回原字节。"""
    try:
        with open_raster(io.BytesIO(data)) as im:
            if im.format not in ("GIF", "PNG", "WEBP", "TIFF") or im.info.get("default_image"):
                return data, "该多帧格式暂不重编码，原样保留"
            file_format = im.format
            options = {key: im.info[key] for key in ("loop", "icc_profile", "exif") if key in im.info}
            frames, durations, pixels = [], [], 0
            for index in range(im.n_frames):
                im.seek(index)
                im.load()
                pixels += im.width * im.height
                if pixels > 25_000_000:
                    return data, "多帧文件超过重编码内存预算，原样保留"
                if im.mode in ("I", "F") or im.mode.startswith("I;16"):
                    return data, "含高位深页面，原样保留"
                if im.format == "TIFF" and max(im.tag_v2.get(258, (8,))) > 8:
                    return data, "含高位深页面，原样保留"
                frames.append(im.copy() if file_format == "TIFF" else im.convert("RGBA"))
                durations.append(im.info.get("duration", 0))
            if file_format != "TIFF":
                options["duration"] = durations
            if file_format == "WEBP":
                options.update(lossless=True, quality=100, method=6)
            elif file_format == "TIFF":
                options["compression"] = "tiff_adobe_deflate"
            elif file_format == "PNG":
                options.update(image_metadata(data))
                options.update(optimize=True, compress_level=9, disposal=0, blend=0)
            else:
                options.update(optimize=True, disposal=2)
            stream = io.BytesIO()
            frames[0].save(stream, format=file_format, save_all=True,
                           append_images=frames[1:], **options)
        candidate = stream.getvalue()
        if len(candidate) < len(data) and sequence_fingerprint(io.BytesIO(candidate)) == sequence_fingerprint(io.BytesIO(data)):
            return candidate, "全部帧/页面、像素、时长和循环信息校验通过"
        return data, "未得到更小且完整校验通过的多帧候选，原样保留"
    except Exception as exc:
        return data, f"多帧重编码未通过，原样保留（{type(exc).__name__}）"


def preserve_image(source: Path, target: Path, relative: Path, note: str, *, optimize: bool = False) -> dict:
    if source.is_symlink():
        link = os.readlink(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".compress-", dir=target.parent)
        os.close(fd)
        Path(temporary).unlink()
        try:
            os.symlink(link, temporary, target_is_directory=source.is_dir())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        content = os.fsencode(link)
        row = byte_fields(relative, target, content, content)
        row.update(format="SYMLINK", width=None, height=None, frames=None,
                   has_transparency=None, alpha_identical=None,
                   min_composite_psnr_db=None, max_composite_channel_error=None,
                   method="symlink-copy", action="copied", link_target=link, note=note)
        return row
    if not optimize:
        # 代码包、压缩包等未知文件采用流式复制，避免整文件载入内存。
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".compress-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as dst, source.open("rb") as src:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            original_hash, copied_hash = file_sha256(source), file_sha256(Path(temporary))
            if original_hash != copied_hash:
                raise ValueError("复制期间源文件变化或复制校验失败")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        try:
            shutil.copystat(source, target)
        except OSError:
            note += "；文件字节已保留，但系统未能复制全部文件属性"
        file_format, width, height, frames = "UNKNOWN", None, None, None
        if source.suffix.lower() in RASTER_EXTENSIONS:
            try:
                with open_raster(source) as im:
                    file_format, (width, height), frames = im.format, im.size, getattr(im, "n_frames", 1)
            except Exception:
                pass
        return {
            "source": relative.as_posix(), "output": target.name,
            "format": file_format, "width": width, "height": height,
            "original_bytes": target.stat().st_size, "output_bytes": target.stat().st_size,
            "saved_percent": 0, "source_sha256": original_hash, "output_sha256": copied_hash,
            "has_transparency": None, "alpha_identical": True,
            "min_composite_psnr_db": None, "max_composite_channel_error": None,
            "method": "original-copy", "action": "copied", "frames": frames, "note": note, "error": "",
        }
    data = source.read_bytes()
    blob, note = optimize_sequence(data) if optimize else (data, note)
    file_format, width, height, frames = "UNKNOWN", None, None, None
    try:
        with open_raster(io.BytesIO(data)) as im:
            file_format, (width, height), frames = im.format, im.size, getattr(im, "n_frames", 1)
    except Exception:
        pass  # 无法解码的位图仍按字节保留，报告不虚构尺寸或格式。
    atomic_write(target, blob)
    if blob == data:
        try:
            shutil.copystat(source, target)
        except OSError:
            note += "；文件字节已保留，但系统未能复制全部文件属性"
    return {
        **byte_fields(relative, target, data, blob),
        "format": file_format, "width": width, "height": height,
        "has_transparency": None, "alpha_identical": True,
        "min_composite_psnr_db": None, "max_composite_channel_error": None,
        "method": "original-copy" if blob == data else "multiframe-lossless",
        "action": "copied" if blob == data else "compressed", "frames": frames, "note": note,
    }


def process_image(source: Path, target: Path, relative: Path, *, lossless: bool,
                  pngquant: str | None, convert_opaque_to_jpeg: bool = False) -> dict:
    data = source.read_bytes()
    with open_raster(io.BytesIO(data)) as im:
        rgba = im.convert("RGBA")
        transparent = rgba.getchannel("A").getextrema()[0] < 255
        convert = convert_opaque_to_jpeg and not transparent
        file_format = "JPEG" if convert else im.format
        psnr, peak = None, None
        if file_format == "PNG":
            blob, method, psnr, peak = compress_transparent(
                data, rgba, lossless=lossless, pngquant=pngquant)
        elif file_format == "JPEG":
            stream = io.BytesIO()
            options = {key: im.info[key] for key in ("icc_profile", "exif") if im.info.get(key)}
            rgb = rgba.convert("RGB")
            if im.mode == "CMYK" and options.get("icc_profile"):
                profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                rgb = ImageCms.profileToProfile(
                    im, ImageCms.ImageCmsProfile(io.BytesIO(options["icc_profile"])),
                    profile, outputMode="RGB")
                options["icc_profile"] = profile.tobytes()
            rgb.save(stream, format="JPEG", quality=90,
                                     subsampling=0, optimize=True, progressive=True, **options)
            blob, method = stream.getvalue(), "jpeg-q90-444"
            # 同格式优化只保留更小的候选；显式跨格式转 JPG 时按指定参数输出。
            if im.format == "JPEG" and len(blob) >= len(data):
                blob, method = data, "original"
        elif file_format in ("AVIF", "HEIF"):
            # 暂不对已有高压缩率的现代有损格式再次有损编码。
            blob, method = data, "original"
        else:
            stream = io.BytesIO()
            options = {key: im.info[key] for key in ("icc_profile", "exif", "dpi") if im.info.get(key)}
            if file_format == "WEBP":
                options.update(lossless=True, quality=100, method=6, exact=True)
            elif file_format == "GIF":
                options["optimize"] = True
            elif file_format == "TIFF":
                options["compression"] = "tiff_adobe_deflate"
            elif file_format == "TGA":
                options["compression"] = "tga_rle"
            elif file_format == "JPEG2000":
                options["irreversible"] = False
            elif file_format == "ICO":
                options["sizes"] = [im.size]
            im.save(stream, format=file_format, **options)
            candidate = stream.getvalue()
            with open_raster(io.BytesIO(candidate)) as decoded:
                decoded.load()
                identical = (decoded.format == file_format and decoded.size == im.size
                             and decoded.convert("RGBA").tobytes() == rgba.tobytes()
                             and all(decoded.info.get(key) == im.info.get(key)
                                     for key in ("icc_profile", "exif") if im.info.get(key)))
            if identical and len(candidate) < len(data):
                blob, method = candidate, "native-lossless"
            else:
                blob, method = data, "original"

    with Image.open(io.BytesIO(blob)) as check:
        check.load()
        if check.size != rgba.size or check.format != file_format:
            raise ValueError("输出尺寸或格式校验失败")
        alpha_identical = check.convert("RGBA").getchannel("A").tobytes() == rgba.getchannel("A").tobytes()
        if not alpha_identical:
            raise ValueError("输出 Alpha 校验失败")
    atomic_write(target, blob)
    return {
        **byte_fields(relative, target, data, blob), "format": file_format,
        "width": rgba.width, "height": rgba.height, "has_transparency": transparent,
        "method": method,
        "alpha_identical": alpha_identical,
        "min_composite_psnr_db": (round(psnr, 3) if psnr is not None and math.isfinite(psnr) else None),
        "max_composite_channel_error": peak,
        "action": "copied" if blob == data else ("compressed" if len(blob) < len(data) else "converted"),
        "frames": 1, "note": "",
    }


def plan_jobs(files: list[Path], source: Path, output: Path, *, convert_opaque_to_jpeg: bool = False) -> list[dict]:
    """预留所有原文件位置，转换重名时保留原扩展名并加序号。"""
    originals = {path.relative_to(source).as_posix().casefold() for path in files}
    if len(originals) != len(files):
        raise ValueError("输入存在仅大小写不同的同名路径，无法在各系统上完整输出，请先改名")
    directories = {path.relative_to(source).as_posix().casefold()
                   for path in find_directories(source, excluded=output)}
    if originals & directories:
        raise ValueError("输入文件与目录存在仅大小写不同的同名路径，请先改名")
    used, jobs = set(), []
    for path in files:
        relative = path.relative_to(source)
        policy, note = "convert", ""
        destination = relative
        if path.is_symlink():
            policy, note = "copy", "符号链接原样复制，不跟随目标"
        elif path.suffix.lower() not in RASTER_EXTENSIONS:
            policy, note = "copy", "非压缩格式，原样复制"
        else:
            try:
                _, transparent = inspect_source(path)
                if convert_opaque_to_jpeg and not transparent:
                    destination = relative.with_suffix(".jpg")
            except PreserveOriginal as exc:
                policy, note = ("sequence" if exc.optimize else "copy"), str(exc)
            except Exception as exc:
                policy, note = "copy", f"无法安全解码，原样保留：{str(exc).replace(str(path), relative.as_posix())}"
        key = destination.as_posix().casefold()
        number = 0
        while key in used or key in directories or (key in originals and key != relative.as_posix().casefold()):
            number += 1
            suffix = destination.suffix
            addition = "" if number == 1 else f"-{number}"
            destination = relative.with_name(f"{relative.name}{addition}{suffix}")
            key = destination.as_posix().casefold()
        used.add(key)
        jobs.append({"source": path, "relative": relative, "destination": destination,
                     "target": output / destination, "policy": policy, "note": note})
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=ROOT / "input", help="输入目录，默认项目的 input/")
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "compressed", help="输出目录，默认项目的 compressed/")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports", help="本次报告及日志目录，默认项目的 reports/，每次覆盖")
    parser.add_argument("--lossless-png", action="store_true", help="PNG 仅做无损优化，不进行 RGB 轻微量化")
    parser.add_argument("--no-pngquant", action="store_true", help="禁用可选 pngquant 候选")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖输出目录中的同名图片；报告始终覆盖")
    parser.add_argument("--convert-opaque-to-jpeg", action="store_true",
                        help="将可处理的不透明静态位图转为质量 90 JPG；默认保留格式和文件名")
    args = parser.parse_args(argv)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    reports = args.reports.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"输入目录不存在：{source}")
    if output == source or output in source.parents:
        parser.error("输出目录不能是输入目录本身或其父目录")
    if output.exists() and not output.is_dir():
        parser.error("输出目录路径已被普通文件占用，请先移走该文件或换输出目录")
    if any(reports == folder or reports in folder.parents or folder in reports.parents
           for folder in (source, output)):
        parser.error("日志报告目录必须与输入、输出目录独立，不能相同或互相嵌套")

    files = sorted(find_files(source, excluded=output))
    directories = sorted(find_directories(source, excluded=output))
    if not files and not directories:
        parser.error("输入目录中未找到文件")
    try:
        jobs = plan_jobs(files, source, output, convert_opaque_to_jpeg=args.convert_opaque_to_jpeg)
    except ValueError as exc:
        parser.error(str(exc))
    rows = []
    # 也预检压缩失败后原样复制的位置；它们已在计划阶段预留，不与其他输出冲突。
    occupied = [target for job in jobs for target in (job["target"], output / job["relative"])
                if target.exists() or target.is_symlink()]
    if occupied and not args.overwrite:
        parser.error(f"已有输出文件：{occupied[0]}。请换输出目录或明确使用 --overwrite")
    blocked = [output / path.relative_to(source) for path in directories
               if (output / path.relative_to(source)).exists() and not (output / path.relative_to(source)).is_dir()]
    if blocked:
        parser.error(f"输出目录路径被已有文件占用：{blocked[0]}。请先移走该文件或换输出目录")
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
    LOGGER.info("输入文件共 %d 个；pngquant：%s", len(files), '已启用' if pngquant else '未启用')
    LOGGER.info("处理模式：%s", "不透明静态位图转 JPG" if args.convert_opaque_to_jpeg else "保留格式和文件名")
    # 空目录也保留；不跟随符号链接，不扫描嵌套输出区。
    output.mkdir(parents=True, exist_ok=True)
    for directory in directories:
        (output / directory.relative_to(source)).mkdir(parents=True, exist_ok=True)
    for index, job in enumerate(jobs, 1):
        path, relative, destination, target = (job[key] for key in ("source", "relative", "destination", "target"))
        try:
            if job["policy"] == "convert":
                try:
                    row = process_image(path, target, relative, lossless=args.lossless_png, pngquant=pngquant,
                                        convert_opaque_to_jpeg=args.convert_opaque_to_jpeg)
                except Exception as exc:
                    destination, target = relative, output / relative
                    row = preserve_image(path, target, relative,
                                         f"压缩失败，原样保留（{type(exc).__name__}）")
            else:
                row = preserve_image(path, target, relative, job["note"], optimize=job["policy"] == "sequence")
            row["output"] = destination.as_posix()
            LOGGER.info("[%d/%d] %s → %s  %s  节省 %.2f%%", index, len(jobs), relative, destination,
                        {"copied": "原样复制", "compressed": "已压缩", "converted": "已转换"}[row["action"]], row['saved_percent'])
            if row.get("note"):
                LOGGER.info("  %s", row["note"])
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
               "compressed": sum(row["action"] == "compressed" for row in successful),
               "converted": sum(row["action"] == "converted" for row in successful),
               "copied": sum(row["action"] == "copied" for row in successful),
               "original_bytes": before, "output_bytes": after,
               "saved_percent": round((1 - after / before) * 100, 2) if before else 0,
               "pngquant_enabled": bool(pngquant), "lossless_png": args.lossless_png}
    # 路径相对报告所在目录保存，项目整体移动或改名后仍能定位。
    report = {"path_reference": "relative-to-report-directory",
              "options": {"convert_opaque_to_jpeg": args.convert_opaque_to_jpeg},
              "source_root": os.path.relpath(source, reports), "output_root": os.path.relpath(output, reports),
              "directories": [path.relative_to(source).as_posix() for path in directories],
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
        f"- 输入文件：{len(files)} 个；成功输出：{len(successful)} 个；失败：{summary['failed']} 个。",
        f"- 已压缩：{summary['compressed']} 个；已转换：{summary['converted']} 个；原样复制：{summary['copied']} 个。",
        f"- 成功输出文件原始体积：{before:,} 字节；输出：{after:,} 字节。",
        f"- 总体积减少：{summary['saved_percent']}%。",
        f"- 静态位图重编码：{sum(row['method'] not in ('original-copy', 'multiframe-lossless') for row in successful)} 张。",
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
