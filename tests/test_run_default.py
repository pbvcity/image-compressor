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
SPEC = importlib.util.spec_from_file_location("run_default", PROJECT / "scripts/run_default.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class DefaultLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "移动后的工具 目录"
        (self.root / "scripts").mkdir(parents=True)
        for name in ("compress_images.py", "verify_results.py"):
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
        self.assertEqual(launcher.main(), 0)
        self.assertTrue((self.root / "input").is_dir())
        self.prepare.assert_not_called()
        self.assertEqual(old.read_text(encoding="utf-8"), "previous batch")

    def test_hidden_and_symlink_images_are_not_input(self):
        original = self.image(".hidden/图片.png")
        try:
            (self.root / "input/link.png").symlink_to(original)
        except OSError:
            pass  # Windows 未授予创建符号链接权限时仍验证隐藏目录。
        self.assertEqual(launcher.main(), 0)
        self.prepare.assert_not_called()

    def test_real_default_compression_overwrite_and_verification(self):
        source = self.image()
        transparent = self.image("子目录/透明.PNG", transparent=True)
        before = {p: p.read_bytes() for p in (source, transparent)}
        self.assertEqual(launcher.main(), 0)
        output = self.root / "compressed/子目录/图片.jpg"
        self.assertTrue(output.is_file())
        self.assertTrue((self.root / "compressed/子目录/透明.png").is_file())
        output.write_bytes(b"old output")
        unrelated = self.root / "compressed/previous.jpg"
        unrelated.write_bytes(b"keep previous batch")
        self.assertEqual(launcher.main(), 0)
        with Image.open(output) as im:
            self.assertEqual(im.size, (17, 11))
        self.assertEqual(unrelated.read_bytes(), b"keep previous batch")
        self.assertIn("2 张通过，0 张失败", (self.root / "reports/verification.log").read_text(encoding="utf-8"))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_partial_failure_is_verified_and_returns_failure(self):
        self.image()
        (self.root / "input/broken.png").write_bytes(b"broken PNG")
        self.assertEqual(launcher.main(), 1)
        report = json.loads((self.root / "reports/compression-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertIn("1 张通过，1 张失败", (self.root / "reports/verification.log").read_text(encoding="utf-8"))

    def test_preflight_failure_does_not_verify_stale_report(self):
        self.image()
        with mock.patch.object(launcher, "command", return_value=2) as run:
            self.assertEqual(launcher.main(), 2)
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
