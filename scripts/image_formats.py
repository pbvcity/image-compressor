"""共享位图白名单和文件扫描规则；扫描时不导入第三方依赖。"""
from functools import lru_cache
from pathlib import Path
import hashlib

RASTER_EXTENSIONS = frozenset({
    ".png", ".apng", ".jpg", ".jpeg", ".jpe", ".jfif", ".webp",
    ".bmp", ".dib", ".gif", ".tif", ".tiff", ".ico", ".avif",
    ".heic", ".heif", ".hif", ".tga", ".pcx", ".ppm", ".pgm",
    ".pbm", ".pnm", ".jp2", ".j2k", ".jpc", ".jpf", ".jpx",
    ".j2c", ".qoi",
})
RASTER_FORMATS = (
    "PNG", "JPEG", "WEBP", "BMP", "DIB", "GIF", "TIFF", "ICO",
    "AVIF", "HEIF", "TGA", "PCX", "PPM", "JPEG2000", "QOI",
)


def find_files(source: Path, excluded: Path | None = None):
    for path in source.rglob("*"):
        if ((path.is_file() or path.is_symlink())
                and (excluded is None or excluded not in path.parents)):
            yield path


def find_directories(source: Path, excluded: Path | None = None):
    for path in source.rglob("*"):
        if (path.is_dir() and not path.is_symlink() and path != excluded
                and (excluded is None or excluded not in path.parents)):
            yield path


@lru_cache(maxsize=1)
def register_plugins():
    from pillow_heif import register_heif_opener
    register_heif_opener()


def open_raster(source):
    from PIL import Image
    register_plugins()
    # 限定实际解码器，改名成 .jpg 的 EPS/WMF 等也不会被栅格化。
    return Image.open(source, formats=RASTER_FORMATS)


def sequence_fingerprint(source):
    """比较全部显示帧/页面及播放、色彩信息，不依赖编码后的文件布局。"""
    with open_raster(source) as image:
        header = (image.format, image.info.get("loop"), bool(image.info.get("default_image")))
        frames = []
        for index in range(getattr(image, "n_frames", 1)):
            image.seek(index)
            image.load()
            frames.append((image.size, hashlib.sha256(image.convert("RGBA").tobytes()).digest(),
                           image.info.get("duration", 0), image.info.get("icc_profile"),
                           image.info.get("exif"), image.info.get("dpi"),
                           image.info.get("gamma"), image.info.get("chromaticity"),
                           image.info.get("srgb")))
        return header, tuple(frames)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
