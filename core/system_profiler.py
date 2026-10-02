"""
System Profiler - manages system profile with capabilities and settings.
Stores persistent information about what works on this specific computer.
"""

import json
import sys
import platform
import locale
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path


class SystemProfiler:
    """Manages system profile - persistent storage of system capabilities."""
    
    PROFILE_VERSION = "1.0"
    
    def __init__(self, profile_path: str = ".system_profile.json"):
        """Initialize SystemProfiler with profile path."""
        self.profile_path = Path(profile_path)
        self.profile: Optional[Dict[str, Any]] = None
    
    def load_profile(self) -> Dict[str, Any]:
        """Load profile from disk, create default if not exists."""
        if not self.profile_path.exists():
            self.profile = self.create_default_profile()
            self.save_profile(self.profile)
            return self.profile
        
        try:
            with open(self.profile_path, 'r', encoding='utf-8') as f:
                self.profile = json.load(f)
            
            # Validate version
            if self.profile.get('version') != self.PROFILE_VERSION:
                print("⚠️ Profile version mismatch, creating new profile")
                self.profile = self.create_default_profile()
                self.save_profile(self.profile)
            
            return self.profile
        
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️ Failed to load profile: {e}, creating new one")
            self.profile = self.create_default_profile()
            self.save_profile(self.profile)
            return self.profile
    
    def save_profile(self, profile: Dict[str, Any]) -> bool:
        """Save profile to disk with atomic write."""
        try:
            profile['last_updated'] = datetime.now().isoformat()
            
            # Atomic write: write to temp file, then rename
            temp_path = self.profile_path.with_suffix('.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            
            # Rename (atomic on most systems)
            temp_path.replace(self.profile_path)
            self.profile = profile
            return True
        
        except (IOError, OSError) as e:
            print(f"❌ Failed to save profile: {e}")
            return False
    
    def create_default_profile(self) -> Dict[str, Any]:
        """Create default profile with system information."""
        now = datetime.now().isoformat()
        
        # Detect system encoding
        try:
            system_encoding = sys.stdout.encoding or 'utf-8'
        except:
            system_encoding = 'utf-8'
        
        # Detect locale
        try:
            system_locale = locale.getdefaultlocale()[0] or 'en_US.UTF-8'
        except:
            system_locale = 'en_US.UTF-8'
        
        return {
            "version": self.PROFILE_VERSION,
            "created": now,
            "last_updated": now,
            "system": {
                "os": platform.system(),
                "python_version": platform.python_version(),
                "encoding": system_encoding,
                "locale": system_locale
            },
            "youtube": {
                "working_methods": [],
                "cookies_valid": False,
                "cookies_path": str(Path(__file__).parent.parent / "youtube_cookies.txt"),
                "last_test": None,
                "test_video_id": "dQw4w9WgXcQ"
            },
            "dependencies": {
                "required": {},
                "optional": {}
            },
            "features": {
                "youtube_download": False,
                "slideshow_detection": False,
                "auto_cookies_export": False,
                "motion_analysis": False
            },
            "performance": {
                "startup_time": 0.0,
                "last_youtube_test_duration": 0.0
            }
        }
    
    def update_profile(self, updates: Dict[str, Any]) -> bool:
        """Update profile with new values (deep merge)."""
        if self.profile is None:
            self.profile = self.load_profile()
        
        self._deep_merge(self.profile, updates)
        return self.save_profile(self.profile)
    
    def _deep_merge(self, target: Dict, source: Dict) -> None:
        """Deep merge source dict into target dict."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value
    
    def reset_profile(self) -> bool:
        """Reset profile to default values."""
        self.profile = self.create_default_profile()
        return self.save_profile(self.profile)
    
    def get_feature_status(self, feature: str) -> bool:
        """Get status of a specific feature."""
        if self.profile is None:
            self.profile = self.load_profile()
        
        return self.profile.get('features', {}).get(feature, False)
    
    def get_working_youtube_methods(self) -> list:
        """Get list of working YouTube download methods."""
        if self.profile is None:
            self.profile = self.load_profile()
        
        return self.profile.get('youtube', {}).get('working_methods', [])
    
    def is_profile_fresh(self, max_age_days: int = 7) -> bool:
        """Check if profile was updated recently."""
        if self.profile is None:
            self.profile = self.load_profile()
        
        last_updated = self.profile.get('last_updated')
        if not last_updated:
            return False
        
        try:
            last_update_time = datetime.fromisoformat(last_updated)
            age = datetime.now() - last_update_time
            return age.days < max_age_days
        except:
            return False
