#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
YouTube Module
Modular structure for YouTube video processing.
"""

# Constants
from .constants import (
    USER_AGENTS,
    AUTH_COOKIES,
    BLACKLIST_FILE,
    SLIDESHOW_KEYWORDS,
    SLIDESHOW_CHANNELS,
    TRANSLATION_DICTIONARY
)

# Utils
from .utils import (
    _dummy_log,
    _parse_duration,
    is_cyrillic,
    _transliterate,
    _translate_from_dictionary,
    calculate_youtube_duration
)

# Cookies
from .cookies import (
    auto_export_youtube_cookies,
    default_youtube_cookies_path,
    ensure_youtube_cookies,
    refresh_youtube_cookies,
    _get_browser_paths,
    _save_cookiejar_to_netscape,
    _try_firefox_cookies
)

from .cookie_normalizer import (
    create_isolated_cookie_snapshot,
    has_current_youtube_auth_cookies,
    is_valid_netscape_cookie_file,
    normalize_cookies_file,
)

from .proxy import (
    detect_vpn_proxy,
    get_auto_proxy,
    reset_proxy_cache
)

# Blacklist
from .blacklist import (
    load_blacklist,
    is_blacklisted,
    add_to_blacklist,
    save_blacklist
)

# Search
from .search import (
    search_youtube_api,
    _get_video_details,
    generate_smart_queries,
    generate_semantic_fallback_queries
)

# Download
from .download import (
    validate_video_file,
    repair_video_file
)

# Relevance
from .relevance import (
    calculate_relevance,
    calculate_topic_evidence,
    check_semantic_relevance_with_gemini,
    calculate_smart_video_count,
    has_meaningful_topic_evidence,
    has_specific_route_evidence
)

from .translation import translate_to_english_ai

# Mixer (main class) - REMOVED to avoid circular import
# YouTubeMixer is imported directly from core.youtube_mixer
# from .mixer import YouTubeMixer

__all__ = [
    # Constants
    'USER_AGENTS',
    'AUTH_COOKIES',
    'BLACKLIST_FILE',
    'SLIDESHOW_KEYWORDS',
    'SLIDESHOW_CHANNELS',
    'TRANSLATION_DICTIONARY',
    
    # Utils
    '_dummy_log',
    '_parse_duration',
    'is_cyrillic',
    '_transliterate',
    '_translate_from_dictionary',
    'calculate_youtube_duration',
    
    # Cookies
    'auto_export_youtube_cookies',
    'create_isolated_cookie_snapshot',
    'default_youtube_cookies_path',
    'ensure_youtube_cookies',
    'has_current_youtube_auth_cookies',
    'is_valid_netscape_cookie_file',
    'normalize_cookies_file',
    'refresh_youtube_cookies',
    '_get_browser_paths',
    '_save_cookiejar_to_netscape',
    '_try_firefox_cookies',
    'detect_vpn_proxy',
    'get_auto_proxy',
    'reset_proxy_cache',
    
    # Blacklist
    'load_blacklist',
    'is_blacklisted',
    'add_to_blacklist',
    'save_blacklist',
    
    # Search
    'search_youtube_api',
    '_get_video_details',
    'generate_smart_queries',
    'generate_semantic_fallback_queries',
    
    # Download
    'validate_video_file',
    'repair_video_file',
    
    # Relevance
    'translate_to_english_ai',
    'calculate_relevance',
    'calculate_topic_evidence',
    'check_semantic_relevance_with_gemini',
    'has_meaningful_topic_evidence',
    'has_specific_route_evidence',
    'calculate_smart_video_count',
    
    # Mixer - REMOVED to avoid circular import
    # 'YouTubeMixer',
]
