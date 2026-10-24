from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools import build_release
from tools.build_release import verify_bundle, stage_public_data


class ReleaseTests(unittest.TestCase):
    def run_fixture_build(self,root,output,passed=True):
        (root/'data').mkdir(parents=True,exist_ok=True)
        for name in build_release.PUBLIC_SEEDS:(root/'data'/name).write_text('{}')
        def build_or_check(command,**kwargs):
            if '--distpath' in command:
                bundle=Path(command[command.index('--distpath')+1])/build_release.NAME
                bundle.mkdir(parents=True)
                (bundle/(build_release.NAME+'.exe')).write_bytes(b'inert executable fixture')
            else:
                Path(command[-1]).write_text(json.dumps({'passed':passed}))
        frontend=root/'frontend'/'dist';frontend.mkdir(parents=True)
        (frontend/'index.html').write_text('<div id="root"></div>')
        with patch.object(build_release,'ROOT',root),patch.object(build_release,'build_frontend',return_value=frontend),patch.object(build_release.subprocess,'run',side_effect=build_or_check):
            return build_release.main(['--output',str(output)])

    def old_package(self,output):
        output.mkdir(parents=True,exist_ok=True)
        archive=output/'HotsHelper-0.1.0-Windows-x64.zip'
        archive.write_bytes(b'previous package')
        return archive

    def test_success_removes_previous_packages_in_release_root_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';releases=root/'releases';output=releases/'new'
            old=self.old_package(releases/'old')
            loose=self.old_package(releases)
            previous=self.old_package(output)
            unrelated=releases/'notes.txt';unrelated.write_text('keep')
            self.run_fixture_build(root,output)
            self.assertFalse(old.parent.exists())
            self.assertFalse(loose.exists())
            self.assertFalse(previous.exists())
            self.assertEqual(unrelated.read_text(),'keep')
            self.assertTrue((output/build_release.release_archive_name()).is_file())
            self.assertTrue((output/'发行校验.json').is_file())

    def test_failed_installation_keeps_previous_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases'/'new'
            old=self.old_package(root/'releases'/'old')
            with self.assertRaises(ValueError):self.run_fixture_build(root,output,passed=False)
            self.assertEqual(old.read_bytes(),b'previous package')

    def test_failed_archive_validation_keeps_previous_packages(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases'
            old=self.old_package(output)
            with patch.object(zipfile.ZipFile,'write',return_value=None):
                with self.assertRaises(ValueError):self.run_fixture_build(root,output)
            self.assertEqual(old.read_bytes(),b'previous package')

    def test_default_output_keeps_new_work_and_removes_old_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases'
            old_work=output/'hots-release-old';old_work.mkdir(parents=True)
            (old_work/'old.txt').write_text('old')
            self.run_fixture_build(root,output)
            self.assertFalse(old_work.exists())
            work=list(output.glob('hots-release-*'))
            self.assertEqual(len(work),1)
            self.assertTrue((work[0]/'dist'/build_release.NAME/(build_release.NAME+'.exe')).exists())

    def test_bad_new_zip_does_not_replace_same_version_package(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases';output.mkdir(parents=True)
            old=output/build_release.release_archive_name()
            old.write_bytes(b'previous same version')
            with patch.object(zipfile.ZipFile,'write',return_value=None):
                with self.assertRaises(ValueError):self.run_fixture_build(root,output)
            self.assertEqual(old.read_bytes(),b'previous same version')

    def test_manifest_write_failure_keeps_old_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases'
            old=self.old_package(output)
            original=Path.write_text
            def write(path,*args,**kwargs):
                if path.name=='发行校验.json.tmp':raise OSError('disk full')
                return original(path,*args,**kwargs)
            with patch.object(Path,'write_text',write):
                with self.assertRaises(OSError):self.run_fixture_build(root,output)
            self.assertEqual(old.read_bytes(),b'previous package')

    def test_external_output_preserves_parent_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=Path(tmp)/'external'/'output'
            old=self.old_package(output)
            parent_package=self.old_package(output.parent)
            notes=output/'notes.txt';notes.write_text('keep')
            source=output/'source';source.mkdir();(source/'main.py').write_text('keep')
            self.run_fixture_build(root,output)
            self.assertFalse(old.exists())
            self.assertTrue(parent_package.exists())
            self.assertEqual(notes.read_text(),'keep')
            self.assertEqual((source/'main.py').read_text(),'keep')

    def test_cleanup_skips_directory_with_junction_to_source(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=root/'releases'/'new'
            old=self.old_package(root/'releases'/'old')
            protected=Path(tmp)/'protected';protected.mkdir();(protected/'main.py').write_text('keep')
            link=old.parent/'source-link'
            if os.name=='nt':
                subprocess.run(['cmd','/c','mklink','/J',str(link),str(protected)],check=True,capture_output=True)
            else:link.symlink_to(protected,target_is_directory=True)
            try:
                self.run_fixture_build(root,output)
                self.assertTrue(old.exists())
                self.assertEqual((protected/'main.py').read_text(),'keep')
            finally:
                if os.name=='nt':os.rmdir(link)
                else:link.unlink()

    def test_public_archive_name_is_ascii_while_program_and_bundle_remain_chinese(self):
        import json
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';output=Path(tmp)/'output'
            (root/'data').mkdir(parents=True)
            for name in build_release.PUBLIC_SEEDS:
                (root/'data'/name).write_text('{}')
            def build_or_check(command,**kwargs):
                if '--distpath' in command:
                    bundle=Path(command[command.index('--distpath')+1])/build_release.NAME
                    bundle.mkdir(parents=True)
                    (bundle/(build_release.NAME+'.exe')).write_bytes(b'inert executable fixture')
                else:
                    self.assertTrue(any(flag in command for flag in ('--check-installation','--check-desktop-installation','--check-desktop-rendering')))
                    Path(command[-1]).write_text(json.dumps({'passed':True}))
            frontend=root/'frontend'/'dist';frontend.mkdir(parents=True)
            (frontend/'index.html').write_text('<div id="root"></div>')
            with patch.object(build_release,'ROOT',root),patch.object(build_release,'build_frontend',return_value=frontend),patch.object(build_release.subprocess,'run',side_effect=build_or_check):
                self.assertEqual(build_release.main(['--output',str(output)]),0)
            name=build_release.release_archive_name()
            self.assertTrue(name.isascii())
            self.assertTrue((output/name).is_file(), 'GitHub cannot preserve the Chinese public archive name')
            manifest=json.loads((output/'发行校验.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['version'],build_release.VERSION)
            self.assertEqual(manifest['channel'],build_release.RELEASE_CHANNEL)
            self.assertEqual(manifest['display_version'],build_release.DISPLAY_VERSION)
            with zipfile.ZipFile(output/name) as archive:
                self.assertIn('风暴英雄助手/风暴英雄助手.exe',archive.namelist())

    def test_staging_only_copies_public_seed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';dest=Path(tmp)/'stage'
            (root/'data').mkdir(parents=True)
            (root/'data'/'phrases.json').write_text('[]')
            for name in build_release.PUBLIC_SEEDS[1:]:(root/'data'/name).write_text('{}')
            (root/'data'/'terms.json').write_text('[]')
            (root/'.env').write_text('private fixture')
            (root/'data'/'private.db').write_bytes(b'private fixture')
            stage_public_data(root,dest)
            self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file()),
                             sorted('data/'+name for name in build_release.PUBLIC_SEEDS))

    def test_staging_does_not_require_retired_term_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';dest=Path(tmp)/'stage'
            (root/'data').mkdir(parents=True)
            (root/'data'/'phrases.json').write_text('[{"source":"hello"}]')
            for name in build_release.PUBLIC_SEEDS[1:]:(root/'data'/name).write_text('{}')
            stage_public_data(root,dest)
            self.assertEqual((dest/'data'/'phrases.json').read_text(),'[{"source":"hello"}]')

    def test_build_command_only_bundles_explicit_public_seeds(self):
        class BuildCommandCaptured(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project';output=Path(tmp)/'releases'
            (root/'data').mkdir(parents=True)
            (root/'data'/'phrases.json').write_text('[]')
            for name in build_release.PUBLIC_SEEDS[1:]:(root/'data'/name).write_text('{}')
            (root/'data'/'terms.json').write_text('[]')
            commands=[]

            def capture_build(command,**kwargs):
                commands.append(command)
                raise BuildCommandCaptured

            frontend=root/'frontend'/'dist';frontend.mkdir(parents=True)
            (frontend/'index.html').write_text('<div id="root"></div>')
            with patch.object(build_release,'ROOT',root),patch.object(build_release,'build_frontend',return_value=frontend),patch.object(build_release.subprocess,'run',side_effect=capture_build):
                with self.assertRaises(BuildCommandCaptured):build_release.main(['--output',str(output)])
            command=commands[0]
            self.assertIn('--icon',command)
            self.assertEqual(Path(command[command.index('--icon')+1]),root/'data'/'assistant.ico')
            resources=[command[index+1] for index,value in enumerate(command) if value=='--add-data']
            self.assertEqual(len(resources),len(build_release.PUBLIC_SEEDS)+1)
            for resource,name in zip(resources,build_release.PUBLIC_SEEDS):
                source,destination=resource.rsplit(build_release.os.pathsep,1)
                self.assertEqual(Path(source).name,name)
                self.assertEqual(destination,'data')
            self.assertEqual(resources[-1],str(frontend)+build_release.os.pathsep+'frontend/dist')

    def test_bundle_refuses_local_credentials_and_usage_state(self):
        for filename in ('.env','.env.backup','credentials.dpapi','usage.sqlite3','usage.sqlite3.initialized','private.log'):
            with self.subTest(filename=filename),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'风暴英雄助手.exe').write_bytes(b'fixture')
                (root/filename).write_bytes(b'private fixture')
                with self.assertRaises(ValueError):verify_bundle(root)

    def test_bundle_manifest_hashes_only_allowed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'风暴英雄助手.exe').write_bytes(b'abc')
            files=verify_bundle(root)
            self.assertEqual(len(files),1)
            self.assertEqual(files[0]['path'],'风暴英雄助手.exe')
            self.assertEqual(files[0]['sha256'],'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
