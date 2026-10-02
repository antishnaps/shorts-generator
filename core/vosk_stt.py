import sys
import json
import os
import tempfile
import threading
import wave
import zipfile
import urllib.request
import subprocess
from pathlib import Path
from typing import List, Optional

from core.process_registry import run_registered

class VoskSynchronizer:
    """Offline STT synchronizer using Vosk."""

    MODELS = {
        'Russian': {
            'url': 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip',
            'folder': 'vosk-model-small-ru-0.22'
        },
        'English': {
            'url': 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip',
            'folder': 'vosk-model-small-en-us-0.15'
        }
    }

    def __init__(self, models_dir: str = 'tools/vosk_models'):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.loaded_models = {}
        self._model_lock = threading.RLock()

    def _download_and_extract_model(self, language: str, log_callback) -> Optional[str]:
        # Batch workers can request the same first-use model simultaneously.
        # Serialize download/extraction so they never overwrite one zip file.
        with self._model_lock:
            return self._download_and_extract_model_locked(language, log_callback)

    def _download_and_extract_model_locked(self, language: str, log_callback) -> Optional[str]:
        if language not in self.MODELS:
            # An English acoustic model produces misleading or empty timings
            # for Korean/CJK/Arabic/etc. Let the multilingual STT or the
            # deterministic subtitle aligner handle those languages instead.
            log_callback(
                f"ℹ️ Язык {language} не поддерживается локальным Vosk; "
                "переходим к мультиязычной синхронизации."
            )
            return None

        model_info = self.MODELS[language]
        model_path = self.models_dir / model_info['folder']
        
        if model_path.exists():
            return str(model_path)

        zip_path = self.models_dir / f"{model_info['folder']}.zip"
        
        try:
            log_callback(f"⬇️ Скачивание модели Vosk для {language}...")
            
            # Download with progress
            def reporthook(count, block_size, total_size):
                if total_size > 0:
                    percent = int(count * block_size * 100 / total_size)
                    if percent % 10 == 0 and percent <= 100:
                        sys.stdout.write(f"\rСкачивание модели... {percent}%")
                        sys.stdout.flush()

            urllib.request.urlretrieve(model_info['url'], str(zip_path), reporthook)
            sys.stdout.write("\n")
            
            log_callback("📦 Распаковка модели Vosk...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.models_dir)
                
            # Cleanup zip
            zip_path.unlink()
            
            if model_path.exists():
                log_callback(f"✅ Модель Vosk для {language} готова!")
                return str(model_path)
            else:
                log_callback("❌ Ошибка: папка модели не найдена после распаковки.")
                return None
                
        except Exception as e:
            log_callback(f"❌ Ошибка скачивания модели Vosk: {e}")
            if zip_path.exists():
                zip_path.unlink()
            return None

    def _convert_audio_to_wav(self, input_path: str, output_path: str, log_callback) -> bool:
        """Convert any audio to 16kHz mono WAV for Vosk."""
        try:
            from core.audio_processor import FFMPEG_PATH
            cmd = [
                FFMPEG_PATH, '-y', '-i', input_path,
                '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
                output_path
            ]
            run_registered(
                cmd,
                label="ffmpeg_vosk_audio_convert",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=600,
            )
            return True
        except Exception as e:
            log_callback(f"❌ Ошибка конвертации аудио для Vosk: {e}")
            return False

    def _align_with_original(self, vosk_timestamps: List[dict], original_text: str) -> List[dict]:
        """Aligns Vosk timestamps with original text to fix any typos from STT."""
        from core.subtitle_sync import SubtitleSynchronizer

        return SubtitleSynchronizer.align_word_timestamps_to_text(
            vosk_timestamps, original_text
        )

    def get_word_timestamps(self, audio_path: str, language: str, log_callback, original_text: str = "") -> Optional[List[dict]]:
        """Process audio with Vosk and return precise word timestamps."""
        try:
            import vosk
            vosk.SetLogLevel(-1) # Disable vosk logs
        except ImportError:
            log_callback("❌ Модуль vosk не установлен. Установите: pip install vosk")
            return None

        model_path = self._download_and_extract_model(language, log_callback)
        if not model_path:
            return None

        # Prepare audio
        descriptor, wav_path = tempfile.mkstemp(prefix='shorts_vosk_', suffix='.wav')
        os.close(descriptor)
        if not self._convert_audio_to_wav(audio_path, wav_path, log_callback):
            Path(wav_path).unlink(missing_ok=True)
            return None

        try:
            log_callback("🔍 Анализ аудио с помощью Vosk...")
            
            # Load model (cache it to avoid reloading)
            with self._model_lock:
                if language not in self.loaded_models:
                    self.loaded_models[language] = vosk.Model(model_path)
                model = self.loaded_models[language]
            
            word_timestamps = []
            
            with wave.open(wav_path, "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
                    log_callback("❌ Аудио файл не соответствует формату (16kHz, mono, s16le)")
                    return None

                rec = vosk.KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)

                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        if 'result' in res:
                            for word_info in res['result']:
                                word_timestamps.append({
                                    'word': word_info['word'],
                                    'start': float(word_info['start']),
                                    'end': float(word_info['end'])
                                })

                # Get final result
                final_res = json.loads(rec.FinalResult())
                if 'result' in final_res:
                    for word_info in final_res['result']:
                        word_timestamps.append({
                            'word': word_info['word'],
                            'start': float(word_info['start']),
                            'end': float(word_info['end'])
                        })

                if original_text:
                    word_timestamps = self._align_with_original(word_timestamps, original_text)

            if word_timestamps:
                log_callback(f"✅ Успешно синхронизировано слов через Vosk: {len(word_timestamps)}")
                return word_timestamps
            else:
                log_callback("⚠️ Vosk не смог распознать слова в аудио.")
                return None

        except Exception as e:
            log_callback(f"❌ Критическая ошибка Vosk STT: {e}")
            return None
        finally:
            # Cleanup temp wav
            if Path(wav_path).exists():
                try:
                    Path(wav_path).unlink()
                except:
                    pass

# Singleton instance
_vosk_sync = None
_vosk_sync_lock = threading.Lock()

def get_vosk_word_timestamps(audio_path: str, language: str, log_callback, original_text: str = "") -> Optional[List[dict]]:
    global _vosk_sync
    if _vosk_sync is None:
        with _vosk_sync_lock:
            if _vosk_sync is None:
                _vosk_sync = VoskSynchronizer()
    return _vosk_sync.get_word_timestamps(audio_path, language, log_callback, original_text)
