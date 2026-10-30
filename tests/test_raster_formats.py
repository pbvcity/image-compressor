"""用真实编码验证多格式位图、透明度、矢量排除及独立复核。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image
from pillow_heif import register_heif_opener

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import compress_images as compress
from image_formats import open_raster
from image_formats import sequence_fingerprint

register_heif_opener()


class RasterFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.output = self.root / "compressed"
        self.reports = self.root / "reports"
        self.source.mkdir()

    def run_compression(self, *extra):
        return compress.main([str(self.source), "-o", str(self.output),
                              "--reports", str(self.reports), "--no-pngquant", *extra])

    def verify(self, expected=0):
        result = subprocess.run([sys.executable, str(PROJECT / "scripts/verify_results.py"),
                                 str(self.reports / "compression-report.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_opaque_bitmap_formats_and_aliases(self):
        formats = [
            ("PNG", "png", "RGB"), ("JPEG", "JPG", "RGB"),
            ("JPEG", "jpeg", "RGB"), ("JPEG", "jfif", "RGB"),
            ("WEBP", "webp", "RGB"), ("BMP", "bmp", "RGB"),
            ("DIB", "dib", "RGB"), ("GIF", "gif", "RGB"),
            ("TIFF", "tif", "RGB"), ("TIFF", "tiff", "RGB"),
            ("ICO", "ico", "RGB"), ("AVIF", "avif", "RGB"),
            ("HEIF", "heic", "RGB"), ("HEIF", "heif", "RGB"),
            ("TGA", "tga", "RGB"), ("PCX", "pcx", "RGB"),
            ("PPM", "ppm", "RGB"), ("PPM", "pgm", "L"),
            ("PPM", "pbm", "1"), ("JPEG2000", "jp2", "RGB"),
            ("QOI", "qoi", "RGB"),
        ]
        originals = {}
        for index, (fmt, extension, mode) in enumerate(formats):
            path = self.source / f"样例{index}.{extension}"
            options = {"sizes": [(32, 32)]} if fmt == "ICO" else {}
            Image.new("RGB", (32, 32), (53, 122, 181)).convert(mode).save(path, format=fmt, **options)
            originals[path] = path.read_bytes()
        self.assertEqual(self.run_compression(), 0)
        for path, data in originals.items():
            with self.subTest(source=path.name), open_raster(path) as src, open_raster(self.output / path.name) as im:
                self.assertEqual(im.format, src.format)
                self.assertEqual(im.size, (32, 32))
                self.assertEqual(path.read_bytes(), data)
                self.assertLessEqual((self.output / path.name).stat().st_size, len(data))
        self.verify()

    def test_transparent_formats_preserve_decoded_alpha_and_original_format(self):
        formats = [("PNG", "png"), ("WEBP", "webp"), ("GIF", "gif"),
                   ("TIFF", "tiff"), ("ICO", "ico"), ("AVIF", "avif"),
                   ("HEIF", "heic"), ("TGA", "tga"), ("QOI", "qoi")]
        for fmt, extension in formats:
            image = Image.new("RGBA", (32, 32), (71, 123, 219, 97))
            image.putpixel((0, 0), (12, 31, 65, 0))
            if fmt == "GIF":
                image = image.convert("P")
                image.info["transparency"] = image.getpixel((0, 0))
            options = {"sizes": [(32, 32)]} if fmt == "ICO" else {}
            image.save(self.source / f"透明-{fmt}.{extension}", format=fmt, **options)
        self.assertEqual(self.run_compression(), 0)
        for path in self.source.iterdir():
            with self.subTest(source=path.name), open_raster(path) as src, open_raster(self.output / path.name) as dst:
                self.assertEqual(dst.format, src.format)
                self.assertEqual(dst.size, src.size)
                self.assertEqual(dst.convert("RGBA").getchannel("A").tobytes(),
                                 src.convert("RGBA").getchannel("A").tobytes())
        self.verify()

    def test_non_png_lossless_mode_preserves_all_decoded_pixels(self):
        source = self.source / "透明.webp"
        Image.new("RGBA", (32, 32), (71, 123, 219, 97)).save(source, lossless=True)
        self.assertEqual(self.run_compression("--lossless-png"), 0)
        with Image.open(source) as src, Image.open(self.output / "透明.webp") as dst:
            self.assertEqual(dst.format, "WEBP")
            self.assertEqual(src.convert("RGBA").tobytes(), dst.convert("RGBA").tobytes())
        self.verify()

    def test_animation_and_multipage_are_complete(self):
        first = Image.new("RGB", (32, 32), "red")
        second = Image.new("RGB", (32, 32), "blue")
        for fmt, extension in (("GIF", "gif"), ("WEBP", "webp"), ("PNG", "apng"),
                               ("TIFF", "tif"), ("AVIF", "avif"), ("HEIF", "heic")):
            first.save(self.source / f"多帧-{fmt}.{extension}", format=fmt,
                       save_all=True, append_images=[second], duration=120, loop=0)
        first.save(self.source / "静态.bmp")
        self.assertEqual(self.run_compression(), 0)
        report = json.loads((self.reports / "compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(len(list(self.output.iterdir())), 7)
        for path in self.source.glob("多帧-*"):
            self.assertEqual(sequence_fingerprint(path), sequence_fingerprint(self.output / path.name))
            self.assertLessEqual((self.output / path.name).stat().st_size, path.stat().st_size)
        self.verify()

    def test_vectors_are_copied_even_when_renamed_to_bitmap_extension(self):
        vectors = {}
        for extension in ("svg", "svgz", "eps", "pdf", "ai", "wmf", "emf"):
            path = self.source / f"矢量.{extension}"
            path.write_bytes(b"vector file must stay unchanged")
            vectors[path] = path.read_bytes()
        # EPS 解码器可识别这段头部，但位图白名单不会启用它。
        disguised = self.source / "伪装.jpg"
        disguised.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 32 32\n")
        vectors[disguised] = disguised.read_bytes()
        Image.new("RGB", (32, 32), "green").save(self.source / "正常.bmp")
        self.assertEqual(self.run_compression(), 0)
        report = json.loads((self.reports / "compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["input_files"], 9)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(len(list(self.output.iterdir())), 9)
        for path, data in vectors.items():
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual((self.output / path.name).read_bytes(), data)
        self.verify()

    def test_different_formats_with_same_stem_are_disambiguated(self):
        Image.new("RGB", (32, 32), "red").save(self.source / "同名.jpg")
        Image.new("RGB", (32, 32), "blue").save(self.source / "同名.webp")
        self.assertEqual(self.run_compression("--overwrite", "--convert-opaque-to-jpeg"), 0)
        self.assertTrue((self.output / "同名.jpg").is_file())
        self.assertTrue((self.output / "同名.webp.jpg").is_file())
        self.verify()

    def test_high_bit_depth_tiff_is_copied(self):
        Image.new("I;16", (32, 32), 32000).save(self.source / "高位深.tiff")
        Image.new("RGB", (32, 32), "red").save(self.source / "普通.bmp")
        self.assertEqual(self.run_compression(), 0)
        self.assertFalse((self.output / "高位深.jpg").exists())
        self.assertEqual((self.output / "高位深.tiff").read_bytes(), (self.source / "高位深.tiff").read_bytes())
        self.verify()

    def test_cmyk_jpeg_and_exif_orientation(self):
        exif = Image.Exif()
        exif[274] = 6
        Image.new("CMYK", (41, 23), (0, 100, 30, 0)).save(self.source / "印刷.jpeg", exif=exif)
        self.assertEqual(self.run_compression(), 0)
        with Image.open(self.output / "印刷.jpeg") as im:
            self.assertIn(im.mode, ("RGB", "CMYK"))
            self.assertEqual(im.size, (41, 23))
            self.assertEqual(im.getexif()[274], 6)
        self.verify()

    def test_unknown_hidden_files_and_empty_directories_are_preserved(self):
        files = {"说明.txt": b"plain text", "数据/custom.xyz": bytes(range(256)),
                 "数据/empty.bin": b"", ".hidden/.settings": b"hidden settings",
                 "文档.pdf": b"original document", "脚本.sh": b"#!/bin/sh\nexit 0\n"}
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.source / "空目录/更深的空目录").mkdir(parents=True)
        self.assertEqual(self.run_compression(), 0)
        self.assertEqual(sum(path.is_file() for path in self.output.rglob("*")), len(files))
        for name, content in files.items():
            self.assertEqual((self.output / name).read_bytes(), content)
        self.assertTrue((self.output / "空目录/更深的空目录").is_dir())
        self.verify()

    def test_empty_directory_only_input_is_preserved(self):
        (self.source / "空目录/子目录").mkdir(parents=True)
        self.assertEqual(self.run_compression(), 0)
        self.assertTrue((self.output / "空目录/子目录").is_dir())
        self.verify()

    def test_conversion_failure_falls_back_to_original_name_and_bytes(self):
        path = self.source / "照片.webp"
        Image.new("RGB", (32, 32), "red").save(path)
        with mock.patch.object(compress, "process_image", side_effect=RuntimeError("encoder failed")):
            self.assertEqual(self.run_compression(), 0)
        self.assertFalse((self.output / "照片.jpg").exists())
        self.assertEqual((self.output / path.name).read_bytes(), path.read_bytes())
        self.verify()

    def test_copy_write_failure_is_reported_as_failure(self):
        (self.source / "文件.txt").write_bytes(b"copy me")
        with mock.patch.object(compress, "preserve_image", side_effect=PermissionError("write denied")):
            self.assertEqual(self.run_compression(), 1)
        report = json.loads((self.reports / "compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["failed"], 1)
        self.verify(expected=1)

    def test_multipage_tiff_is_actually_compressed_and_page_loss_is_detected(self):
        import hashlib
        path = self.source / "分页.tiff"
        first = Image.new("RGB", (64, 48), "red")
        second = Image.new("RGB", (40, 80), "blue")
        first.save(path, save_all=True, append_images=[second], compression="raw")
        self.assertEqual(self.run_compression(), 0)
        report_path = self.reports / "compression-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["files"][0]["method"], "multiframe-lossless")
        self.assertLess((self.output / path.name).stat().st_size, path.stat().st_size)
        self.verify()
        # 即使错误输出的校验值已更新，独立复核仍应发现丢页。
        first.save(self.output / path.name, compression="tiff_adobe_deflate")
        wrong = (self.output / path.name).read_bytes()
        report["files"][0]["output_sha256"] = hashlib.sha256(wrong).hexdigest()
        report["files"][0]["output_bytes"] = len(wrong)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        self.verify(expected=1)

    def test_animation_timing_loop_and_transparency_survive(self):
        frames = [Image.new("RGBA", (32, 32), color)
                  for color in ((200, 10, 30, 0), (10, 200, 30, 127), (10, 30, 200, 255))]
        for fmt, extension in (("GIF", "gif"), ("PNG", "apng"), ("WEBP", "webp")):
            frames[0].save(self.source / f"时序-{fmt}.{extension}", format=fmt,
                           save_all=True, append_images=frames[1:], duration=[70, 130, 250], loop=3)
        self.assertEqual(self.run_compression(), 0)
        for path in self.source.iterdir():
            self.assertEqual(sequence_fingerprint(path), sequence_fingerprint(self.output / path.name))
        self.verify()

    def test_multisize_ico_is_copied_with_all_sizes(self):
        path = self.source / "图标.ico"
        Image.new("RGBA", (64, 64), (10, 20, 200, 127)).save(path, sizes=[(16, 16), (32, 32), (64, 64)])
        self.assertEqual(self.run_compression(), 0)
        self.assertEqual((self.output / path.name).read_bytes(), path.read_bytes())
        self.verify()

    def test_conversion_cannot_take_another_files_original_path(self):
        (self.source / "同名.jpg").write_bytes(b"unreadable jpeg must stay here")
        Image.new("RGB", (32, 32), "red").save(self.source / "同名.png")
        (self.source / "空目录.jpg").mkdir()
        Image.new("RGB", (32, 32), "blue").save(self.source / "空目录.png")
        self.assertEqual(self.run_compression("--convert-opaque-to-jpeg"), 0)
        self.assertEqual((self.output / "同名.jpg").read_bytes(), b"unreadable jpeg must stay here")
        self.assertTrue((self.output / "同名.png.jpg").is_file())
        self.assertTrue((self.output / "空目录.jpg").is_dir())
        self.assertTrue((self.output / "空目录.png.jpg").is_file())
        self.verify()

    def test_default_asset_package_preserves_names_formats_and_code_references(self):
        assets = self.source / "assets"
        assets.mkdir()
        Image.new("RGB", (32, 32), "red").save(assets / "logo.PNG")
        Image.new("RGB", (32, 32), "blue").save(assets / "photo.JPEG")
        Image.new("RGBA", (32, 32), (12, 34, 56, 89)).save(assets / "icon.webp")
        code = b'<img src="assets/logo.PNG"><img src="assets/photo.JPEG"><img src="assets/icon.webp">'
        (self.source / "index.html").write_bytes(code)
        self.assertEqual(self.run_compression(), 0)
        inputs = {path.relative_to(self.source) for path in self.source.rglob("*") if path.is_file()}
        outputs = {path.relative_to(self.output) for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(outputs, inputs)
        self.assertEqual((self.output / "index.html").read_bytes(), code)
        for path in assets.iterdir():
            with open_raster(path) as src, open_raster(self.output / "assets" / path.name) as dst:
                self.assertEqual(dst.format, src.format)
                self.assertEqual(dst.size, src.size)
        self.verify()

    def test_jpeg_opt_in_changes_only_opaque_static_bitmap_format(self):
        Image.new("RGB", (32, 32), "red").save(self.source / "无透明.png")
        Image.new("RGBA", (32, 32), (12, 34, 56, 89)).save(self.source / "透明.webp")
        self.assertEqual(self.run_compression("--convert-opaque-to-jpeg"), 0)
        self.assertFalse((self.output / "无透明.png").exists())
        with Image.open(self.output / "无透明.jpg") as im:
            self.assertEqual(im.format, "JPEG")
        with Image.open(self.output / "透明.webp") as im:
            self.assertEqual(im.format, "WEBP")
        self.verify()

    def test_directory_and_broken_symlinks_are_copied_without_traversal(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "do-not-traverse.txt").write_bytes(b"outside")
        try:
            (self.source / "directory-link").symlink_to(outside, target_is_directory=True)
            (self.source / "broken-link").symlink_to("missing-target")
        except OSError:
            self.skipTest("当前系统不允许创建符号链接")
        self.assertEqual(self.run_compression(), 0)
        for name in ("directory-link", "broken-link"):
            copied = self.output / name
            self.assertTrue(copied.is_symlink())
            self.assertEqual(copied.readlink(), (self.source / name).readlink())
        report = json.loads((self.reports / "compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["input_files"], 2)
        self.verify()


if __name__ == "__main__":
    unittest.main()
