import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from getbrolls import providers

class SocialRecoveryTests(unittest.TestCase):
    def test_social_urls_have_download_transport_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            for url in ('https://youtube.com/watch?v=abcdefghijk', 'https://instagram.com/reel/ABC123/', 'https://instagram.com/nasajohnson/reel/DcMXl1IPNtB/', 'https://www.tiktok.com/@fixture/video/12345'):
                c = providers.resolve(url)
                self.assertEqual(c['acquisition']['method'], 'yt-dlp')
                self.assertEqual(c['acquisition']['status'], 'available')
                self.assertEqual(c['approval']['status'], 'pending')
                self.assertNotEqual(c['state'], 'reference_only')

    def test_public_tree_is_consolidated_under_getbrolls(self):
        scripts = ROOT / 'scripts'
        self.assertEqual(
            ['getbrolls'],
            sorted(path.name for path in scripts.iterdir() if path.is_dir() and path.name != '__pycache__' and not path.name.startswith('.')),
        )
        for path in (
            'scripts/getbrolls/tools/youtube/search.sh',
            'scripts/getbrolls/tools/youtube/contact.sh',
            'scripts/getbrolls/tools/youtube/fetch.sh',
            'scripts/getbrolls/instagram_pairs.py',
            'requirements.txt',
        ):
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertFalse((ROOT / 'scripts/broll').exists())
        self.assertFalse((ROOT / 'scripts/instagram').exists())

    def test_doctor_checks_download_dependencies(self):
        from getbrolls.cli import main
        result = main(['doctor'])
        for name in ('yt-dlp', 'curl', 'bash'):
            self.assertIn(name, result['executables'])
        self.assertIn('social', result)

if __name__ == '__main__': unittest.main()

class RemotePreviewTests(unittest.TestCase):
    def test_local_ytdlp_accepts_windows_and_posix_virtualenv_layouts(self):
        from getbrolls import social
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / '.venv/Scripts/yt-dlp.exe'
            windows.parent.mkdir(parents=True)
            windows.write_text('fixture', encoding='utf-8')
            self.assertEqual(windows, social.local_ytdlp(root))
            windows.unlink()
            posix = root / '.venv/bin/yt-dlp'
            posix.parent.mkdir(parents=True)
            posix.write_text('fixture', encoding='utf-8')
            self.assertEqual(posix, social.local_ytdlp(root))

    def test_local_playwright_accepts_windows_command_shim(self):
        from getbrolls.commands import _local_playwright
        with tempfile.TemporaryDirectory() as tmp:
            shim = Path(tmp) / '.tools/node_modules/.bin/playwright-cli.cmd'
            shim.parent.mkdir(parents=True)
            shim.write_text('fixture', encoding='utf-8')
            self.assertTrue(_local_playwright(tmp))

    def test_youtube_search_without_key_uses_ytdlp_metadata(self):
        from getbrolls import social
        with patch.dict(os.environ, {}, clear=True), patch.object(social, 'search', return_value=[{'id':'abcdefghijk','title':'Literal source','duration':60,'channel':'Author'}]):
            c=providers.search('youtube','test',1)[0]
            self.assertEqual(c['title'],'Literal source')
            self.assertEqual(c['creator']['name'],'Author')
            self.assertEqual(c['acquisition']['method'],'yt-dlp')

    def test_preview_caches_remote_interval_and_fetch_uses_same_bytes(self):
        import shutil, subprocess
        from getbrolls.cli import main
        from getbrolls import social
        from getbrolls.media import probe
        if not shutil.which('ffmpeg'): self.skipTest('FFmpeg required')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'synthetic.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
            c=main(['resolve','--url','https://youtube.com/watch?v=abcdefghijk','--project',str(root)])
            base=['--candidate',c['id'],'--project',str(root)]
            def download(url,target,start,end):
                self.assertEqual((start,end),(30,32)); shutil.copyfile(source,target); return target
            with patch.object(social,'download_segment',side_effect=download) as get:
                c=main(['preview',*base,'--start','30','--end','32'])
                self.assertTrue(c['preview'].get('gif_path'))
                self.assertEqual(c['approval']['status'],'pending')
                self.assertEqual(c['provider'],'youtube')
                self.assertEqual(c['local_start_s'],30)
                main(['preview',*base,'--start','30','--end','32'])
                self.assertEqual(get.call_count,1)
            # Synthetic fixture only, not an approval attributed to a real person.
            main(['approve',*base,'--start','30','--end','32','--by','Synthetic test fixture',
                  '--statement','Aprovo este trecho sintético.'])
            main(['permit',*base,'--evidence','Locally generated synthetic test media'])
            c=main(['fetch',*base])
            self.assertAlmostEqual(probe(root/'brolls'/c['output']['path'])['duration_s'],2,delta=.1)
            self.assertEqual(main(['verify','--project',str(root)])['count'],1)

class HelperRuntimeTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Helpers Bash são opcionais no Windows nativo.')
    def test_original_helper_enables_node_without_deno(self):
        import shutil, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); helpers=root/'scripts/getbrolls/tools/youtube';shutil.copytree(ROOT/'scripts/getbrolls/tools/youtube',helpers)
            bindir=root/'bin';bindir.mkdir()
            for name in ('yt-dlp','node'):
                p=bindir/name;p.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding='utf-8');p.chmod(0o755)
            (bindir/'dirname').symlink_to('/usr/bin/dirname')
            result=subprocess.run(['/bin/bash',str(helpers/'search.sh'),'literal','1'],env={'PATH':str(bindir)},capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--js-runtimes\nnode\n',result.stdout)

    @unittest.skipIf(os.name == 'nt', 'Helpers Bash são opcionais no Windows nativo.')
    def test_contact_helper_uses_portable_mktemp_and_linux_font(self):
        import shutil, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); helpers=root/'scripts/getbrolls/tools/youtube';shutil.copytree(ROOT/'scripts/getbrolls/tools/youtube',helpers)
            bindir=root/'bin';bindir.mkdir()
            for name in ('awk','dirname','head','rm'):
                (bindir/name).symlink_to(shutil.which(name))
            ffmpeg_args=root/'ffmpeg-args.txt'; font=root/'DejaVuSans.ttf';font.write_bytes(b'fixture')
            for name, body in {
                'yt-dlp': '#!/bin/sh\ncase "$*" in *"%(title)s"*) echo "Fixture title";; *) echo "https://example.org/video.mp4";; esac\n',
                'mktemp': '#!/bin/sh\ncase "$1" in *XXXXXX) p="${1%XXXXXX}ABC123"; : > "$p"; echo "$p";; *) exit 64;; esac\n',
                'ffmpeg': '#!/bin/sh\nprintf "%s\\n" "$@" > "$FFMPEG_ARGS_FILE"\nfor last do :; done\n: > "$last"\n',
            }.items():
                path=bindir/name;path.write_text(body, encoding='utf-8');path.chmod(0o755)
            output=root/'contact.jpg'
            result=subprocess.run(
                ['/bin/bash',str(helpers/'contact.sh'),'abcdefghijk','00:00-00:02',str(output)],
                env={'PATH':str(bindir),'TMPDIR':str(root),'GB_FONT_FILE':str(font),'FFMPEG_ARGS_FILE':str(ffmpeg_args)},capture_output=True,text=True,encoding='utf-8',
            )
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(output.is_file())
            args=ffmpeg_args.read_text(encoding='utf-8')
            self.assertIn(f'drawtext=fontfile={font}',args)
            self.assertIn("text='%{n}'",args)
            self.assertIn('textfile=',args)

class InstallerTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'O Windows usa scripts/install.ps1.')
    def test_rejects_old_node_before_installing_dependencies(self):
        import shutil, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            bindir=Path(tmp)
            for name in ('bash','dirname','python3','ffmpeg','ffprobe','curl','awk','npm','npx'):
                actual=shutil.which(name)
                if actual: (bindir/name).symlink_to(actual)
            node=bindir/'node'; node.write_text('#!/bin/sh\nprintf "v20.0.0\\n"\n', encoding='utf-8'); node.chmod(0o755)
            r=subprocess.run(['/bin/bash',str(ROOT/'scripts/install.sh'),'--check'],env={'PATH':str(bindir)},capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(r.returncode,0,r.stdout)
            self.assertIn('22',r.stdout+r.stderr)

class SocialErrorTests(unittest.TestCase):
    def test_ip_block_is_reported_without_signed_urls(self):
        import subprocess
        from getbrolls import social
        with patch.object(social, 'command', return_value=['yt-dlp']), patch.object(social.subprocess, 'run', side_effect=subprocess.CalledProcessError(1,['yt-dlp'],stderr='Your IP address is blocked from accessing this post https://cdn.example/?secret=x')):
            with self.assertRaisesRegex(Exception, 'bloqueou o IP') as caught:
                social.run([])
            self.assertNotIn('secret', str(caught.exception))
