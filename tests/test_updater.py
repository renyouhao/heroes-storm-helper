import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from urllib.request import Request
import zipfile

from hots_chat import APP_NAME
from hots_chat.updater import (
    CheckResult, ReleaseAsset, ReleaseInfo, UpdateCancelled, UpdateError, Version,
    check_for_update, download_update,
)
from hots_chat import updater


def exe_fixture():
    data = bytearray(160)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:134] = b'PE\0\0\x64\x86'
    return bytes(data)


def archive_fixture(extra=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(APP_NAME + '/' + APP_NAME + '.exe', exe_fixture())
        archive.writestr(APP_NAME + '/_internal/python313.dll', b'inert runtime fixture')
        for name, data in extra:
            archive.writestr(name, data)
    return stream.getvalue()


def release_payload(data=None):
    data = archive_fixture() if data is None else data
    name = APP_NAME + '-1.2.0-Windows-x64.zip'
    return {
        'tag_name': 'v1.2.0', 'draft': False, 'prerelease': False,
        'html_url': 'https://github.com/renyouhao/heroes-storm-helper/releases/tag/v1.2.0',
        'body': '更新说明',
        'assets': [{'name': name, 'size': len(data),
                    'browser_download_url': 'https://github.com/renyouhao/heroes-storm-helper/releases/download/v1.2.0/' + name,
                    'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}],
    }


class Response(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def check_fixture(payload):
    return check_for_update('1.1.0', opener=lambda request, **kw: Response(
        json.dumps(payload, ensure_ascii=False).encode(), request.full_url))


class VersionTests(unittest.TestCase):
    def test_numeric_versions_compare_without_lexical_errors(self):
        self.assertEqual(Version.parse('v1.1'), Version.parse('1.1.0'))
        self.assertGreater(Version.parse('1.10'), Version.parse('1.9.9'))
        self.assertLess(Version.parse('v1.0'), Version.parse('1.1.0'))

    def test_invalid_and_prerelease_versions_are_rejected(self):
        for value in ('1.2-beta', 'latest', 'v1..2', '1.-2', '', '1.2.3+dev', '1' * 5000):
            with self.subTest(value=value), self.assertRaises(UpdateError):
                Version.parse(value)


class ReleaseTests(unittest.TestCase):
    def test_ascii_release_asset_is_selected_with_its_official_url_and_digest(self):
        payload = release_payload()
        asset = payload['assets'][0]
        asset['name'] = 'HotsHelper-1.2.0-Windows-x64.zip'
        asset['browser_download_url'] = 'https://github.com/renyouhao/heroes-storm-helper/releases/download/v1.2.0/' + asset['name']
        selected = check_fixture(payload).release.asset
        self.assertIsNotNone(selected, 'GitHub removes Chinese characters from uploaded asset names')
        self.assertEqual(selected.name, asset['name'])
        self.assertEqual(selected.url, asset['browser_download_url'])
        self.assertEqual(selected.sha256, asset['digest'].split(':')[1])

    def test_ascii_and_legacy_names_cannot_select_two_equivalent_packages(self):
        payload = release_payload()
        asset = dict(payload['assets'][0])
        asset['name'] = 'HotsHelper-1.2.0-Windows-x64.zip'
        asset['browser_download_url'] = 'https://github.com/renyouhao/heroes-storm-helper/releases/download/v1.2.0/' + asset['name']
        payload['assets'].append(asset)
        with self.assertRaises(UpdateError):
            check_fixture(payload)

    def test_official_stable_release_supplies_notes_asset_and_availability(self):
        result = check_fixture(release_payload())
        self.assertIsInstance(result, CheckResult)
        self.assertTrue(result.available)
        self.assertEqual(result.release.version, '1.2.0')
        self.assertEqual(result.release.notes, '更新说明')
        self.assertIsNotNone(result.release.asset.sha256)

    def test_old_and_equal_versions_do_not_offer_downgrades(self):
        for tag in ('v1.0', 'v1.1', 'v1.1.0'):
            payload = release_payload()
            payload['tag_name'] = tag
            payload['html_url'] = 'https://github.com/renyouhao/heroes-storm-helper/releases/tag/' + tag
            payload['assets'] = []
            self.assertFalse(check_fixture(payload).available)

    def test_draft_or_prerelease_is_not_an_update(self):
        for flag in ('draft', 'prerelease'):
            payload = release_payload()
            payload[flag] = True
            self.assertIsNone(check_fixture(payload).release)

    def test_missing_digest_is_reported_without_inventing_trust(self):
        payload = release_payload()
        payload['assets'][0]['digest'] = None
        result = check_fixture(payload)
        self.assertTrue(result.available)
        self.assertIsNone(result.release.asset.sha256)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(UpdateError):
            download_update(result.release, staging_root=Path(tmp), opener=lambda *a, **kw: self.fail('must not download'))

    def test_unrelated_release_and_asset_urls_are_rejected(self):
        for field, bad in (
            ('html_url', 'https://github.com/evil/heroes-storm-helper/releases/tag/v1.2.0'),
            ('browser_download_url', 'https://github.com/evil/heroes-storm-helper/releases/download/v1.2.0/anything.zip'),
            ('browser_download_url', 'http://github.com/renyouhao/heroes-storm-helper/releases/download/v1.2.0/a.zip'),
            ('browser_download_url', 'https://github.com.evil.test/renyouhao/heroes-storm-helper/releases/download/v1.2.0/a.zip'),
        ):
            payload = release_payload()
            (payload if field == 'html_url' else payload['assets'][0])[field] = bad
            with self.subTest(bad=bad), self.assertRaises(UpdateError):
                check_fixture(payload)

    def test_wrong_or_ambiguous_package_is_not_selected(self):
        payload = release_payload()
        payload['assets'][0]['name'] = 'another-app-1.2.0-Windows-x64.zip'
        self.assertIsNone(check_fixture(payload).release.asset)
        payload = release_payload()
        payload['assets'].append(dict(payload['assets'][0]))
        with self.assertRaises(UpdateError):
            check_fixture(payload)


class DownloadTests(unittest.TestCase):
    def test_ascii_release_asset_downloads_and_validates_the_chinese_bundle(self):
        data = archive_fixture()
        payload = release_payload(data)
        asset = payload['assets'][0]
        asset['name'] = 'HotsHelper-1.2.0-Windows-x64.zip'
        asset['browser_download_url'] = 'https://github.com/renyouhao/heroes-storm-helper/releases/download/v1.2.0/' + asset['name']
        release = check_fixture(payload).release
        self.assertIsNotNone(release.asset)
        with tempfile.TemporaryDirectory() as tmp:
            staged = download_update(release, staging_root=Path(tmp),
                opener=lambda request, **kw: Response(data, request.full_url))
            self.assertEqual(staged.archive_path.read_bytes(), data)
            self.assertEqual((staged.bundle_dir / (APP_NAME + '.exe')).read_bytes(), exe_fixture())

    def download(self, tmp, data, *, payload=None, **kwargs):
        release = check_fixture(payload or release_payload(data)).release
        return download_update(release, staging_root=Path(tmp),
                               opener=lambda request, **kw: Response(data, request.full_url), **kwargs)

    def test_download_verifies_and_stages_complete_bundle_without_installing(self):
        data = archive_fixture()
        progress = []
        with tempfile.TemporaryDirectory() as tmp:
            staged = self.download(tmp, data, progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(staged.archive_path.read_bytes(), data)
            self.assertEqual((staged.bundle_dir / (APP_NAME + '.exe')).read_bytes(), exe_fixture())
            self.assertTrue((staged.bundle_dir / '_internal/python313.dll').is_file())
            self.assertEqual(progress[-1], (len(data), len(data)))
            self.assertIn(APP_NAME + '.exe', staged.files)

    def test_bad_digest_or_size_leaves_no_staged_files(self):
        data = archive_fixture()
        for field, value in (('digest', 'sha256:' + '0' * 64), ('size', len(data) + 1)):
            payload = release_payload(data)
            payload['assets'][0][field] = value
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(UpdateError):
                    self.download(tmp, data, payload=payload)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cancellation_cleans_only_its_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / 'existing.txt'
            sentinel.write_text('keep')
            with self.assertRaises(UpdateCancelled):
                self.download(tmp, archive_fixture(), cancel=lambda: True)
            self.assertEqual(sentinel.read_text(), 'keep')
            self.assertEqual(list(Path(tmp).iterdir()), [sentinel])

    def test_unsafe_archive_paths_are_rejected_without_writing_outside_workspace(self):
        for name in ('../escape.txt', '/absolute.txt', 'C:/outside.txt',
                     APP_NAME + '/_internal/../../escape.txt', APP_NAME + '/x:stream',
                     APP_NAME + '/CON.txt', APP_NAME + '/trailing. ',
                     APP_NAME + '/_internal\\escape.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                data = archive_fixture(((name, b'bad'),))
                if '\\' in name:
                    # Windows' zip writer normalizes separators; restore the malicious raw ZIP name.
                    data = data.replace(name.replace('\\', '/').encode(), name.encode())
                with self.assertRaises(UpdateError):
                    self.download(tmp, data)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_symlink_and_duplicate_case_insensitive_members_are_rejected(self):
        link = zipfile.ZipInfo(APP_NAME + '/link')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        for entries in (((link, b'outside'),), ((APP_NAME + '/' + APP_NAME.upper() + '.EXE', b'bad'),)):
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(UpdateError):
                self.download(tmp, archive_fixture(entries))

    def test_missing_runtime_wrong_architecture_and_zip_bomb_are_rejected(self):
        for member, content in ((APP_NAME + '/' + APP_NAME + '.exe', b'not x64 PE'),
                                (APP_NAME + '/_internal/bomb', b'0' * 2_000_000)):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(member, content)
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(UpdateError):
                self.download(tmp, stream.getvalue())

    def test_download_cannot_redirect_to_untrusted_host(self):
        data = archive_fixture()
        release = check_fixture(release_payload(data)).release
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(UpdateError):
            download_update(release, staging_root=Path(tmp), opener=lambda *args, **kw: Response(data, 'https://evil.test/file.zip'))

    def test_reserved_windows_device_names_are_rejected_before_any_file_open(self):
        for name in ('COM¹.txt', 'LPT²', 'CONOUT$', 'CONIN$'):
            with self.subTest(name=name), self.assertRaises(UpdateError):
                updater._safe_member(zipfile.ZipInfo(APP_NAME + '/' + name))

    def test_nonstandard_compression_is_rejected_before_extraction(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_LZMA) as archive:
            archive.writestr(APP_NAME + '/' + APP_NAME + '.exe', exe_fixture())
            archive.writestr(APP_NAME + '/_internal/python313.dll', b'inert fixture')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(UpdateError):
            self.download(tmp, stream.getvalue())

    def test_redirect_chain_only_accepts_github_signed_asset_hosts(self):
        initial = release_payload()['assets'][0]['browser_download_url']
        redirect = updater._SafeRedirect(initial, True)
        request = Request(initial)
        trusted = 'https://release-assets.githubusercontent.com/github-production-release-asset/123/uuid?signature=fixture'
        self.assertEqual(redirect.redirect_request(request, None, 302, 'Found', {}, trusted).full_url, trusted)
        for location in ('http://release-assets.githubusercontent.com/github-production-release-asset/123/u',
                         'https://evil.test/file', 'https://github.com/evil/repo/releases/download/v1/a.zip',
                         'https://user:pass@release-assets.githubusercontent.com/github-production-release-asset/123/u'):
            with self.subTest(location=location), self.assertRaises(UpdateError):
                redirect.redirect_request(request, None, 302, 'Found', {}, location)

    def test_real_transport_encodes_chinese_asset_name_as_ascii(self):
        observed = []
        initial = release_payload()['assets'][0]['browser_download_url']

        def transport(request, **kwargs):
            observed.append(request.full_url)
            request.full_url.encode('ascii')
            return Response(b'', request.full_url)

        class Transport:
            open = staticmethod(transport)

        with patch.object(updater, 'build_opener', return_value=Transport()):
            with updater._open(initial, download=True):
                pass
        self.assertIn('%E9%A3%8E', observed[0])

    def test_cancel_during_extraction_removes_partial_bundle(self):
        count = 0

        def cancel():
            nonlocal count
            count += 1
            return count >= 5

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UpdateCancelled):
                self.download(tmp, archive_fixture(), cancel=cancel)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_owned_stage_can_only_be_discarded_under_its_actual_staging_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = self.download(tmp, archive_fixture())
            unrelated = Path(tmp) / 'keep.txt'
            unrelated.write_text('keep')
            self.assertFalse(updater.discard_update(stage, staging_root=Path(tmp) / 'different'))
            self.assertTrue(stage.workspace.exists())
            self.assertTrue(updater.discard_update(stage, staging_root=Path(tmp)))
            self.assertFalse(stage.workspace.exists())
            self.assertEqual(unrelated.read_text(), 'keep')

    def test_discard_rejects_foreign_objects_tokens_and_installer_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = self.download(tmp, archive_fixture())
            self.assertFalse(updater.discard_update(object(), staging_root=Path(tmp)))
            self.assertFalse(updater.discard_update(replace(stage, ownership_token='0' * 64), staging_root=Path(tmp)))
            for marker in ('helper', 'install-plan.json', 'helper.lock'):
                path = stage.workspace / marker
                path.write_text('inert handoff fixture')
                self.assertFalse(updater.discard_update(stage, staging_root=Path(tmp)))
                self.assertTrue(stage.archive_path.exists())
                path.unlink()
            self.assertTrue(updater.discard_update(stage, staging_root=Path(tmp)))

    def test_discard_never_follows_symlink_or_reparse_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = self.download(tmp, archive_fixture())
            original_lstat = Path.lstat
            protected = stage.bundle_dir / '_internal/python313.dll'

            def reparse_metadata(path):
                metadata = original_lstat(path)
                if path == protected:
                    from types import SimpleNamespace
                    return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
                return metadata

            with patch.object(Path, 'lstat', reparse_metadata):
                self.assertFalse(updater.discard_update(stage, staging_root=Path(tmp)))
            self.assertTrue(protected.exists())

    def test_discard_rejects_workspace_without_its_ownership_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = self.download(tmp, archive_fixture())
            marker = stage.workspace / '.stage-owner.json'
            marker.unlink()
            self.assertFalse(updater.discard_update(stage, staging_root=Path(tmp)))
            self.assertTrue(stage.archive_path.exists())

    def test_discard_cannot_use_a_valid_token_for_an_unrelated_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = self.download(tmp, archive_fixture())
            unrelated = root / 'hots-update-important'
            unrelated.mkdir()
            keep = unrelated / 'keep.txt'
            keep.write_text('keep')
            (unrelated / '.stage-owner.json').write_bytes((stage.workspace / '.stage-owner.json').read_bytes())
            forged = replace(stage, workspace=unrelated, archive_path=unrelated / 'package.zip',
                             bundle_dir=unrelated / 'unpacked' / APP_NAME)
            self.assertFalse(updater.discard_update(forged, staging_root=root))
            self.assertFalse(updater.discard_update(replace(stage, archive_path=keep), staging_root=root))
            self.assertEqual(keep.read_text(), 'keep')
            self.assertTrue(stage.archive_path.exists())

    def test_discard_validation_has_a_bounded_member_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = self.download(tmp, archive_fixture())
            with patch.object(updater, 'MAX_FILES', 1):
                self.assertFalse(updater.discard_update(stage, staging_root=Path(tmp)))
            self.assertTrue(stage.archive_path.exists())


if __name__ == '__main__':
    unittest.main()
