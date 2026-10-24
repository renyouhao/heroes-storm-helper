"""原创助手图标的多尺寸结构、透明度、来源和离线重现。"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

try:
    from tools import build_app_icon
except ImportError:
    build_app_icon = None


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 24, 32, 48, 64, 96, 128, 256)


def ico_frames(data):
    reserved, kind, count = struct.unpack_from('<HHH', data)
    if (reserved, kind) != (0, 1):
        raise ValueError('不是 ICO')
    frames = []
    for index in range(count):
        w, h, colors, unused, planes, bits, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + index * 16)
        if offset < 6 + count * 16 or offset + length > len(data):
            raise ValueError('ICO 图像范围无效')
        frames.append((w or 256, h or 256, planes, bits, data[offset:offset + length]))
    return frames


def png_rgba(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('不是 PNG')
    position, packed = 8, bytearray()
    while position < len(data):
        length = struct.unpack_from('!I', data, position)[0]
        kind = data[position + 4:position + 8]
        chunk = data[position + 8:position + 8 + length]
        checksum = struct.unpack_from('!I', data, position + 8 + length)[0]
        if zlib.crc32(kind + chunk) != checksum:
            raise ValueError('PNG CRC 不匹配')
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('!2I5B', chunk)
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError('PNG 不是非交错 8-bit RGBA')
        elif kind == b'IDAT':
            packed.extend(chunk)
        position += length + 12
    raw, rows = zlib.decompress(packed), bytearray()
    stride = width * 4
    previous = bytearray(stride)
    for y in range(height):
        mode = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            left = row[x - 4] if x >= 4 else 0
            up = previous[x]
            diagonal = previous[x - 4] if x >= 4 else 0
            if mode == 1:
                row[x] = (row[x] + left) & 255
            elif mode == 2:
                row[x] = (row[x] + up) & 255
            elif mode == 3:
                row[x] = (row[x] + (left + up) // 2) & 255
            elif mode == 4:
                p = left + up - diagonal
                distances = (abs(p - left), abs(p - up), abs(p - diagonal))
                predictor = (left, up, diagonal)[distances.index(min(distances))]
                row[x] = (row[x] + predictor) & 255
            elif mode != 0:
                raise ValueError('PNG 过滤器无效')
        rows.extend(row)
        previous = row
    return width, height, rows


class AppIconTests(unittest.TestCase):
    def test_ico_contains_eight_rgba_png_sizes_with_transparent_outer_corners(self):
        frames = ico_frames((ROOT / 'data/hots.ico').read_bytes())
        self.assertEqual([(w, h) for w, h, *_ in frames], [(size, size) for size in SIZES])
        for width, height, planes, bits, data in frames:
            with self.subTest(size=width):
                self.assertEqual((planes, bits), (1, 32))
                png_width, png_height, pixels = png_rgba(data)
                self.assertEqual((png_width, png_height), (width, height))
                for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
                    self.assertEqual(pixels[(y * width + x) * 4 + 3], 0)
                self.assertEqual(pixels[((height // 2) * width + width // 2) * 4 + 3], 255)
                self.assertTrue(any(0 < alpha < 255 for alpha in pixels[3::4]), '需要抗锯齿透明边缘')

    def test_metadata_hashes_identify_only_project_authored_vector_inputs(self):
        metadata = json.loads((ROOT / 'data/hots_icon_source.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata.get('source_type'), 'original_vector_artwork')
        self.assertEqual(metadata.get('inputs'), ['data/hots_helper.svg'])
        self.assertEqual(metadata['sizes'], list(SIZES))
        self.assertEqual(metadata['sha256'], hashlib.sha256((ROOT / 'data/hots.ico').read_bytes()).hexdigest())
        self.assertEqual(metadata['source_sha256'], hashlib.sha256((ROOT / 'data/hots_helper.svg').read_bytes()).hexdigest())
        frames = ico_frames((ROOT / 'data/hots.ico').read_bytes())
        self.assertEqual(metadata['frames'], [dict(size=w, png_sha256=hashlib.sha256(data).hexdigest())
                                              for w, _, _, _, data in frames])

    def test_svg_is_self_contained_geometry_without_game_raster_or_external_inputs(self):
        source = ROOT / 'data/hots_helper.svg'
        self.assertTrue(source.is_file(), '需要项目自制 SVG 源文件')
        tree = ET.parse(source)
        for node in tree.iter():
            self.assertIn(node.tag.split('}')[-1], ('svg', 'title', 'desc', 'polygon'))
            self.assertFalse(any('href' in name.lower() for name in node.attrib))

    def test_stdlib_cli_reproduces_committed_bytes_and_all_preview_sizes(self):
        self.assertIsNotNone(build_app_icon, '需要可复现的图标生成器')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            icon, metadata, preview = base / 'hots.ico', base / 'source.json', base / 'preview'
            result = subprocess.run([sys.executable, '-S', str(ROOT / 'tools/build_app_icon.py'),
                                     '--output', str(icon), '--metadata', str(metadata),
                                     '--preview-dir', str(preview)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(icon.read_bytes(), (ROOT / 'data/hots.ico').read_bytes())
            self.assertEqual(metadata.read_bytes(), (ROOT / 'data/hots_icon_source.json').read_bytes())
            for size in SIZES:
                width, height, _ = png_rgba((preview / f'hots-helper-{size}.png').read_bytes())
                self.assertEqual((width, height), (size, size))

    def test_svg_loader_rejects_embedded_or_external_images(self):
        self.assertIsNotNone(build_app_icon, '需要只接受几何输入的 SVG 解析器')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'invalid.svg'
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
                              '<image href="file:///private-input.png"/></svg>', encoding='utf-8')
            with self.assertRaises(ValueError):
                build_app_icon.load_shapes(source)


if __name__ == '__main__':
    unittest.main()
