"""Resolve shipped media tools before application imports; no downloads."""
import os
from pathlib import Path
import sys

if getattr(sys, 'frozen', False):
    resources = Path(sys._MEIPASS)
    media = resources / 'tools' / 'ffmpeg'
    os.environ['PATH'] = str(media) + os.pathsep + os.environ.get('PATH', '')
    os.environ['IMAGEIO_FFMPEG_EXE'] = str(media / 'ffmpeg.exe')
    os.environ['FFMPEG_BINARY'] = str(media / 'ffmpeg.exe')
    if '--portable-check' in sys.argv:
        from tools.portable_smoke import run
        raise SystemExit(run(Path(sys.argv[sys.argv.index('--portable-check') + 1])))
