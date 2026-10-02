#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Mixer
Main class for YouTube video downloading and processing.

This module provides the YouTubeMixer class which handles:
- Searching YouTube videos by theme
- Downloading videos with smart quality selection
- Extracting clips from videos
- Parallel processing for performance
- Caching for efficiency
"""

# Import the YouTubeMixer class from the main module
# This maintains backward compatibility while organizing the code
from core.youtube_mixer import YouTubeMixer

__all__ = ['YouTubeMixer']
