#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generation Worker - QThread for video generation

P1: Extracted from main_window.py to reduce monolithic file size
"""

import threading
from PyQt5.QtCore import QThread, pyqtSignal

from core.gemini_models import DEFAULT_IMAGE_MODEL
from gui.translations import t


class GenerationWorker(QThread):
    """
    Worker thread for video generation.
    
    Runs video generation in a separate thread to keep UI responsive.
    Emits signals for progress updates, logging, and completion.
    
    Signals:
        progress (int): Progress percentage (0-100)
        log (str): Log message to display
        finished (): Generation completed
        new_image (str): Path to newly generated image for preview
    
    Attributes:
        stop_flag (bool): Set to True to request generation stop
        current_video (int): Current video number being generated
        total_videos (int): Total number of videos to generate
    """

    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal()
    new_image = pyqtSignal(str)  # Signal for image preview
    publish_package_ready = pyqtSignal(dict)
    
    # Stage weights for granular progress (must sum to 100)
    STAGE_WEIGHTS = {
        'text': 10,      # 0-10%: Text generation
        'audio': 25,     # 10-35%: TTS audio generation  
        'images': 35,    # 35-70%: Image generation
        'render': 30,    # 70-100%: Video rendering
    }

    def __init__(
        self,
        source_data,
        num_videos,
        music_path,
        api_key,
        video_settings,
        output_path,
        subtitle_settings=None,
        media_path=None,
        use_ai_image_generation=False,
        strict_theme_following=True,
        strict_text_theme=True,
        google_ai_api_key=None,
        overlay_settings=None,
        use_triple_template=False,
        audio_settings=None,
        enable_parallel_generation=False,
        num_workers=2,
        unlimited_images=False,
        num_unique_images=5,
        enable_scene_variety=True,
        image_model=DEFAULT_IMAGE_MODEL,
        veo3_settings=None,
        youtube_mixer_settings=None,
        custom_images_folder=None,
        use_only_custom_images=False,
        use_image_cache=False,
        save_to_image_cache=False,
        image_pool_settings=None,
        use_reference_images=False,
        reference_images_folder=None,
        avatar_settings=None,
        final_output_settings=None,
    ):
        """
        Initialize GenerationWorker.
        
        Args:
            source_data: Theme or source data for generation
            num_videos: Number of videos to generate
            music_path: Path to music folder
            api_key: Gemini API key
            video_settings: Video settings dict (width, height, fps, etc.)
            output_path: Output folder path
            subtitle_settings: Subtitle configuration
            media_path: Path to overlay media
            use_ai_image_generation: Enable AI image generation
            strict_theme_following: Strict theme following for images
            strict_text_theme: Strict theme following for text
            google_ai_api_key: Google AI API key
            overlay_settings: Overlay configuration
            use_triple_template: Use half-screen image template
            audio_settings: TTS settings (provider, voice, speed)
            enable_parallel_generation: Enable parallel generation
            num_workers: Number of parallel workers
            unlimited_images: Generate unlimited images
            num_unique_images: Number of unique images per video
            enable_scene_variety: Enable scene variety system
            image_model: Image generation model name
            veo3_settings: Veo 3 AI intro settings
            youtube_mixer_settings: YouTube mixer settings
            custom_images_folder: Path to custom images folder
            use_only_custom_images: Use only custom images without generation
            use_image_cache: Use cached images when available
            save_to_image_cache: Save generated images to cache
            use_reference_images: Use reference images
            reference_images_folder: Path to reference images folder
            avatar_settings: AI Avatar (HeyGen) settings dict
        """
        super().__init__()
        self.source_data = source_data
        self.num_videos = num_videos
        self.music_path = music_path
        self.api_key = api_key
        from core.settings_schema import (
            normalize_video_settings,
            normalize_subtitle_settings,
            normalize_audio_settings,
            normalize_overlay_settings,
            normalize_youtube_mixer_settings,
        )
        self.video_settings = normalize_video_settings(video_settings)
        self.output_path = output_path
        self.subtitle_settings = normalize_subtitle_settings(subtitle_settings)
        self.media_path = media_path
        self.use_ai_image_generation = use_ai_image_generation
        self.strict_theme_following = strict_theme_following
        self.strict_text_theme = strict_text_theme
        self.google_ai_api_key = google_ai_api_key
        self.overlay_settings = normalize_overlay_settings(overlay_settings)
        self.use_triple_template = use_triple_template
        self.audio_settings = normalize_audio_settings(audio_settings)
        self.enable_parallel_generation = enable_parallel_generation
        self.num_workers = num_workers
        self.stop_flag = False
        self.unlimited_images = unlimited_images
        self.num_unique_images = num_unique_images
        self.enable_scene_variety = enable_scene_variety
        self.image_model = image_model
        self.veo3_settings = veo3_settings or {'enabled': False}
        self.youtube_mixer_settings = normalize_youtube_mixer_settings(youtube_mixer_settings)
        self.custom_images_folder = custom_images_folder
        self.use_only_custom_images = use_only_custom_images
        self.use_image_cache = use_image_cache
        self.save_to_image_cache = save_to_image_cache
        self.image_pool_settings = image_pool_settings or {'use_image_pool': False}
        self.use_reference_images = use_reference_images
        self.reference_images_folder = reference_images_folder
        self.avatar_settings = avatar_settings or {'enabled': False}
        self.final_output_settings = final_output_settings or {}
        self.error_message = None
        
        # Granular progress tracking
        self.current_video = 0
        self.total_videos = num_videos
        self._current_stage_progress = 0
        
        # Thread-safe parallel progress tracking
        self._progress_lock = threading.Lock()
        self._parallel_stages = {}  # {video_num: cumulative_weight}
        self._thread_local = threading.local()  # Per-thread video_num

    def run(self):
        """Run generation process in separate thread."""
        self.error_message = None
        try:
            # Import here to avoid circular imports
            from core.generator import ShortsGenerator

            generator = ShortsGenerator()
            
            # Common kwargs for both methods
            common_kwargs = {
                'source_data': self.source_data,
                'num_videos': self.num_videos,
                'music_path': self.music_path,
                'api_key': self.api_key,
                'video_settings': self.video_settings,
                'output_path': self.output_path,
                'progress_callback': self.progress_callback,
                'log_callback': self.log_callback,
                'subtitle_settings': self.subtitle_settings,
                'media_path': self.media_path,
                'use_ai_image_generation': self.use_ai_image_generation,
                'strict_theme_following': self.strict_theme_following,
                'google_ai_api_key': self.google_ai_api_key,
                'overlay_settings': self.overlay_settings,
                'worker': self,
                'use_triple_template': self.use_triple_template,
                'audio_settings': self.audio_settings,
                'unlimited_images': self.unlimited_images,
                'num_unique_images': self.num_unique_images,
                'enable_scene_variety': self.enable_scene_variety,
                'image_model': self.image_model,
                'veo3_settings': self.veo3_settings,
                'youtube_mixer_settings': self.youtube_mixer_settings,
                'custom_images_folder': self.custom_images_folder,
                'use_only_custom_images': self.use_only_custom_images,
                'use_image_cache': self.use_image_cache,
                'save_to_image_cache': self.save_to_image_cache,
                'image_callback': self.image_callback,
                'image_pool_settings': self.image_pool_settings,
                'avatar_settings': self.avatar_settings,
                'final_output_settings': self.final_output_settings,
                'publish_callback': self.publish_package_ready.emit,
            }
            
            if self.enable_parallel_generation:
                generator.generate_shorts_parallel(
                    **common_kwargs,
                    num_workers=self.num_workers,
                    force_parallel=True,
                )
            else:
                generator.generate_shorts(**common_kwargs)

        except Exception as e:
            self.error_message = str(e)
            self.log.emit(
                t("generation_worker_error").format(error=self.error_message)
            )
        finally:
            self.finished.emit()

    def stop(self):
        """Request generation stop."""
        self.stop_flag = True

    def progress_callback(self, value: int) -> None:
        """
        Progress callback - emits progress signal.
        
        Args:
            value: Progress percentage (0-100)
        """
        self.progress.emit(value)
    
    def stage_progress_callback(self, stage: str, stage_percent: int = 100) -> None:
        """
        Granular stage progress callback - calculates overall progress based on stage.
        Thread-safe: works correctly both in sequential and parallel modes.
        
        Args:
            stage: Current stage name ('text', 'audio', 'images', 'render')
            stage_percent: Progress within the current stage (0-100)
        """
        if self.total_videos <= 0:
            return
        
        stages_order = ['text', 'audio', 'images', 'render']
        cumulative_weight = 0
        
        for s in stages_order:
            if s == stage:
                stage_weight = self.STAGE_WEIGHTS.get(stage, 0)
                cumulative_weight += (stage_weight * stage_percent) / 100
                break
            else:
                cumulative_weight += self.STAGE_WEIGHTS.get(s, 0)
        
        with self._progress_lock:
            # Thread-local video_num для параллельного режима, fallback на self.current_video
            video_num = getattr(self._thread_local, 'video_num', self.current_video)
            # В параллельном режиме: трекаем прогресс каждого видео отдельно
            self._parallel_stages[video_num] = cumulative_weight
            
            # Общий прогресс = сумма прогрессов всех видео / total
            total_weight = sum(self._parallel_stages.values())
            overall_progress = total_weight / self.total_videos
        
        clamped = max(0, min(100, int(overall_progress)))
        self._current_stage_progress = clamped
        self.progress.emit(clamped)
    
    def set_current_video(self, video_num: int) -> None:
        """
        Set current video number for progress calculation.
        Thread-safe: stores in thread-local so parallel workers don't collide.
        
        Args:
            video_num: Current video number (1-based)
        """
        self.current_video = video_num
        self._thread_local.video_num = video_num

    def log_callback(self, message: str) -> None:
        """
        Log callback - emits log signal.
        
        Args:
            message: Log message to display
        """
        self.log.emit(message)
    
    def image_callback(self, image_path: str) -> None:
        """
        Image callback - emits new_image signal when image is generated.
        
        Args:
            image_path: Path to generated image
        """
        self.new_image.emit(image_path)
