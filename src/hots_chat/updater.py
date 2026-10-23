"""显式调用的官方发行检查和隔离下载；导入不联网、不启动线程。"""
from dataclasses import dataclass
from functools import total_ordering
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import struct
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from . import APP_NAME, RELEASE_ARCHIVE_PREFIX, __version__

REPOSITORY = 'renyouhao/heroes-storm-helper'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILES = 20000
MAX_RATIO = 200
_CHUNK = 256 * 1024


class UpdateError(Exception):
    """可直接展示给用户的升级错误，不包含请求凭据。"""


class UpdateCancelled(UpdateError):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    parts: tuple

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r'v?\d+(?:\.\d+){0,3}', value, re.ASCII):
            raise UpdateError('发行版本号格式无效，仅支持数字正式版本。')
        numbers = [int(part) for part in value.removeprefix('v').split('.')]
        while len(numbers) > 1 and numbers[-1] == 0:
            numbers.pop()
        return cls(tuple(numbers))

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.parts < other.parts


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str | None


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    asset: ReleaseAsset | None


@dataclass(frozen=True)
class CheckResult:
    current_version: str
    release: ReleaseInfo | None
    available: bool


@dataclass(frozen=True)
class StagedUpdate:
    release: ReleaseInfo
    workspace: Path
    bundle_dir: Path
    archive_path: Path
    files: dict
    ownership_token: str | None = None


def _https_url(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise UpdateError('升级地址格式无效。')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not parsed.hostname or parsed.fragment):
            raise ValueError
        return parsed
    except ValueError as failure:
        raise UpdateError('升级地址必须使用可信 HTTPS 来源。') from failure


def _official_url(value, path):
    parsed = _https_url(value)
    if parsed.hostname != 'github.com' or parsed.query or unquote(parsed.path) != path:
        raise UpdateError('发行来源与官方 GitHub 仓库不一致。')


def _download_location(value, initial):
    parsed = _https_url(value)
    if value == initial:
        return
    if parsed.hostname == 'release-assets.githubusercontent.com' and parsed.path.startswith('/github-production-release-asset/'):
        return
    if parsed.hostname == 'objects.githubusercontent.com' and parsed.path.startswith('/github-production-release-asset-'):
        return
    raise UpdateError('已阻止升级下载跳转到非 GitHub 发行资产来源。')


class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self, initial, download):
        super().__init__()
        self.initial, self.download = initial, download

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if self.download:
            _download_location(newurl, self.initial)
        elif newurl != self.initial:
            raise UpdateError('已阻止官方发行接口跳转。')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _open(url, *, opener=None, download=False):
    # GitHub assets may contain Chinese names; urllib's HTTP transport needs ASCII.
    url = quote(url, safe=':/?&=%#')
    headers = {'User-Agent': 'heroes-storm-helper/' + __version__,
               'Accept': 'application/octet-stream' if download else 'application/vnd.github+json'}
    if not download:
        headers['X-GitHub-Api-Version'] = '2022-11-28'
    request = Request(url, headers=headers)
    transport = opener or build_opener(_SafeRedirect(url, download)).open
    response = transport(request, timeout=30)
    try:
        final = response.geturl()
        if download:
            _download_location(final, url)
        elif final != url:
            raise UpdateError('官方发行接口返回了非预期地址。')
    except BaseException:
        response.close()
        raise
    return response


def _asset_from_payload(payload, tag, version):
    assets = payload.get('assets', [])
    if not isinstance(assets, list):
        raise UpdateError('发行资产列表格式无效。')
    matches = []
    for row in assets:
        if not isinstance(row, dict):
            raise UpdateError('发行资产格式无效。')
        name = row.get('name', '')
        prefixes = '(?:' + '|'.join(map(re.escape, (RELEASE_ARCHIVE_PREFIX, APP_NAME))) + ')'
        match = re.fullmatch(prefixes + r'-(v?\d+(?:\.\d+){0,3})-Windows-x64\.zip', name) if isinstance(name, str) else None
        if not match or Version.parse(match[1]) != Version.parse(version):
            continue
        _official_url(row.get('browser_download_url'), f'/{REPOSITORY}/releases/download/{tag}/{name}')
        size = row.get('size')
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise UpdateError('升级包大小无效或超过限制。')
        digest = row.get('digest')
        if digest is None:
            sha256 = None
        elif isinstance(digest, str) and re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
            sha256 = digest.split(':', 1)[1].lower()
        else:
            raise UpdateError('官方发行资产 SHA256 格式无效。')
        matches.append(ReleaseAsset(name, row['browser_download_url'], size, sha256))
    if len(matches) > 1:
        raise UpdateError('发行包含多个匹配的 Windows x64 升级包，请手动检查。')
    return matches[0] if matches else None


def check_for_update(current_version=__version__, *, opener=None):
    current = Version.parse(current_version)
    try:
        with _open(LATEST_API, opener=opener) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise UpdateError('发行信息超过大小限制。')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise UpdateError('官方发行信息格式无效。')
        if payload.get('draft') is not False or payload.get('prerelease') is not False:
            return CheckResult(current_version, None, False)
        tag = payload.get('tag_name')
        latest = Version.parse(tag)
        _official_url(payload.get('html_url'), f'/{REPOSITORY}/releases/tag/{tag}')
        notes = payload.get('body') or ''
        if not isinstance(notes, str):
            raise UpdateError('更新说明格式无效。')
        release = ReleaseInfo(tag.removeprefix('v'), tag, notes[:100000], payload['html_url'],
                              _asset_from_payload(payload, tag, tag.removeprefix('v')))
        return CheckResult(current_version, release, latest > current)
    except HTTPError as failure:
        if failure.code == 404:
            return CheckResult(current_version, None, False)
        raise UpdateError('检查更新失败，GitHub 暂时无法访问或请求受限，请稍后重试。') from failure
    except (URLError, OSError, UnicodeError, json.JSONDecodeError) as failure:
        raise UpdateError('检查更新失败，请检查网络连接后重试。') from failure


def _cancelled(cancel):
    return bool(cancel and (cancel() if callable(cancel) else cancel.is_set()))


def _check_cancel(cancel):
    if _cancelled(cancel):
        raise UpdateCancelled('已取消下载更新。')


def _safe_member(info):
    # ZipInfo.filename is already normalized on Windows and truncates NUL bytes.
    name = info.orig_filename
    if '\\' in name or '\x00' in name or name.startswith('/'):
        raise UpdateError('升级包包含不安全的文件路径。')
    parts = name.rstrip('/').split('/')
    if not parts or any(not part or part in ('.', '..') or ':' in part or part.endswith(('.', ' '))
                        or re.fullmatch(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', part, re.I)
                        or any(ord(char) < 32 or char in '<>"|?*' for char in part) for part in parts):
        raise UpdateError('升级包包含 Windows 不安全文件名。')
    if parts[0] != APP_NAME:
        raise UpdateError('升级包目录与助手发行结构不一致。')
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
        raise UpdateError('升级包包含链接、特殊文件或加密成员。')
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise UpdateError('升级包使用了不支持的压缩算法。')
    return PurePosixPath(*parts)


def validate_bundle(bundle):
    """发行目录必须有 x64 PE 入口和实际运行库；不执行其中任何代码。"""
    bundle = Path(bundle)
    entry = bundle / (APP_NAME + '.exe')
    runtime = bundle / '_internal'
    if not entry.is_file() or not runtime.is_dir() or not any(path.is_file() for path in runtime.rglob('*')):
        raise UpdateError('升级包缺少助手程序或 _internal 完整运行库。')
    try:
        with entry.open('rb') as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b'MZ':
                raise ValueError
            offset = struct.unpack_from('<I', header, 60)[0]
            if not 64 <= offset <= min(entry.stat().st_size - 6, 1024 * 1024):
                raise ValueError
            stream.seek(offset)
            if stream.read(6) != b'PE\0\0\x64\x86':
                raise ValueError
    except (OSError, ValueError, struct.error) as failure:
        raise UpdateError('升级包程序不是有效的 Windows x64 入口。') from failure


def _extract(archive_path, destination, cancel):
    files = {}
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise UpdateError('升级包文件数量超出限制。')
        seen, total = set(), 0
        for info in infos:
            member = _safe_member(info)
            key = str(member).casefold()
            if key in seen:
                raise UpdateError('升级包包含重复文件路径。')
            seen.add(key)
            total += info.file_size
            if (info.file_size > MAX_ARCHIVE_BYTES or total > MAX_UNPACKED_BYTES
                    or info.file_size > max(1, info.compress_size) * MAX_RATIO):
                raise UpdateError('升级包解压大小或压缩比超出安全限制。')
        for info in infos:
            _check_cancel(cancel)
            member = _safe_member(info)
            target = destination.joinpath(*member.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, count = hashlib.sha256(), 0
            with archive.open(info) as source, target.open('xb') as sink:
                while chunk := source.read(_CHUNK):
                    _check_cancel(cancel)
                    count += len(chunk)
                    if count > info.file_size:
                        raise UpdateError('升级包成员大小与声明不符。')
                    digest.update(chunk)
                    sink.write(chunk)
            if count != info.file_size:
                raise UpdateError('升级包成员不完整。')
            files[PurePosixPath(*member.parts[1:]).as_posix()] = digest.hexdigest()
    bundle = destination / APP_NAME
    validate_bundle(bundle)
    return bundle, files


def download_update(release, *, staging_root=None, opener=None, cancel=None, progress=None):
    if not isinstance(release, ReleaseInfo) or release.asset is None:
        raise UpdateError('此发行没有匹配的完整 Windows x64 包，请前往官方发行页手动下载。')
    asset = release.asset
    Version.parse(release.tag)
    _official_url(release.html_url, f'/{REPOSITORY}/releases/tag/{release.tag}')
    _official_url(asset.url, f'/{REPOSITORY}/releases/download/{release.tag}/{asset.name}')
    if not asset.sha256 or not re.fullmatch(r'[a-f0-9]{64}', asset.sha256):
        raise UpdateError('官方发行未提供可信 SHA256，已停止自动安装；请前往官方发行页手动下载。')
    if type(asset.size) is not int or not 0 < asset.size <= MAX_ARCHIVE_BYTES:
        raise UpdateError('升级包大小超出限制。')
    _check_cancel(cancel)
    root = Path(staging_root).resolve() if staging_root is not None else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='hots-update-', dir=root)).resolve()
    archive_path = workspace / 'package.zip'
    try:
        digest, count = hashlib.sha256(), 0
        with _open(asset.url, opener=opener, download=True) as source, archive_path.open('xb') as sink:
            while chunk := source.read(_CHUNK):
                _check_cancel(cancel)
                count += len(chunk)
                if count > asset.size or count > MAX_ARCHIVE_BYTES:
                    raise UpdateError('下载大小超过官方发行声明。')
                digest.update(chunk)
                sink.write(chunk)
                if progress:
                    progress(count, asset.size)
        _check_cancel(cancel)
        if count != asset.size or digest.hexdigest() != asset.sha256:
            raise UpdateError('升级包大小或 SHA256 校验失败，已停止安装。')
        bundle, files = _extract(archive_path, workspace / 'unpacked', cancel)
        ownership_token = secrets.token_hex(32)
        marker = {'schema': 1, 'kind': 'hots-helper-stage', 'state': 'validated',
                  'token': ownership_token, 'root': str(workspace.parent), 'workspace': workspace.name}
        (workspace / '.stage-owner.json').write_text(json.dumps(marker), encoding='utf-8')
        return StagedUpdate(release, workspace, bundle, archive_path, files, ownership_token)
    except (URLError, OSError, zipfile.BadZipFile, RuntimeError, EOFError) as failure:
        raise UpdateError('升级下载或解压失败，请检查网络、磁盘空间及文件权限。') from failure
    finally:
        # A returned stage is retained. Every failed/cancelled attempt owns only this fresh directory.
        import sys
        if sys.exc_info()[0] is not None:
            shutil.rmtree(workspace)


def _plain_metadata(path):
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
        raise UpdateError('拒绝清理包含链接或目录联接的升级目录。')
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise UpdateError('拒绝清理包含特殊文件的升级目录。')
    return metadata


def _validate_plain_tree(directory, budget=None, depth=0):
    budget = [MAX_FILES * 2] if budget is None else budget
    if depth > 64:
        raise UpdateError('升级清理目录层级超出限制。')
    _plain_metadata(directory)
    for child in directory.iterdir():
        budget[0] -= 1
        if budget[0] < 0:
            raise UpdateError('升级清理文件数量超出限制。')
        metadata = _plain_metadata(child)
        if stat.S_ISDIR(metadata.st_mode):
            _validate_plain_tree(child, budget, depth + 1)


def _remove_plain_tree(directory, budget=None, depth=0):
    # Recheck each component immediately before mutation; do not delegate to a
    # recursive shell command or follow Windows reparse points.
    _plain_metadata(directory)
    budget = [MAX_FILES * 2] if budget is None else budget
    if depth > 64:
        raise UpdateError('升级清理目录层级超出限制。')
    children = list(directory.iterdir())
    # Preserve the ownership proof until the final step if exit interrupts cleanup.
    children.sort(key=lambda path: path.name == '.stage-owner.json')
    for child in children:
        budget[0] -= 1
        if budget[0] < 0:
            raise UpdateError('升级清理文件数量超出限制。')
        metadata = _plain_metadata(child)
        if stat.S_ISDIR(metadata.st_mode):
            _remove_plain_tree(child, budget, depth + 1)
        else:
            child.unlink()
    directory.rmdir()


def discard_update(staged, *, staging_root=None):
    """只回收本次下载拥有的直接子暂存目录；无法证明边界时保留文件。"""
    if not isinstance(staged, StagedUpdate):
        return False
    token = staged.ownership_token
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        return False
    try:
        root = Path(staging_root if staging_root is not None else tempfile.gettempdir())
        workspace = Path(staged.workspace)
        if not root.is_absolute() or not workspace.is_absolute():
            return False
        # Validate lexical ancestors before resolving, which would hide a link.
        for path in (root, *root.parents, workspace):
            _plain_metadata(path)
        root = root.resolve()
        if (workspace.parent != root or not re.fullmatch(r'hots-update-[a-z0-9_]{8,}', workspace.name)
                or workspace.resolve() != workspace or Path(staged.archive_path) != workspace / 'package.zip'
                or Path(staged.bundle_dir) != workspace / 'unpacked' / APP_NAME):
            return False
        if any((workspace / name).exists() for name in ('helper', 'install-plan.json', 'helper.lock', 'helper.ready')):
            return False
        marker = workspace / '.stage-owner.json'
        _plain_metadata(marker)
        if marker.stat().st_size > 4096:
            return False
        proof = json.loads(marker.read_text(encoding='utf-8'))
        expected = {'schema': 1, 'kind': 'hots-helper-stage', 'state': 'validated',
                    'token': token, 'root': str(root), 'workspace': workspace.name}
        if proof != expected:
            return False
        _validate_plain_tree(workspace)
        _remove_plain_tree(workspace)
        return True
    except (OSError, ValueError, TypeError, UpdateError, RecursionError):
        return False
