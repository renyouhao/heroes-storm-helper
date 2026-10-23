"""从项目自制 SVG 几何重现助手 ICO；仅用标准库，不读取游戏或用户配置。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zlib


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/hots_helper.svg'
SIZES = (16, 24, 32, 48, 64, 96, 128, 256)
SUPERSAMPLE = 4


def load_shapes(source):
    """只接受内置颜色的多边形，不解释图片、脚本、样式、实体或外部链接。"""
    data = Path(source).read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('图标 SVG 不允许实体或外部文档')
    root = ET.fromstring(data)
    if root.tag.split('}')[-1] != 'svg' or root.get('viewBox') != '0 0 256 256':
        raise ValueError('图标 SVG 必须使用 256×256 viewBox')
    shapes = []
    for node in root.iter():
        tag = node.tag.split('}')[-1]
        if tag not in ('svg', 'title', 'desc', 'polygon'):
            raise ValueError('不支持的 SVG 元素：' + tag)
        if any('href' in name.lower() for name in node.attrib):
            raise ValueError('图标 SVG 不允许外部输入')
        if tag != 'polygon':
            continue
        if set(node.attrib) != {'points', 'fill'}:
            raise ValueError('多边形只允许 points 和 fill')
        color = node.get('fill', '')
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
            raise ValueError('图标颜色必须是六位 RGB')
        points = [tuple(float(value) for value in pair.split(','))
                  for pair in node.get('points', '').split()]
        if len(points) < 3 or any(len(point) != 2 or not all(math.isfinite(value) for value in point)
                                  for point in points):
            raise ValueError('图标多边形坐标无效')
        shapes.append((points, color))
    if not shapes:
        raise ValueError('图标 SVG 没有几何图形')
    return shapes


def render_rgba(shapes, size):
    """4×扫描线采样后按覆盖率降采样，透明边缘不混入黑色底。"""
    side = size * SUPERSAMPLE
    scale = side / 256
    pixels = bytearray(side * side * 4)
    for polygon, color in shapes:
        polygon = [(x * scale, y * scale) for x, y in polygon]
        rgba = bytes([*(int(color[i:i + 2], 16) for i in (1, 3, 5)), 255])
        edges = list(zip(polygon, polygon[1:] + polygon[:1]))
        first = max(0, math.floor(min(point[1] for point in polygon)))
        last = min(side, math.ceil(max(point[1] for point in polygon)))
        for y in range(first, last):
            line = y + .5
            intersections = sorted(x0 + (line - y0) * (x1 - x0) / (y1 - y0)
                                   for (x0, y0), (x1, y1) in edges
                                   if y0 <= line < y1 or y1 <= line < y0)
            for a, b in zip(intersections[::2], intersections[1::2]):
                left, right = max(0, math.ceil(a - .5)), min(side, math.ceil(b - .5))
                if right > left:
                    pixels[(y * side + left) * 4:(y * side + right) * 4] = rgba * (right - left)
    result = bytearray()
    for y in range(size):
        for x in range(size):
            total, count = [0, 0, 0], 0
            for oy in range(SUPERSAMPLE):
                for ox in range(SUPERSAMPLE):
                    index = ((y * SUPERSAMPLE + oy) * side + x * SUPERSAMPLE + ox) * 4
                    if pixels[index + 3]:
                        count += 1
                        for channel in (0, 1, 2):
                            total[channel] += pixels[index + channel]
            result.extend([*(round(channel / count) if count else 0 for channel in total),
                           round(count * 255 / SUPERSAMPLE ** 2)])
    return result


def encode_png(size, pixels):
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data
                + struct.pack('!I', zlib.crc32(kind + data)))

    raw = b''.join(b'\0' + pixels[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def build_icon():
    shapes = load_shapes(SOURCE)
    frames = [(size, encode_png(size, render_rgba(shapes, size))) for size in SIZES]
    offset = 6 + len(frames) * 16
    directory = bytearray(struct.pack('<HHH', 0, 1, len(frames)))
    for size, image in frames:
        directory.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0,
                                     1, 32, len(image), offset))
        offset += len(image)
    icon = bytes(directory) + b''.join(image for _, image in frames)
    metadata = {
        'source_type': 'original_vector_artwork',
        'source': 'Project-authored hexagonal storm emblem with an assistant chat-bubble badge',
        'inputs': ['data/hots_helper.svg'],
        'generator': 'tools/build_app_icon.py',
        'processing': 'Python standard-library geometry rasterizer; 4x coverage sampling; PNG-compressed RGBA ICO frames',
        'provenance': 'Redrawn project geometry; no game raster data, executable resources, or private inputs are used',
        'sizes': list(SIZES),
        'sha256': hashlib.sha256(icon).hexdigest(),
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'frames': [dict(size=size, png_sha256=hashlib.sha256(image).hexdigest()) for size, image in frames],
    }
    return icon, metadata, frames


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/hots.ico')
    parser.add_argument('--metadata', type=Path, default=ROOT / 'data/hots_icon_source.json')
    parser.add_argument('--preview-dir', type=Path, help='可选：输出各实际尺寸 PNG 以便预览')
    args = parser.parse_args(argv)
    icon, metadata, frames = build_icon()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(icon)
    args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    if args.preview_dir is not None:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        for size, image in frames:
            (args.preview_dir / f'hots-helper-{size}.png').write_bytes(image)
    print(f'{args.output}\nSHA256 {metadata["sha256"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
