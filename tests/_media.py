"""Geração de mídia sintética com FFmpeg (`lavfi`) para os testes de ciclo real.

Reúne os ~16 blocos hoje espalhados por `test_cli.py`, `test_core.py`,
`test_inspect.py`, `test_guidance.py`, `test_nasa_spaced_urls.py`,
`test_instagram_recovery.py`, `test_social_recovery.py` e `test_workflow.py`.
Os três `synth_*` chamam `subprocess.run(..., check=True)` sem capturar
saída — o mesmo formato usado em todo bloco existente — e as flags/ordem do
`ffmpeg` são as já usadas hoje, só parametrizadas.

`skip_unless_ffmpeg` é o mesmo `unittest.skipUnless(shutil.which("ffmpeg"), …)`
repetido em cada método de teste que sintetiza mídia; importe e aplique como
decorador.
"""

import shutil
import subprocess
import unittest

skip_unless_ffmpeg = unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")


def synth_video(path, size="160x90", duration=6, rate=10, pattern="testsrc"):
    """Vídeo sintético H.264/yuv420p via `lavfi` (`testsrc` ou `testsrc2`)."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{pattern}=size={size}:duration={duration}:rate={rate}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def synth_image(path, color="red", size="64x64"):
    """Imagem sintética de um frame via `lavfi` `color=`."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


def synth_audio(path, frequency=440, duration=1):
    """Áudio sintético AAC via `lavfi` `sine=`."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
