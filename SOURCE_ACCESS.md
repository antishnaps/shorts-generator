# Portable build source access

Application source and PyInstaller build scripts are available without charge:
https://github.com/antishnaps/shorts-generator
Use the source archive published alongside this binary release for matching code.

The program is GPL-3.0-only. No bundled component has been modified. License
texts from installed wheels are preserved in `licenses/`; exact Python package
versions and their source download pages are listed in `DEPENDENCIES.json`.
The Qt libraries are separate DLLs and may be replaced with compatible builds.

## Native components

- Python 3.12.10 source: https://www.python.org/downloads/release/python-31210/
- Qt 5.15.2 source and build instructions: https://download.qt.io/archive/qt/5.15/5.15.2/single/
- PyQt5 5.15.11 source: https://pypi.org/project/PyQt5/5.15.11/#files
- FFmpeg 7.1.1 exact upstream revision: https://github.com/FFmpeg/FFmpeg/commit/db69d06eee
- FFmpeg Windows builder and dependency information: https://www.gyan.dev/ffmpeg/builds/
- Deno 2.6.2 source: https://github.com/denoland/deno/tree/v2.6.2
- OpenCV wheel sources, build patches and bundled media notices:
  https://github.com/opencv/opencv-python
- Vosk source and native build instructions: https://github.com/alphacep/vosk-api

FFmpeg configure flags and the distributor's original README and GPL text are
preserved in `licenses/ffmpeg/`. Dependency source links do not grant rights to
stock footage, music, fonts or other media users choose to download.

## Build the app

Use Windows x64 and Python 3.12. Install `requirements.txt`, plus
`PyInstaller==6.18.0`. Put ffmpeg.exe, ffprobe.exe and deno.exe in a separate
runtime folder. Set `CONTENTBOT_FREE_RUNTIME` to that folder, then run:

```
python -m PyInstaller ContentBotPro-Free.spec
```

The executable is `dist/ContentBotPro-Free/ContentBotPro.exe`. Preserve the whole
directory, including `_internal`. This free build uses no obfuscation, activation
server, self-integrity lock or developer credentials.
