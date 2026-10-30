#!/usr/bin/env python3
"""独立复核最新压缩报告；所有路径相对于报告定位，可随项目移动。"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image

from image_formats import file_sha256, find_directories, find_files, open_raster, sequence_fingerprint


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "reports/compression-report.json")
    args = parser.parse_args()
    report_path = args.report.expanduser().resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_root = (report_path.parent / report["source_root"]).resolve()
    output_root = (report_path.parent / report["output_root"]).resolve()
    convert_to_jpeg = report.get("options", {"convert_opaque_to_jpeg": True}).get("convert_opaque_to_jpeg", False)
    reference = io.BytesIO()
    Image.new("RGB", (8, 8)).save(reference, format="JPEG", quality=90, subsampling=0)
    with Image.open(io.BytesIO(reference.getvalue())) as im:
        q90_tables = im.quantization
    errors = []
    verified = 0
    outputs = [row["output"].casefold() for row in report["files"] if not row.get("error")]
    if len(outputs) != len(set(outputs)):
        errors.append("报告中存在重复输出路径")
    for directory in report.get("directories", []):
        if not (output_root / directory).is_dir():
            errors.append(f"缺少输出目录：{directory}")
    if "directories" in report:
        inputs = {path.relative_to(source_root).as_posix() for path in find_files(source_root, excluded=output_root.resolve())}
        if inputs != {row["source"] for row in report["files"]}:
            errors.append("当前输入文件清单与报告不一致")
        directories = {path.relative_to(source_root).as_posix()
                       for path in find_directories(source_root, excluded=output_root)}
        if directories != set(report["directories"]):
            errors.append("当前输入目录清单与报告不一致")
    for row in report["files"]:
        if row.get("error"):
            errors.append(f"{row['source']}: 处理时失败：{row['error']}")
            continue
        try:
            source = source_root / row["source"]
            target = output_root / row["output"]
            if row.get("method") == "symlink-copy":
                require(Path(row["output"]) == Path(row["source"]), "符号链接路径不匹配")
                require(source.is_symlink() and target.is_symlink(), "符号链接类型不匹配")
                original_link, copied_link = os.readlink(source), os.readlink(target)
                require(original_link == copied_link == row["link_target"], "符号链接目标不一致")
                digest = hashlib.sha256(os.fsencode(original_link)).hexdigest()
                require(digest == row["source_sha256"] == row["output_sha256"], "符号链接校验值不一致")
                verified += 1
                continue
            if row.get("method") == "original-copy":
                require(Path(row["output"]) == Path(row["source"]), "原样复制文件的目录或文件名不匹配")
                original_hash, copied_hash = file_sha256(source), file_sha256(target)
                require(original_hash == row["source_sha256"], "源文件已变化")
                require(copied_hash == row["output_sha256"] == original_hash, "原样复制文件的字节校验不一致")
                require(source.stat().st_size == row["original_bytes"] and target.stat().st_size == row["output_bytes"], "字节数不匹配")
                verified += 1
                continue
            original, result = source.read_bytes(), target.read_bytes()
            require(hashlib.sha256(original).hexdigest() == row["source_sha256"], "源文件已变化")
            require(hashlib.sha256(result).hexdigest() == row["output_sha256"], "结果文件已变化")
            require(len(original) == row["original_bytes"] and len(result) == row["output_bytes"], "字节数不匹配")
            if row.get("method") == "multiframe-lossless":
                require(Path(row["output"]) == Path(row["source"]), "原格式保留文件的目录或文件名不匹配")
                a = sequence_fingerprint(io.BytesIO(original))
                b = sequence_fingerprint(io.BytesIO(result))
                require(a == b, "动画或多页文件的帧、页面、像素、播放或色彩信息不一致")
                require(len(a[1]) == row["frames"], "帧数或页数与报告不匹配")
                require(len(result) < len(original), "多帧压缩结果没有更小")
                verified += 1
                continue
            with open_raster(io.BytesIO(original)) as src, Image.open(io.BytesIO(result)) as dst:
                require(src.size == dst.size == (row["width"], row["height"]), "尺寸不匹配")
                alpha = src.convert("RGBA").getchannel("A")
                transparent = alpha.getextrema()[0] < 255
                convert = convert_to_jpeg and not transparent
                expected = Path(row["source"]).with_suffix(".jpg") if convert else Path(row["source"])
                actual = Path(row["output"])
                # 重名输出允许保留输入扩展名并附加序号，但目录及目标格式仍受约束。
                suffix = ".jpg" if convert else expected.suffix
                original_name = Path(row["source"]).name
                renamed = (actual.name == original_name + suffix
                           or (actual.name.startswith(original_name + "-") and actual.suffix == suffix
                               and actual.name[len(original_name) + 1:-len(suffix)].isdigit()))
                require(actual == expected or (convert and actual.parent == expected.parent and renamed), "目录或文件名映射不匹配")
                require(dst.format == ("JPEG" if convert else src.format), "输出格式不匹配")
                require(dst.convert("RGBA").getchannel("A").tobytes() == alpha.tobytes(), "Alpha 不匹配")
                if row["method"] == "jpeg-q90-444":
                    require(dst.quantization == q90_tables, "JPEG 质量参数不是 90")
                    require(all(layer[1:3] == (1, 1) for layer in dst.layer), "JPEG 不是 4:4:4")
                if row.get("action") == "copied":
                    require(original == result, "原样保留图片的字节不一致")
                if row["method"] == "native-lossless":
                    require(src.convert("RGBA").tobytes() == dst.convert("RGBA").tobytes(), "同格式无损优化像素不一致")
            verified += 1
        except (ValueError, OSError, KeyError) as exc:
            errors.append(f"{row['source']}: {exc}")
    print(f"逐文件复核：{verified} 个通过，{len(errors)} 个失败。")
    for error in errors:
        print(error)
    (report_path.parent / "verification.log").write_text(
        f"逐文件复核：{verified} 个通过，{len(errors)} 个失败。\n" + "\n".join(errors), encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
