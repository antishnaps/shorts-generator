#!/usr/bin/env python3
# -*- coding: utf-8 -*-

FREE_TEXT_MODEL_CHAIN = [
    'gemini-3.1-flash-lite',
    'gemini-2.5-flash-lite',
    'gemini-2.5-flash',
]

TEXT_MODEL_CHAIN = FREE_TEXT_MODEL_CHAIN.copy()

DESCRIPTION_MODEL_CHAIN = TEXT_MODEL_CHAIN.copy()

FAST_TEXT_MODEL = 'gemini-3.1-flash-lite'
STABLE_TEXT_MODEL = 'gemini-3.5-flash'
# Long-output fallback that remains available to free AI Studio keys.
PRO_TEXT_MODEL = 'gemini-2.5-flash'

IMAGE_MODEL_CHAIN = [
    'gemini-3.1-flash-image',
    'gemini-3-pro-image',
    'gemini-2.5-flash-image',
]

DEFAULT_IMAGE_MODEL = 'gemini-3.1-flash-image'
IMAGE_MODEL_BY_UI_INDEX = {
    0: 'gemini-3.1-flash-image',
    1: 'gemini-3-pro-image',
    2: 'gemini-2.5-flash-image',
}

TTS_MODEL_CHAIN = [
    'gemini-3.1-flash-tts-preview',
    'gemini-2.5-flash-preview-tts',
    'gemini-2.5-pro-preview-tts',
]

VEO_MODEL_CHAIN = [
    'veo-3.1-fast-generate-preview',
    'veo-3.1-generate-preview',
    'veo-3.1-lite-generate-preview',
]

DEFAULT_VEO_MODEL = VEO_MODEL_CHAIN[0]


def image_model_from_ui_index(index) -> str:
    try:
        index = int(index)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_MODEL
    return IMAGE_MODEL_BY_UI_INDEX.get(index, DEFAULT_IMAGE_MODEL)


def next_image_model(current_model: str):
    """Return the next valid image model in the centralized fallback chain."""
    try:
        current_index = IMAGE_MODEL_CHAIN.index(str(current_model or '').strip())
    except ValueError:
        return DEFAULT_IMAGE_MODEL

    next_index = current_index + 1
    return IMAGE_MODEL_CHAIN[next_index] if next_index < len(IMAGE_MODEL_CHAIN) else None


def generate_content_url(model_name: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
