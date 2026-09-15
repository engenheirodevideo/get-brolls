import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ig_recovery',ROOT/'scripts/instagram/ig_curl_pair_downloader.py')
ig=importlib.util.module_from_spec(spec);spec.loader.exec_module(ig)

class InstagramRecoveryTests(unittest.TestCase):
    def test_failed_transfer_does_not_poison_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); conf=root/'video.conf';conf.write_text('url = "https://example.org/private-media"\n')
            target=root/'part.mp4'
            def fail(cmd,**kwargs):
                Path(cmd[cmd.index('--output')+1]).write_bytes(b'partial')
                raise subprocess.CalledProcessError(18,cmd)
            kwargs=dict(cfg_path=conf,part_path=target,config_output_root=root,force_download=False,prefer_config_output=False)
            with patch.object(ig.subprocess,'run',side_effect=fail),self.assertRaises(SystemExit):
                ig.download_or_reuse(**kwargs)
            self.assertFalse(target.exists())
            def success(cmd,**kwargs):
                Path(cmd[cmd.index('--output')+1]).write_bytes(b'complete')
            with patch.object(ig.subprocess,'run',side_effect=success):
                self.assertEqual(ig.download_or_reuse(**kwargs),'downloaded')
            self.assertEqual(target.read_bytes(),b'complete')

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_separate_video_audio_merge_and_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); configs=root/'configs';configs.mkdir()
            video=root/'video.mp4'; audio=root/'audio.m4a'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=1','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','aac',str(audio)],check=True)
            for stem in ('01_TEST','02_TEST'):
                (configs/(stem+'_video.conf')).write_text(f'url = "https://example.org/video"\noutput = "{video}"\n')
                (configs/(stem+'_audio.conf')).write_text(f'url = "https://example.org/audio"\noutput = "{audio}"\n')
            args=['--config-dir',str(configs),'--output-dir',str(root/'out'),'--parts-dir',str(root/'parts'),'--layout','flat','--fail-on-duplicate-audio']
            with self.assertRaises(SystemExit): ig.main(args)
            report=ig.verify_output(root/'out/01_TEST.mp4')
            self.assertTrue(report['audio_hash_sha256'])
            self.assertEqual(ig.audio_hash(root/'out/01_TEST.mp4'),ig.audio_hash(root/'out/02_TEST.mp4'))
