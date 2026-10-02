# Core package
"""
Shorts Generator Core Module
Version: 4.0.0

All imports are done directly from submodules:
  from core.generator import ShortsGenerator
  from core.gemini_client import GeminiClient
  from core.utils import retry_with_backoff
  etc.

This __init__.py is intentionally kept minimal to speed up startup.
Heavy modules are loaded lazily on first use.
"""

__version__ = "4.0.0"
