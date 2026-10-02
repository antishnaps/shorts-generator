"""Portable free edition. Build only inside an audited source export."""
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH)
if not (root / 'RELEASE_MANIFEST.json').is_file():
    raise RuntimeError('Build from a clean free source export, not the private workspace')
runtime = Path(os.environ['CONTENTBOT_FREE_RUNTIME'])
datas = [(str(root / 'config.json.template'), '.'),
         (str(root / 'gui/locale_overrides.json'), 'gui'),
         (str(root / 'assets/branding'), 'assets/branding'),
         (str(root / 'assets/support'), 'assets/support')]
for package in ('imageio', 'imageio-ffmpeg', 'tqdm', 'yt-dlp', 'yt-dlp-ejs'):
    datas += copy_metadata(package)
datas += collect_data_files('yt_dlp_ejs')
# Use one audited full FFmpeg build, not a second embedded imageio fallback.
datas += collect_data_files('imageio_ffmpeg', excludes=['binaries/*.exe'])
hidden = []
for package in ('core', 'gui', 'google.genai', 'json_repair', 'moviepy',
                'edge_tts', 'browser_cookie3', 'yt_dlp_ejs', 'googleapiclient'):
    hidden += collect_submodules(package, filter=lambda n: '.tests' not in n)
a = Analysis([str(root / 'main.py')], pathex=[str(root)],
    binaries=[(str(runtime / 'ffmpeg.exe'), 'tools/ffmpeg'),
              (str(runtime / 'ffprobe.exe'), 'tools/ffmpeg'),
              (str(runtime / 'deno.exe'), 'runtime')] + collect_dynamic_libs('vosk'),
    datas=datas, hiddenimports=hidden + ['tools.portable_smoke'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': 'Agg'}},
    runtime_hooks=[str(root / 'tools/portable_runtime.py')],
    excludes=['pytest', 'scipy', 'pandas', 'IPython', 'jupyter', 'notebook',
              'tkinter', 'PyQt5.QtWebEngine', 'PyQt5.QtWebEngineWidgets'],
    noarchive=False)
# The upstream imageio hook adds another FFmpeg even when explicitly excluded
# from our datas; remove that duplicate after hooks have run.
def without_duplicate_ffmpeg(entries):
    return [entry for entry in entries if not (
        entry[0].replace('\\', '/').startswith('imageio_ffmpeg/binaries/')
        and entry[0].lower().endswith('.exe'))]
a.datas = without_duplicate_ffmpeg(a.datas)
a.binaries = without_duplicate_ffmpeg(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ContentBotPro',
          console=False, debug=False, strip=False, upx=False,
          icon=str(root / 'assets/branding/contentbot.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='ContentBotPro-Free')
