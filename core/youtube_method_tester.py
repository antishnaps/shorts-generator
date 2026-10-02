"""
YouTube Method Tester - tests different download methods and finds working ones.
Caches results in system profile for fast startup.
"""

import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from core.system_profiler import SystemProfiler
from core.process_registry import run_registered


# All YouTube download methods in priority order
YOUTUBE_METHODS = [
    {
        'id': 'cookies_tv',
        'name': 'TV Client with Cookies',
        'priority': 1,
        'requires_cookies': True,
        'yt_dlp_args': ['--cookies', '{cookies_path}', '--extractor-args', 'youtube:player_client=tv']
    },
    {
        'id': 'cookies_android',
        'name': 'Android Client with Cookies',
        'priority': 2,
        'requires_cookies': True,
        'yt_dlp_args': ['--cookies', '{cookies_path}', '--extractor-args', 'youtube:player_client=android']
    },
    {
        'id': 'cookies_ios',
        'name': 'iOS Client with Cookies',
        'priority': 3,
        'requires_cookies': True,
        'yt_dlp_args': ['--cookies', '{cookies_path}', '--extractor-args', 'youtube:player_client=ios']
    },
    {
        'id': 'chrome_direct_tv',
        'name': 'Chrome Direct + TV',
        'priority': 4,
        'requires_cookies': False,
        'yt_dlp_args': ['--extractor-args', 'youtube:player_client=tv', '--user-agent', 'Mozilla/5.0']
    },
    {
        'id': 'tv_no_cookies',
        'name': 'TV Client (No Cookies)',
        'priority': 5,
        'requires_cookies': False,
        'yt_dlp_args': ['--extractor-args', 'youtube:player_client=tv']
    },
    {
        'id': 'android_no_cookies',
        'name': 'Android Client (No Cookies)',
        'priority': 6,
        'requires_cookies': False,
        'yt_dlp_args': ['--extractor-args', 'youtube:player_client=android']
    },
    {
        'id': 'default',
        'name': 'Default (No Special Args)',
        'priority': 7,
        'requires_cookies': False,
        'yt_dlp_args': []
    }
]


def _get_ytdlp_bin() -> str:
    """Finds the yt-dlp executable, checking virtual environment first."""
    import sys
    from pathlib import Path
    
    prefix_path = Path(sys.prefix)
    if sys.platform == 'win32':
        venv_bin = prefix_path / 'Scripts' / 'yt-dlp.exe'
    else:
        venv_bin = prefix_path / 'bin' / 'yt-dlp'
        
    if venv_bin.exists():
        return str(venv_bin)
        
    # Check adjacent directory for local venv
    local_venv_bin = Path(__file__).parent.parent / 'venv' / ('Scripts' if sys.platform == 'win32' else 'bin') / ('yt-dlp.exe' if sys.platform == 'win32' else 'yt-dlp')
    if local_venv_bin.exists():
        return str(local_venv_bin)
        
    return 'yt-dlp'


class YouTubeMethodTester:
    """Tests YouTube download methods and finds working ones."""
    
    def __init__(self, test_video_id: str = "dQw4w9WgXcQ", verbose: bool = True):
        """
        Initialize YouTubeMethodTester.
        
        Args:
            test_video_id: YouTube video ID to use for testing (default: Rick Astley)
            verbose: Print progress messages
        """
        self.test_video_id = test_video_id
        self.test_url = f"https://www.youtube.com/watch?v={test_video_id}"
        self.verbose = verbose
        self.profiler = SystemProfiler()
    
    def test_all_methods(self, cookies_path: Optional[str] = None) -> List[str]:
        """
        Test all methods and return list of working method IDs.
        
        Args:
            cookies_path: Path to cookies file (optional)
        
        Returns:
            List of working method IDs in priority order
        """
        start_time = time.time()
        working_methods = []
        
        if self.verbose:
            print("\n" + "="*60)
            print("🧪 TESTING YOUTUBE DOWNLOAD METHODS")
            print("="*60)
            print(f"Test video: {self.test_url}")
            if cookies_path:
                print(f"Cookies: {cookies_path}")
            print()
        
        # Check if cookies exist
        cookies_exist = cookies_path and Path(cookies_path).exists()
        
        for method in YOUTUBE_METHODS:
            # Skip methods that require cookies if we don't have them
            if method['requires_cookies'] and not cookies_exist:
                if self.verbose:
                    print(f"⏭️ {method['name']}: Skipped (no cookies)")
                continue
            
            if self.verbose:
                print(f"🔄 Testing: {method['name']}...", end=' ', flush=True)
            
            # Try quick test first (metadata only)
            success = self.quick_test(method, cookies_path)
            
            if success:
                working_methods.append(method['id'])
                if self.verbose:
                    print("✅ Works!")
            else:
                if self.verbose:
                    print("❌ Failed")
        
        elapsed = time.time() - start_time
        
        if self.verbose:
            print("\n" + "="*60)
            print(f"✅ Found {len(working_methods)} working methods in {elapsed:.1f}s")
            if working_methods:
                print("Working methods:")
                for method_id in working_methods:
                    method = next(m for m in YOUTUBE_METHODS if m['id'] == method_id)
                    print(f"  • {method['name']}")
            print("="*60 + "\n")
        
        # Update profile
        self.update_profile_with_results(working_methods, elapsed)
        
        return working_methods
    
    def quick_test(self, method: Dict, cookies_path: Optional[str] = None) -> bool:
        """
        Quick test - only fetch metadata, don't download video.
        
        Args:
            method: Method configuration dict
            cookies_path: Path to cookies file
        
        Returns:
            True if method works, False otherwise
        """
        try:
            # Build command
            import sys
            ytdlp_bin = _get_ytdlp_bin()
            cmd = [ytdlp_bin, '--skip-download', '--no-warnings', '--quiet', '--no-check-certificate']
            
            # Add method-specific args
            for arg in method['yt_dlp_args']:
                if '{cookies_path}' in arg and cookies_path:
                    cmd.append(arg.replace('{cookies_path}', cookies_path))
                else:
                    cmd.append(arg)
            
            # Add URL
            cmd.append(self.test_url)
            
            # Run with timeout and hidden window on Windows
            creation_flags = 0x08000000 if sys.platform == 'win32' else 0
            result = run_registered(
                cmd,
                label="youtube_method_probe",
                capture_output=True,
                text=True,
                timeout=10,  # Reduced from 15 to 10
                creationflags=creation_flags
            )
            
            # Success if exit code is 0
            return result.returncode == 0
        
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
    
    def test_method(self, method: Dict, cookies_path: Optional[str] = None) -> bool:
        """
        Full test - download a short clip to verify method works.
        
        Args:
            method: Method configuration dict
            cookies_path: Path to cookies file
        
        Returns:
            True if method works, False otherwise
        """
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_template = str(Path(tmpdir) / 'test.%(ext)s')
                
                # Build command
                import sys
                ytdlp_bin = _get_ytdlp_bin()
                cmd = [
                    ytdlp_bin,
                    '--no-warnings',
                    '--quiet',
                    '--format', 'worst',  # Download worst quality for speed
                    '--download-sections', '*0-5',  # Only first 5 seconds
                    '-o', output_template
                ]
                
                # Add method-specific args
                for arg in method['yt_dlp_args']:
                    if '{cookies_path}' in arg and cookies_path:
                        cmd.append(arg.replace('{cookies_path}', cookies_path))
                    else:
                        cmd.append(arg)
                
                # Add URL
                cmd.append(self.test_url)
                
                # Run with timeout and hidden window on Windows
                creation_flags = 0x08000000 if sys.platform == 'win32' else 0
                result = run_registered(
                    cmd,
                    label="youtube_method_download_probe",
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=creation_flags
                )
                
                # Check if file was downloaded
                if result.returncode == 0:
                    downloaded_files = list(Path(tmpdir).glob('test.*'))
                    return len(downloaded_files) > 0
                
                return False
        
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
    
    def get_recommended_methods(self) -> List[str]:
        """
        Get recommended methods from profile or test if needed.
        
        Returns:
            List of recommended method IDs
        """
        profile = self.profiler.load_profile()
        working_methods = profile.get('youtube', {}).get('working_methods', [])
        
        # If no methods cached or profile is old, re-test
        if not working_methods or not self.profiler.is_profile_fresh(max_age_days=30):
            cookies_path = profile.get('youtube', {}).get('cookies_path', 'youtube_cookies.txt')
            working_methods = self.test_all_methods(cookies_path)
        
        return working_methods
    
    def update_profile_with_results(self, working_methods: List[str], test_duration: float) -> None:
        """
        Update system profile with test results.
        
        Args:
            working_methods: List of working method IDs
            test_duration: Time taken to test (seconds)
        """
        updates = {
            'youtube': {
                'working_methods': working_methods,
                'last_test': datetime.now().isoformat()
            },
            'performance': {
                'last_youtube_test_duration': test_duration
            },
            'features': {
                'youtube_download': len(working_methods) > 0
            }
        }
        
        self.profiler.update_profile(updates)
