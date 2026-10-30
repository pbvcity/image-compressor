"""用真实编码文件验证透明度、目录映射、覆盖保护及异常输入。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "compress_images", Path(__file__).resolve().parents[1] / "scripts/compress_images.py")
compress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compress)


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.output = self.root / "output"
        self.reports = self.root / "reports"
        self.source.mkdir()

    def run_compression(self, *extra):
        return compress.main([str(self.source), "-o", str(self.output), "--reports", str(self.reports),
                              "--no-pngquant", "--convert-opaque-to-jpeg", *extra])

    def test_nested_structure_dimensions_and_exact_alpha(self):
        folder = self.source / "目录 空格" / "深层"
        folder.mkdir(parents=True)
        original = Image.new("RGBA", (96, 64))
        original.putdata([((x * 11) % 256, (x * 17) % 256, (x * 23) % 256, x % 256)
                          for x in range(96 * 64)])
        original.save(folder / "transparent.PNG")
        Image.new("RGBA", (41, 29), (12, 132, 217, 255)).save(folder / "opaque.png")
        self.assertEqual(self.run_compression(), 0)
        with Image.open(self.output / "目录 空格/深层/transparent.png") as im:
            self.assertEqual(im.size, original.size)
            self.assertEqual(im.convert("RGBA").getchannel("A").tobytes(), original.getchannel("A").tobytes())
            score, peak = compress.visual_error(original, im.convert("RGBA"))
            self.assertGreaterEqual(score, 42)
            self.assertLessEqual(peak, 12)
        with Image.open(self.output / "目录 空格/深层/opaque.jpg") as im:
            self.assertEqual(im.size, (41, 29))
            self.assertEqual(im.format, "JPEG")
            # 真实 JPEG 的量化表确认质量参数及 4:4:4 编码设置。
            reference = self.root / "q90.jpg"
            Image.new("RGB", (41, 29)).save(reference, quality=90, subsampling=0)
            with Image.open(reference) as expected:
                self.assertEqual(im.quantization, expected.quantization)
            self.assertTrue(all(layer[1:3] == (1, 1) for layer in im.layer))
        self.assertTrue((folder / "opaque.png").exists())
        self.assertLessEqual((self.output / "目录 空格/深层/transparent.png").stat().st_size,
                             (folder / "transparent.PNG").stat().st_size)

    def test_palette_trns_and_rgb_opaque(self):
        palette = Image.new("P", (8, 8), 0)
        palette.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        palette.putpixel((0, 0), 1)
        palette.save(self.source / "palette.png", transparency=bytes([255, 37]))
        Image.new("RGB", (8, 8), "blue").save(self.source / "rgb.png")
        self.assertEqual(self.run_compression(), 0)
        with Image.open(self.output / "palette.png") as im:
            self.assertEqual(im.convert("RGBA").getpixel((0, 0))[3], 37)
        self.assertTrue((self.output / "rgb.jpg").exists())

    def test_lossless_and_colour_metadata(self):
        original = Image.new("RGBA", (32, 32), (111, 152, 213, 127))
        metadata = compress.PngImagePlugin.PngInfo()
        metadata.add(b"gAMA", compress.struct.pack(">I", 45455))
        original.save(self.source / "lossless.png", pnginfo=metadata)
        self.assertEqual(self.run_compression("--lossless-png"), 0)
        with Image.open(self.output / "lossless.png") as im:
            self.assertEqual(im.convert("RGBA").tobytes(), original.tobytes())
            self.assertAlmostEqual(im.info["gamma"], 0.45455)

    def test_overwrite_requires_explicit_flag(self):
        Image.new("RGB", (8, 8), "red").save(self.source / "a.png")
        self.assertEqual(self.run_compression(), 0)
        before = (self.output / "a.jpg").read_bytes()
        with self.assertRaises(SystemExit) as raised:
            self.run_compression()
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual((self.output / "a.jpg").read_bytes(), before)
        self.assertEqual(self.run_compression("--overwrite"), 0)

    def test_unreadable_and_high_depth_inputs_are_preserved(self):
        (self.source / "bad.png").write_bytes(b"not a PNG")
        Image.new("I;16", (8, 8)).save(self.source / "16bit.png")
        Image.new("RGB", (8, 8), "red").save(self.source / "ok.png")
        self.assertEqual(self.run_compression(), 0)
        report = json.loads((self.reports / "compression-report.json").read_text())
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["succeeded"], 3)
        self.assertEqual(report["summary"]["copied"], 2)
        for name in ("bad.png", "16bit.png"):
            self.assertEqual((self.output / name).read_bytes(), (self.source / name).read_bytes())

    def test_nested_output_is_excluded(self):
        self.output = self.source / "output"
        self.output.mkdir()
        Image.new("RGBA", (8, 8), (1, 2, 3, 0)).save(self.source / "a.png")
        Image.new("RGB", (8, 8)).save(self.output / "old.png")
        self.assertEqual(self.run_compression(), 0)
        report = json.loads((self.reports / "compression-report.json").read_text())
        self.assertEqual(report["summary"]["input_files"], 1)

    def test_reports_replaced_and_portable_after_project_rename(self):
        import shutil
        import subprocess
        import sys
        Image.new("RGB", (8, 8), "red").save(self.source / "old.png")
        self.assertEqual(self.run_compression(), 0)
        (self.reports / "verification.log").write_text("old verification")
        (self.source / "old.png").unlink()
        Image.new("RGB", (11, 7), "blue").save(self.source / "new.png")
        self.assertEqual(self.run_compression(), 0)
        report = json.loads((self.reports / "compression-report.json").read_text())
        self.assertEqual([row['source'] for row in report['files']], ['new.png'])
        self.assertFalse(Path(report['source_root']).is_absolute())
        self.assertFalse(Path(report['output_root']).is_absolute())
        self.assertFalse((self.reports / "verification.log").exists())
        self.assertNotIn("old.png", (self.reports / "compression-run.log").read_text())
        self.assertFalse((self.source / "compression-report.json").exists())
        self.assertFalse((self.output / "compression-report.json").exists())
        renamed = self.root / "改名后的项目"
        renamed.mkdir()
        for directory in (self.source, self.output, self.reports):
            shutil.move(str(directory), renamed / directory.name)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/verify_results.py"),
                                 str(renamed / "reports/compression-report.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reports_cannot_be_inside_input_or_output(self):
        Image.new("RGB", (8, 8)).save(self.source / "a.png")
        for reports in (self.source / "reports", self.output / "reports"):
            with self.subTest(reports=reports), self.assertRaises(SystemExit):
                self.run_compression("--reports", str(reports))

    def test_default_paths_follow_script_after_move(self):
        import shutil
        import subprocess
        import sys
        original = self.root / "temporary-tool"
        (original / "scripts").mkdir(parents=True)
        (original / "input/任意新批次/子目录").mkdir(parents=True)
        shutil.copy(Path(__file__).resolve().parents[1] / "scripts/compress_images.py", original / "scripts")
        shutil.copy(Path(__file__).resolve().parents[1] / "scripts/image_formats.py", original / "scripts")
        Image.new("RGB", (9, 7), "green").save(original / "input/任意新批次/子目录/a.png")
        moved = self.root / "已移动且改名"
        original.rename(moved)
        result = subprocess.run([sys.executable, str(moved / "scripts/compress_images.py"), "--no-pngquant", "--convert-opaque-to-jpeg"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((moved / "compressed/任意新批次/子目录/a.jpg").exists())
        self.assertTrue((moved / "reports/compression-report.json").exists())


if __name__ == "__main__":
    unittest.main()
