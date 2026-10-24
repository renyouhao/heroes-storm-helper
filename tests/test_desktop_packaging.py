"""Desktop build contract: fresh resources, Windows backend, all self-checks."""
from pathlib import Path
import json
import re
import tempfile
import unittest
from unittest.mock import patch

from tools import build_release


class DesktopPackagingTests(unittest.TestCase):
    def test_release_version_matches_python_metadata_frontend_and_lockfile(self):
        from hots_chat import __version__, DISPLAY_VERSION, RELEASE_CHANNEL
        root = Path(__file__).resolve().parents[1]
        metadata = (root / 'pyproject.toml').read_text(encoding='utf-8')
        declared_version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', metadata)
        self.assertIsNotNone(declared_version)
        self.assertEqual(declared_version.group(1), __version__)
        frontend = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8'))
        lockfile = json.loads((root / 'frontend/package-lock.json').read_text(encoding='utf-8'))
        self.assertEqual(frontend['version'], __version__)
        self.assertEqual(lockfile['version'], __version__)
        self.assertEqual(lockfile['packages']['']['version'], __version__)
        self.assertEqual(build_release.VERSION, __version__)
        self.assertEqual(DISPLAY_VERSION, 'v' + '.'.join(__version__.split('.')[:2]))
        self.assertEqual(RELEASE_CHANNEL, 'stable')

    def test_prerelease_name_is_separate_from_numeric_version_and_stable_name(self):
        self.assertEqual(build_release.release_archive_name('1.4.0','prerelease'),
                         'HotsHelper-1.4.0-prerelease-Windows-x64.zip')
        self.assertEqual(build_release.release_archive_name('1.2.0','stable'),
                         'HotsHelper-1.2.0-Windows-x64.zip')
        self.assertTrue(build_release._archive_name('HotsHelper-1.4.0-prerelease-Windows-x64.zip'))

    def test_demo_launcher_uses_only_root_exe_and_demo_flag_with_ascii_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle=Path(tmp)
            note=bundle/'使用说明.txt';note.write_text('应用标题\n\n原说明\n',encoding='utf-8-sig')
            build_release.stage_demo_launcher(bundle)
            script=(bundle/'查看界面效果.cmd').read_bytes().decode('ascii')
            self.assertIn('for %%f in ("%~dp0*.exe")',script)
            self.assertIn('start "" "%%~ff" --demo',script)
            self.assertNotIn('_internal',script)
            self.assertNotIn('/r',script.lower())
            self.assertIn('普通使用双击“风暴英雄助手.exe”',note.read_text(encoding='utf-8-sig'))
            build_release.stage_demo_launcher(bundle)
            self.assertEqual(note.read_text(encoding='utf-8-sig').count('查看界面效果：'),1)

    def test_only_shared_purple_icon_is_staged_without_legacy_icon_or_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';data=root/'data';data.mkdir(parents=True)
            for name in build_release.PUBLIC_SEEDS:(data/name).write_bytes(name.encode('ascii'))
            self.assertNotIn('hots.ico',build_release.PUBLIC_SEEDS)
            self.assertIn('assistant.ico',build_release.PUBLIC_SEEDS)
            (data/'hots.ico').write_bytes(b'legacy blue icon must stay source-only')
            (data/'assistant-mark.svg').write_text('<svg/>')
            destination=Path(tmp)/'staged'
            build_release.stage_public_data(root,destination)
            self.assertFalse((destination/'data'/'hots.ico').exists())
            self.assertEqual((destination/'data'/'assistant.ico').read_bytes(),b'assistant.ico')
            self.assertFalse((destination/'data'/'assistant-mark.svg').exists())

    def test_frontend_build_uses_lockfile_then_checks_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);frontend=root/'frontend';frontend.mkdir()
            (frontend/'package-lock.json').write_text('{}')
            commands=[]
            def run(command,**kwargs):
                commands.append((command,kwargs))
                if command[-2:]==['run','build']:
                    (frontend/'dist'/'assets').mkdir(parents=True)
                    (frontend/'dist'/'index.html').write_text('<div id="root"></div>')
                    (frontend/'dist'/'assets'/'main.js').write_text('void 0;')
            with patch.object(build_release.shutil,'which',return_value='npm.cmd'),patch.object(build_release.subprocess,'run',side_effect=run):
                self.assertEqual(build_release.build_frontend(root),frontend/'dist')
            self.assertEqual([item[0] for item in commands],[['npm.cmd','ci'],['npm.cmd','run','build']])
            self.assertTrue(all(item[1]=={'cwd':frontend,'check':True} for item in commands))

    def test_missing_lockfile_or_incomplete_dist_blocks_freezing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'package-lock'):
                build_release.build_frontend(root)
            (root/'frontend').mkdir();(root/'frontend'/'package-lock.json').write_text('{}')
            with patch.object(build_release.shutil,'which',return_value='npm.cmd'),patch.object(build_release.subprocess,'run'):
                with self.assertRaisesRegex(ValueError,'index.html'):
                    build_release.build_frontend(root)

    def test_windows_backend_bridges_are_bundled_without_qt_or_cef(self):
        options=build_release.desktop_freeze_options(Path('frontend/dist'))
        pairs=list(zip(options[::2],options[1::2]))
        self.assertIn(('--hidden-import','webview.platforms.edgechromium'),pairs)
        self.assertIn(('--hidden-import','webview.platforms.winforms'),pairs)
        for package in ('webview','pythonnet','clr_loader'):
            self.assertIn(('--collect-data',package),pairs)
            self.assertIn(('--collect-binaries',package),pairs)
        for package in ('PyQt5','PyQt6','PySide2','PySide6','cefpython3'):
            self.assertIn(('--exclude-module',package),pairs)
        self.assertNotIn('--collect-all',options)

    def test_desktop_check_failure_preserves_previous_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'releases';output.mkdir()
            previous=output/'HotsHelper-0.1.0-Windows-x64.zip';previous.write_bytes(b'keep')
            (root/'data').mkdir()
            for seed in build_release.PUBLIC_SEEDS:(root/'data'/seed).write_text('{}')
            checks=[]
            def run(command,**kwargs):
                if '--distpath' in command:
                    bundle=Path(command[command.index('--distpath')+1])/build_release.NAME
                    bundle.mkdir(parents=True);(bundle/(build_release.NAME+'.exe')).write_bytes(b'fixture')
                else:
                    checks.append(command[1])
                    Path(command[-1]).write_text(json.dumps({'passed':command[1]=='--check-installation'}))
            with patch.object(build_release,'ROOT',root),patch.object(build_release,'build_frontend',return_value=root/'frontend'/'dist'),patch.object(build_release.subprocess,'run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'桌面'):
                    build_release.main(['--output',str(output)])
            self.assertEqual(checks,['--check-installation','--check-desktop-installation'])
            self.assertEqual(previous.read_bytes(),b'keep')

    def test_source_launcher_supports_demo_and_legacy_without_node_at_launch(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'启动助手.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('--legacy-ui',script)
        self.assertIn('--demo',script)
        self.assertIn('frontend/dist/index.html',script)
        self.assertNotIn('& npm',script)

    def test_rendering_failure_blocks_zip_and_keeps_previous_verified_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'output';output.mkdir()
            previous=output/'HotsHelper-0.1.0-Windows-x64.zip';previous.write_bytes(b'verified old package')
            (root/'data').mkdir()
            for seed in build_release.PUBLIC_SEEDS:(root/'data'/seed).write_text('{}')
            checks=[]
            def run(command,**kwargs):
                if '--distpath' in command:
                    bundle=Path(command[command.index('--distpath')+1])/build_release.NAME
                    bundle.mkdir(parents=True);(bundle/(build_release.NAME+'.exe')).write_bytes(b'fixture')
                else:
                    checks.append(command[1])
                    Path(command[-1]).write_text(json.dumps({'passed':command[1]!='--check-desktop-rendering'}))
            with patch.object(build_release,'ROOT',root),patch.object(build_release,'build_frontend',return_value=root/'frontend'/'dist'),patch.object(build_release.subprocess,'run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'实际渲染'):
                    build_release.main(['--output',str(output)])
            self.assertEqual(checks,['--check-installation','--check-desktop-installation','--check-desktop-rendering'])
            self.assertEqual(previous.read_bytes(),b'verified old package')
            self.assertEqual(list(output.glob('HotsHelper-*.zip')),[previous])


if __name__=='__main__':unittest.main()
