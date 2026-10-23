from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import build_release
from tools.build_release import verify_bundle, stage_public_data


class ReleaseTests(unittest.TestCase):
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
                    self.assertIn('--check-installation',command)
                    Path(command[-1]).write_text(json.dumps({'passed':True}))
            with patch.object(build_release,'ROOT',root),patch.object(build_release.subprocess,'run',side_effect=build_or_check):
                self.assertEqual(build_release.main(['--output',str(output)]),0)
            name='HotsHelper-'+build_release.VERSION+'-Windows-x64.zip'
            self.assertTrue(name.isascii())
            self.assertTrue((output/name).is_file(), 'GitHub cannot preserve the Chinese public archive name')
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

            with patch.object(build_release,'ROOT',root),patch.object(build_release.subprocess,'run',side_effect=capture_build):
                with self.assertRaises(BuildCommandCaptured):build_release.main(['--output',str(output)])
            command=commands[0]
            self.assertIn('--icon',command)
            self.assertEqual(Path(command[command.index('--icon')+1]),root/'data'/'hots.ico')
            resources=[command[index+1] for index,value in enumerate(command) if value=='--add-data']
            self.assertEqual(len(resources),len(build_release.PUBLIC_SEEDS))
            for resource,name in zip(resources,build_release.PUBLIC_SEEDS):
                source,destination=resource.rsplit(build_release.os.pathsep,1)
                self.assertEqual(Path(source).name,name)
                self.assertEqual(destination,'data')

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
