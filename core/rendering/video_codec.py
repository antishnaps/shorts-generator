#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🎬 Video Codec Settings
Настройки кодеков для рендеринга видео (NVENC, libx264)
"""

from typing import Dict, Any, Callable, Optional

from core.process_registry import run_registered


class VideoCodec:
    """Управление настройками видео кодеков"""
    
    # NVENC settings (NVIDIA GPU encoding)
    NVENC_PRESET = 'p3'  # p1-p7, p3 = баланс скорость/качество
    NVENC_CQ = 23  # Quality (lower = better, 0-51) - 23 = YouTube стандарт
    NVENC_BITRATE = '10M'  # Битрейт для YouTube
    NVENC_MAXRATE = '15M'  # Max bitrate
    
    def __init__(self, ffmpeg_path: str = 'ffmpeg'):
        """
        Args:
            ffmpeg_path: Путь к ffmpeg executable
        """
        self.ffmpeg_path = ffmpeg_path
        self._nvenc_available = None  # Cache NVENC availability
        self._codec_settings_cache = {}
        self._nvenc_logged = False
        self._cpu_logged = False
    
    def check_nvenc_available(self) -> bool:
        """
        Проверяет доступность NVENC (NVIDIA GPU encoding).
        
        Returns:
            True если NVENC доступен, False иначе
        """
        if self._nvenc_available is not None:
            return self._nvenc_available
        
        try:
            # Check if h264_nvenc encoder is available
            result = run_registered(
                [self.ffmpeg_path, '-hide_banner', '-encoders'],
                label="ffmpeg_video_codec_probe",
                capture_output=True,
                text=True,
                timeout=5
            )
            self._nvenc_available = 'h264_nvenc' in result.stdout
            return self._nvenc_available
        except Exception:
            self._nvenc_available = False
            return False

    
    def get_video_codec_settings(self, use_nvenc: bool = True, 
                                 log_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Получает оптимальные настройки видео кодека.
        
        Args:
            use_nvenc: Использовать NVENC если доступен
            log_callback: Функция для логирования
        
        Returns:
            Словарь с настройками кодека
        """
        # Кэширование результата
        cache_key = f"codec_{use_nvenc}"
        if cache_key in self._codec_settings_cache:
            return self._codec_settings_cache[cache_key].copy()
        
        if use_nvenc and self.check_nvenc_available():
            # NVENC settings (NVIDIA GPU encoding) - OPTIMIZED FOR SPEED + QUALITY
            if log_callback and not self._nvenc_logged:
                log_callback("✅ NVENC (NVIDIA GPU) обнаружен - используем аппаратное ускорение")
                self._nvenc_logged = True
            
            result = {
                'vcodec': 'h264_nvenc',
                'preset': self.NVENC_PRESET,
                'tune': 'hq',    # High quality tuning
                'rc': 'vbr',     # Variable bitrate
                'cq': self.NVENC_CQ,
                'b:v': self.NVENC_BITRATE,
                'maxrate': self.NVENC_MAXRATE,
                'bufsize': '20M',
            }
        else:
            # CPU encoding fallback - OPTIMIZED FOR SPEED
            if log_callback and not self._cpu_logged:
                log_callback("ℹ️ NVENC недоступен - используем CPU кодирование (ultrafast preset)")
                self._cpu_logged = True
            
            result = {
                'vcodec': 'libx264',
                'preset': 'ultrafast',  # ~5x faster than 'medium'
                'tune': 'fastdecode',   # Optimize for fast decoding
                'crf': 20,              # Slightly better quality to compensate
            }
        
        # Кэшируем результат
        self._codec_settings_cache[cache_key] = result.copy()
        return result
    
    def clear_cache(self):
        """Очищает кэш настроек кодека"""
        self._codec_settings_cache.clear()
        self._nvenc_available = None
        self._nvenc_logged = False
        self._cpu_logged = False
