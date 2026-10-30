"""验证一键入口的默认行为及压缩、复核的衔接。"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
SPEC = importlib.util.spec_from_file_location("run_default", PROJECT / "scripts/run_default.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class DefaultLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "移动后的工具 目录"
        (self.root / "scripts").mkdir(parents=True)
        for name in ("compress_images.py", "verify_results.py", "image_formats.py"):
            shutil.copy(PROJECT / "scripts" / name, self.root / "scripts" / name)
        self.root_patch = mock.patch.object(launcher, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        # 测试复用已安装依赖的解释器，避免测试时联网下载。
        self.environment = mock.patch.object(launcher, "ensure_environment", return_value=Path(sys.executable))
        self.prepare = self.environment.start()
        self.addCleanup(self.environment.stop)

    def image(self, name="子目录/图片.png", transparent=False):
        path = self.root / "input" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (17, 11), (115, 52, 219, 37 if transparent else 255)).save(path)
        return path

    def test_empty_input_creates_folder_without_installing_or_replacing_reports(self):
        reports = self.root / "reports"
        reports.mkdir()
        old = reports / "verification.log"
        old.write_text("previous batch", encoding="utf-8")
        self.assertEqual(launcher.main([]), 0)
        self.assertTrue((self.root / "input").is_dir())
        self.prepare.assert_not_called()
        self.assertEqual(old.read_text(encoding="utf-8"), "previous batch")

    def test_symlink_images_are_copied_without_following(self):
        source = self.root / "input"
        source.mkdir()
        original = self.root / "external.png"
        Image.new("RGB", (17, 11), "red").save(original)
        try:
            (source / "link.png").symlink_to(original)
        except OSError:
            self.skipTest("当前系统不允许创建符号链接")
        self.assertEqual(launcher.main([]), 0)
        self.prepare.assert_called_once()
        copied = self.root / "compressed/link.png"
        self.assertTrue(copied.is_symlink())
        self.assertEqual(copied.readlink(), original)

    def test_vector_only_input_is_copied_and_verified(self):
        source = self.root / "input"
        source.mkdir()
        (source / "矢量.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.assertEqual(launcher.main([]), 0)
        self.prepare.assert_called_once()
        self.assertEqual((self.root / "compressed/矢量.svg").read_bytes(), (source / "矢量.svg").read_bytes())

    def test_jpeg_only_input_is_compressed_and_verified(self):
        source = self.root / "input"
        source.mkdir()
        Image.new("RGB", (19, 13), "green").save(source / "照片.JPG")
        self.assertEqual(launcher.main([]), 0)
        self.assertTrue((self.root / "compressed/照片.JPG").is_file())
        self.assertIn("1 个通过，0 个失败", (self.root / "reports/verification.log").read_text(encoding="utf-8"))

    def test_opt_in_jpeg_overwrite_and_verification(self):
        source = self.image()
        transparent = self.image("子目录/透明.PNG", transparent=True)
        before = {p: p.read_bytes() for p in (source, transparent)}
        self.assertEqual(launcher.main(["--convert-opaque-to-jpeg"]), 0)
        output = self.root / "compressed/子目录/图片.jpg"
        self.assertTrue(output.is_file())
        self.assertTrue((self.root / "compressed/子目录/透明.png").is_file())
        output.write_bytes(b"old output")
        unrelated = self.root / "compressed/previous.jpg"
        unrelated.write_bytes(b"keep previous batch")
        self.assertEqual(launcher.main(["--convert-opaque-to-jpeg"]), 0)
        with Image.open(output) as im:
            self.assertEqual(im.size, (17, 11))
        self.assertEqual(unrelated.read_bytes(), b"keep previous batch")
        self.assertIn("2 个通过，0 个失败", (self.root / "reports/verification.log").read_text(encoding="utf-8"))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_unreadable_input_is_copied_and_verified(self):
        self.image()
        (self.root / "input/broken.png").write_bytes(b"broken PNG")
        self.assertEqual(launcher.main([]), 0)
        report = json.loads((self.root / "reports/compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual((self.root / "compressed/broken.png").read_bytes(), b"broken PNG")
        self.assertIn("2 个通过，0 个失败", (self.root / "reports/verification.log").read_text(encoding="utf-8"))

    def test_preflight_failure_does_not_verify_stale_report(self):
        self.image()
        with mock.patch.object(launcher, "command", return_value=2) as run:
            self.assertEqual(launcher.main([]), 2)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
