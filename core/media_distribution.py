#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Pure media sequencing algorithms used by the generation pipeline."""

import random
from collections import Counter
from typing import List, Sequence, Tuple


MediaItem = Tuple[str, str]


class MediaDistributionPlanner:
    """Build varied image/video sequences without consecutive path repeats."""

    def __init__(self, rng=None):
        self.rng = rng or random

    def create_mixed_media_list(
        self,
        image_paths: Sequence[str],
        video_clips: Sequence[str],
        total_shots: int = None,
        video_ratio: float = 0.3,
        start_with_images: bool = False,
        first_shot_from_pool: bool = False,
        allow_video_reuse: bool = False,
    ) -> List[MediaItem]:
        video_clips = list(dict.fromkeys(str(path) for path in video_clips if path))
        video_keys = set(video_clips)
        image_paths = [
            path for path in dict.fromkeys(str(path) for path in image_paths if path)
            if path not in video_keys
        ]
        if not video_clips:
            count = len(image_paths) if total_shots is None else total_shots
            return self.create_no_repeat_sequence(image_paths, count, "image")

        # Если все image_paths — заглушки (fallback) и есть клипы — используем только клипы
        from pathlib import Path as _Path
        _all_fallback = image_paths and all('fallback' in _Path(p).name.lower() for p in image_paths)
        if _all_fallback:
            count = total_shots if total_shots is not None else len(video_clips)
            if allow_video_reuse:
                return self.create_reusable_sequence(video_clips, count, "video")
            return self.create_no_repeat_sequence(video_clips, count, "video")

        if not image_paths:
            count = len(video_clips) if total_shots is None else total_shots
            if allow_video_reuse:
                return self.create_reusable_sequence(video_clips, count, "video")
            return self.create_no_repeat_sequence(video_clips, count, "video")

        total_shots = total_shots if total_shots is not None else len(image_paths) + len(video_clips)
        total_shots = max(0, int(total_shots))
        if total_shots == 0:
            return []
        if (
            len(image_paths) + len(video_clips) < total_shots
            and not (allow_video_reuse and video_clips)
        ):
            from core.smart_clip_matcher import InsufficientUniqueVisualsError
            raise InsufficientUniqueVisualsError(
                "Недостаточно уникального визуального материала: "
                f"нужно {total_shots} шотов, доступно "
                f"{len(video_clips)} видео + {len(image_paths)} изображений. "
                "Повторы отключены."
            )

        try:
            clamped_video_ratio = float(video_ratio)
        except (TypeError, ValueError):
            clamped_video_ratio = 0.3
        clamped_video_ratio = max(0.0, min(1.0, clamped_video_ratio))
        if clamped_video_ratio <= 0:
            if len(image_paths) < total_shots:
                from core.smart_clip_matcher import InsufficientUniqueVisualsError
                raise InsufficientUniqueVisualsError(
                    f"Режим без видео требует {total_shots} уникальных изображений, "
                    f"доступно {len(image_paths)}. Повторы отключены."
                )
            num_videos = 0
        elif clamped_video_ratio >= 1:
            if len(video_clips) < total_shots and not allow_video_reuse:
                from core.smart_clip_matcher import InsufficientUniqueVisualsError
                raise InsufficientUniqueVisualsError(
                    f"Режим 100% видео требует {total_shots} уникальных клипов, "
                    f"доступно {len(video_clips)}. Повторы отключены."
                )
            num_videos = total_shots
        else:
            num_videos = max(1, int(total_shots * clamped_video_ratio))
            num_videos = max(num_videos, total_shots - len(image_paths))
            if not allow_video_reuse:
                num_videos = min(len(video_clips), num_videos)
        num_images = total_shots - num_videos

        unique_clip_count = len(set(video_clips))
        if (
            clamped_video_ratio < 1.0
            and image_paths
            and unique_clip_count == 1
            and num_videos > 1
        ):
            # In mixed image/video mode, prefer a slightly lower video ratio to
            # showing the exact same source clip back-to-back. This avoids the
            # "frozen shot" feeling when only one usable video is available.
            max_non_adjacent_videos = num_images if (start_with_images or first_shot_from_pool) else num_images + 1
            max_non_adjacent_videos = max(1, max_non_adjacent_videos)
            if num_videos > max_non_adjacent_videos:
                extra_images = min(num_videos - max_non_adjacent_videos, total_shots - num_images)
                num_videos -= extra_images
                num_images += extra_images

        if num_videos <= 0:
            return self.create_no_repeat_sequence(image_paths, total_shots, "image")
        if num_images <= 0:
            if allow_video_reuse:
                return self.create_reusable_sequence(video_clips, total_shots, "video")
            return self.create_no_repeat_sequence(video_clips, total_shots, "video")
        if unique_clip_count == 1 and clamped_video_ratio < 1.0:
            return self.create_single_clip_mixed_distribution(
                image_paths,
                video_clips,
                num_images,
                num_videos,
                total_shots,
                start_with_images=start_with_images,
                first_shot_from_pool=first_shot_from_pool,
            )

        result = self.create_video_first_distribution(
            image_paths,
            video_clips,
            num_images,
            num_videos,
            total_shots,
            start_with_images=start_with_images,
            first_shot_from_pool=first_shot_from_pool,
        )
        protected_prefix = min(2, len(result)) if start_with_images else (1 if first_shot_from_pool else 0)
        return self.fix_consecutive_repeats(result, protected_prefix=protected_prefix)

    def create_single_clip_mixed_distribution(
        self,
        images: Sequence[str],
        clips: Sequence[str],
        num_images: int,
        num_clips: int,
        total_shots: int,
        start_with_images: bool = False,
        first_shot_from_pool: bool = False,
    ) -> List[MediaItem]:
        """Alternate one reusable clip with images instead of grouping repeats."""
        image_pool = self._build_random_item_pool(images, num_images)
        clip = next(iter(dict.fromkeys(clips)))
        result: List[MediaItem] = []
        image_index = 0
        clips_left = num_clips

        prefer_image_first = start_with_images or first_shot_from_pool or num_clips <= num_images
        next_type = "image" if prefer_image_first else "video"
        last_path = None

        while len(result) < total_shots and (image_index < len(image_pool) or clips_left > 0):
            if next_type == "video" and clips_left > 0 and last_path != clip:
                result.append((clip, "video"))
                clips_left -= 1
                last_path = clip
                next_type = "image"
                continue

            if image_index < len(image_pool):
                image_path = image_pool[image_index]
                image_index += 1
                result.append((image_path, "image"))
                last_path = image_path
                next_type = "video"
                continue

            if clips_left > 0:
                result.append((clip, "video"))
                clips_left -= 1
                last_path = clip
                next_type = "image"
                continue

        return result[:total_shots]

    def create_video_first_distribution(
        self,
        images: Sequence[str],
        clips: Sequence[str],
        num_images: int,
        num_clips: int,
        total_shots: int,
        start_with_images: bool = False,
        first_shot_from_pool: bool = False,
    ) -> List[MediaItem]:
        result: List[MediaItem] = []
        image_pool = self._build_random_item_pool(images, num_images)
        clip_pool = self._build_random_clip_pool(clips, num_clips)

        image_index = 0
        clip_index = 0
        if start_with_images and image_pool:
            max_intro = min(3, num_images, len(image_pool), len(set(images)))
            intro_count = self.rng.randint(2, max_intro) if max_intro >= 2 else 1
            for _ in range(intro_count):
                result.append((image_pool[image_index], "image"))
                image_index += 1
        elif first_shot_from_pool and image_pool:
            result.append((image_pool[image_index], "image"))
            image_index += 1
        elif self.rng.choice([True, False]) and clip_pool:
            intro_count = self.rng.randint(1, min(2, num_clips, len(clip_pool), len(set(clips))))
            for _ in range(intro_count):
                result.append((clip_pool[clip_index], "video"))
                clip_index += 1
        elif image_pool:
            result.append((image_pool[image_index], "image"))
            image_index += 1

        remaining_clips = num_clips - clip_index
        remaining_total = total_shots - len(result)
        if remaining_total <= 0:
            return result[:total_shots]

        clip_positions = {
            min((index + 1) * (remaining_total // (remaining_clips + 1)), remaining_total - 1)
            for index in range(remaining_clips)
        } if remaining_clips > 0 and image_index < len(image_pool) else set()

        for position in range(remaining_total):
            if position in clip_positions and clip_index < len(clip_pool):
                result.append((clip_pool[clip_index], "video"))
                clip_index += 1
            elif image_index < len(image_pool):
                result.append((image_pool[image_index], "image"))
                image_index += 1
            elif clip_index < len(clip_pool):
                result.append((clip_pool[clip_index], "video"))
                clip_index += 1
        return result[:total_shots]

    def create_no_repeat_sequence(self, items: Sequence[str], total_needed: int, item_type: str) -> List[MediaItem]:
        if not items or total_needed <= 0:
            return []
        items = list(dict.fromkeys(str(item) for item in items if item))
        if total_needed > len(items):
            from core.smart_clip_matcher import InsufficientUniqueVisualsError
            raise InsufficientUniqueVisualsError(
                f"Нужно {total_needed} уникальных элементов типа {item_type}, "
                f"доступно {len(items)}. Повторы отключены."
            )
        result = []
        available = list(items)
        self.rng.shuffle(available)
        for index in range(total_needed):
            item = available[index]
            result.append((item, item_type))
        return result

    def create_reusable_sequence(
        self,
        items: Sequence[str],
        total_needed: int,
        item_type: str,
    ) -> List[MediaItem]:
        """Cycle a shuffled pool while avoiding adjacent repeats when possible."""
        items = list(dict.fromkeys(str(item) for item in items if item))
        if not items or total_needed <= 0:
            return []
        return [
            (item, item_type)
            for item in self._build_random_item_pool(items, int(total_needed))
        ]

    def interleave_no_repeat(
        self,
        images: Sequence[str],
        clips: Sequence[str],
        num_images: int,
        num_clips: int,
    ) -> List[MediaItem]:
        total = num_images + num_clips
        if total <= 0:
            return []

        image_pool = [images[index % len(images)] for index in range(num_images)] if images else []
        clip_pool = self.create_no_repeat_sequence(clips, num_clips, "video")
        clip_pool = [path for path, _ in clip_pool]
        self.rng.shuffle(image_pool)

        result = []
        image_index = clip_index = 0
        image_accumulator = clip_accumulator = 0
        last_type = None
        same_type_count = 0

        for _ in range(total):
            image_accumulator += num_images
            clip_accumulator += num_clips
            prefer_clip = clip_accumulator >= image_accumulator
            if last_type == "video" and same_type_count >= 2:
                prefer_clip = False
            elif last_type == "image" and same_type_count >= 2:
                prefer_clip = True

            if prefer_clip and clip_index < len(clip_pool):
                candidate, item_type = clip_pool[clip_index], "video"
                clip_index += 1
                clip_accumulator -= total
            elif image_index < len(image_pool):
                candidate, item_type = image_pool[image_index], "image"
                image_index += 1
                image_accumulator -= total
            elif clip_index < len(clip_pool):
                candidate, item_type = clip_pool[clip_index], "video"
                clip_index += 1
            else:
                break

            result.append((candidate, item_type))
            same_type_count = same_type_count + 1 if last_type == item_type else 1
            last_type = item_type
        return result

    @staticmethod
    def fix_consecutive_repeats(media_list: Sequence[MediaItem], protected_prefix: int = 0) -> List[MediaItem]:
        if len(media_list) < 2:
            return list(media_list)
        result = list(media_list)
        for _ in range(len(result) * 2):
            repeat_index = next(
                (index for index in range(1, len(result)) if result[index][0] == result[index - 1][0]),
                None,
            )
            if repeat_index is None:
                break
            swapped = False
            for candidate_index in range(len(result)):
                if candidate_index < protected_prefix or candidate_index in {repeat_index, repeat_index - 1}:
                    continue
                repeated_item = result[repeat_index]
                candidate = result[candidate_index]
                if MediaDistributionPlanner._can_place(result, repeated_item, candidate_index, repeat_index):
                    if MediaDistributionPlanner._can_place(result, candidate, repeat_index, candidate_index):
                        result[repeat_index], result[candidate_index] = candidate, repeated_item
                        swapped = True
                        break
            if not swapped:
                break
        if any(result[index][0] == result[index - 1][0] for index in range(1, len(result))):
            rebuilt = MediaDistributionPlanner._rebuild_no_repeat_suffix(result, protected_prefix)
            if MediaDistributionPlanner._repeat_count(rebuilt) <= MediaDistributionPlanner._repeat_count(result):
                return rebuilt
        return result

    @staticmethod
    def _rebuild_no_repeat_suffix(media_list: Sequence[MediaItem], protected_prefix: int = 0) -> List[MediaItem]:
        protected_prefix = max(0, min(int(protected_prefix or 0), len(media_list)))
        result = list(media_list[:protected_prefix])
        remaining = Counter(media_list[protected_prefix:])
        last_path = result[-1][0] if result else None

        while sum(remaining.values()) > 0:
            candidates = [
                item for item, count in remaining.items()
                if count > 0 and item[0] != last_path
            ]
            if not candidates:
                candidates = [item for item, count in remaining.items() if count > 0]
            if not candidates:
                break
            highest_remaining = max(remaining[item] for item in candidates)
            strongest_candidates = [
                item for item in candidates
                if remaining[item] == highest_remaining
            ]
            selected = strongest_candidates[0]
            result.append(selected)
            remaining[selected] -= 1
            last_path = selected[0]
        return result

    @staticmethod
    def _repeat_count(media_list: Sequence[MediaItem]) -> int:
        return sum(
            1
            for index in range(1, len(media_list))
            if media_list[index][0] == media_list[index - 1][0]
        )

    def _build_random_clip_pool(self, clips: Sequence[str], count: int) -> List[str]:
        return self._build_random_item_pool(clips, count)

    def _build_random_item_pool(self, items: Sequence[str], count: int) -> List[str]:
        if not items or count <= 0:
            return []
        pool_counts = Counter(items[index % len(items)] for index in range(count))
        result = []
        for _ in range(count):
            last_item = result[-1] if result else None
            candidates = [
                item for item, remaining in pool_counts.items()
                if remaining > 0 and item != last_item
            ]
            if not candidates:
                candidates = [item for item, remaining in pool_counts.items() if remaining > 0]
            if not candidates:
                break

            highest_remaining = max(pool_counts[item] for item in candidates)
            strongest_candidates = [
                item for item in candidates
                if pool_counts[item] == highest_remaining
            ]
            selected = self.rng.choice(strongest_candidates)
            result.append(selected)
            pool_counts[selected] -= 1
        return result

    @staticmethod
    def _can_place(items: Sequence[MediaItem], item: MediaItem, index: int, ignored_index: int) -> bool:
        left = items[index - 1] if index > 0 and index - 1 != ignored_index else None
        right = items[index + 1] if index + 1 < len(items) and index + 1 != ignored_index else None
        return (left is None or left[0] != item[0]) and (right is None or right[0] != item[0])
