"""Explicit offline release check, invoked only with --portable-check PATH."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def run(destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=False)
    report = {'checks': {}, 'ok': False}
    try:
        resources = Path(sys._MEIPASS)
        os.chdir(Path(sys.executable).parent)
        # No developer PATH, home credentials or network are needed.
        import socket
        def blocked(*args, **kwargs):
            raise RuntimeError('Network disabled during portable check')
        socket.socket.connect = blocked
        socket.create_connection = blocked
        import main
        assert main.run_smoke_test(Path(sys.executable).parent) == 0
        report['checks']['startup_resources'] = True
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFontDatabase
        app = QApplication([])
        if not QFontDatabase().families():
            fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
            for name in ('segoeui.ttf', 'arial.ttf', 'malgun.ttf', 'msyh.ttc'):
                if (fonts / name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts / name))
        from core.config_manager import ensure_config_exists
        ensure_config_exists()
        from core.startup_validator import StartupValidator
        status = StartupValidator(verbose=False).run_quick_checks()
        assert status.ready, status.errors
        report['checks']['required_dependencies'] = True
        from gui.main_window_v2 import MainWindowV2
        from gui.donations import SupportDialog, load_payment_details, qr_matches_address
        window = MainWindowV2()
        window.show()
        deadline = time.monotonic() + 15
        while window.generator is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
        assert window.generator is not None
        app.processEvents()
        assert window.grab().save(str(destination / 'main-window.png'))
        report['checks']['main_window'] = True
        details = load_payment_details()
        assert details['russia_card'] and details['belarus_visa']
        assert qr_matches_address(details['ethereum_address'])
        dialog = SupportDialog(window)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(destination / 'support-dialog.png'))
        report['checks']['support_resources'] = True
        dialog.close()
        window.close()
        ffmpeg = resources / 'tools/ffmpeg/ffmpeg.exe'
        ffprobe = resources / 'tools/ffmpeg/ffprobe.exe'
        clip = destination / 'render-check.mp4'
        subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=360x640:rate=24:duration=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True,
            capture_output=True, timeout=60)
        probe = subprocess.run([str(ffprobe), '-v', 'error', '-show_streams',
            '-of', 'json', str(clip)], check=True, capture_output=True, timeout=30)
        stream = json.loads(probe.stdout)['streams'][0]
        assert (stream['width'], stream['height']) == (360, 640)
        report['checks']['render_and_probe'] = True
        from core.youtube.download_policy import detect_youtube_js_runtimes
        runtimes = detect_youtube_js_runtimes()
        assert 'deno' in runtimes
        report['checks']['youtube_runtime'] = True
        from google import genai
        import edge_tts, cv2, vosk, moviepy, yt_dlp
        report['checks']['optional_imports'] = True
        report['ok'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    (destination / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 2
