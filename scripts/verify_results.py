#!/usr/bin/env python3
"""独立复核最新压缩报告；所有路径相对于报告定位，可随项目移动。"""
import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image


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
    source_root = report_path.parent / report["source_root"]
    output_root = report_path.parent / report["output_root"]
    reference = io.BytesIO()
    Image.new("RGB", (8, 8)).save(reference, format="JPEG", quality=90, subsampling=0)
    with Image.open(io.BytesIO(reference.getvalue())) as im:
        q90_tables = im.quantization
    errors = []
    verified = 0
    for row in report["files"]:
        if row.get("error"):
            errors.append(f"{row['source']}: 压缩时失败：{row['error']}")
            continue
        try:
            source = source_root / row["source"]
            target = output_root / row["output"]
            original, result = source.read_bytes(), target.read_bytes()
            require(hashlib.sha256(original).hexdigest() == row["source_sha256"], "源文件已变化")
            require(hashlib.sha256(result).hexdigest() == row["output_sha256"], "结果文件已变化")
            require(len(original) == row["original_bytes"] and len(result) == row["output_bytes"], "字节数不匹配")
            with Image.open(io.BytesIO(original)) as src, Image.open(io.BytesIO(result)) as dst:
                require(src.size == dst.size == (row["width"], row["height"]), "尺寸不匹配")
                alpha = src.convert("RGBA").getchannel("A")
                transparent = alpha.getextrema()[0] < 255
                expected = Path(row["source"]).with_suffix(".png" if transparent else ".jpg")
                require(Path(row["output"]) == expected, "目录或文件名映射不匹配")
                require(dst.format == ("PNG" if transparent else "JPEG"), "输出格式不匹配")
                require(dst.convert("RGBA").getchannel("A").tobytes() == alpha.tobytes(), "Alpha 不匹配")
                if not transparent:
                    require(dst.quantization == q90_tables, "JPEG 质量参数不是 90")
                    require(all(layer[1:3] == (1, 1) for layer in dst.layer), "JPEG 不是 4:4:4")
            verified += 1
        except (ValueError, OSError, KeyError) as exc:
            errors.append(f"{row['source']}: {exc}")
    print(f"逐文件复核：{verified} 张通过，{len(errors)} 张失败。")
    for error in errors:
        print(error)
    (report_path.parent / "verification.log").write_text(
        f"逐文件复核：{verified} 张通过，{len(errors)} 张失败。\n" + "\n".join(errors), encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
