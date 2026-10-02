"""
Dependency Checker - validates all required and optional dependencies.
Provides clear error messages and installation instructions.
"""

import subprocess
import sys
import importlib
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from pathlib import Path

from core.process_registry import run_registered


@dataclass
class DependencyStatus:
    """Status of a single dependency."""
    name: str
    installed: bool
    version: Optional[str] = None
    path: Optional[str] = None
    error: Optional[str] = None


# Required dependencies - program cannot run without these
REQUIRED_DEPENDENCIES = {
    'yt-dlp': {
        'import_name': 'yt_dlp',  # Python module
        'check_method': 'import',  # Check as Python module, not CLI
        'install': 'pip install -U "yt-dlp[default]"',
        'critical': True,
        'description': 'YouTube video downloader'
    },
    'ffmpeg': {
        'import_name': None,
        'check_method': 'command',
        'command': 'ffmpeg -version',
        'install': 'Download from ffmpeg.org or use package manager',
        'critical': True,
        'description': 'Video processing tool'
    },
    'PIL': {
        'import_name': 'PIL',
        'check_method': 'import',
        'install': 'pip install Pillow',
        'critical': True,
        'description': 'Image processing library'
    },
    'requests': {
        'import_name': 'requests',
        'check_method': 'import',
        'install': 'pip install requests',
        'critical': True,
        'description': 'HTTP library'
    },
    'google-genai': {
        'import_name': 'google.genai',
        'check_method': 'import',
        'install': 'pip install google-genai',
        'critical': True,
        'description': 'Google Gemini API'
    },
    'numpy': {
        'import_name': 'numpy',
        'check_method': 'import',
        'install': 'pip install numpy',
        'critical': True,
        'description': 'Numerical computing library'
    },
    'moviepy': {
        'import_name': 'moviepy',
        'check_method': 'import',
        'install': 'pip install moviepy',
        'critical': True,
        'description': 'Video rendering library'
    },
    'json-repair': {
        'import_name': 'json_repair',
        'check_method': 'import',
        'install': 'pip install json-repair',
        'critical': True,
        'description': 'JSON recovery utility'
    }
}

# Optional dependencies - program works without these but with reduced functionality
OPTIONAL_DEPENDENCIES = {
    'opencv': {
        'import_name': 'cv2',
        'check_method': 'import',
        'install': 'pip install opencv-python',
        'feature': 'slideshow_detection',
        'fallback': 'Slideshow detection disabled',
        'description': 'Computer vision library'
    },
    'browser-cookie3': {
        'import_name': 'browser_cookie3',
        'check_method': 'import',
        'install': 'pip install browser-cookie3',
        'feature': 'auto_cookies_export',
        'fallback': 'Manual cookies export required',
        'description': 'Browser cookies extraction'
    }
}


class DependencyChecker:
    """Checks all dependencies and reports status."""
    
    def __init__(self, verbose: bool = True):
        """Initialize DependencyChecker."""
        self.verbose = verbose
        self.results: Dict[str, DependencyStatus] = {}
    
    def check_all(self) -> Dict[str, DependencyStatus]:
        """Check all dependencies (required + optional)."""
        self.results = {}
        
        # Check required
        for name, config in REQUIRED_DEPENDENCIES.items():
            status = self._check_dependency(name, config)
            self.results[name] = status
        
        # Check optional
        for name, config in OPTIONAL_DEPENDENCIES.items():
            status = self._check_dependency(name, config)
            self.results[name] = status
        
        return self.results
    
    def check_required(self) -> bool:
        """Check only required dependencies, return True if all present."""
        all_ok = True
        
        for name, config in REQUIRED_DEPENDENCIES.items():
            status = self._check_dependency(name, config)
            self.results[name] = status
            
            if not status.installed:
                all_ok = False
                if self.verbose:
                    print(f"❌ Missing required: {name} - {config['description']}")
                    print(f"   Install: {config['install']}")
        
        return all_ok
    
    def check_optional(self) -> Dict[str, bool]:
        """Check optional dependencies, return dict of availability."""
        optional_status = {}
        
        for name, config in OPTIONAL_DEPENDENCIES.items():
            status = self._check_dependency(name, config)
            self.results[name] = status
            optional_status[name] = status.installed
            
            if self.verbose:
                if status.installed:
                    print(f"✅ Optional: {name} - available")
                else:
                    print(f"⚠️ Optional: {name} - {config['fallback']}")
        
        return optional_status
    
    def _check_dependency(self, name: str, config: Dict) -> DependencyStatus:
        """Check a single dependency."""
        if name == 'yt-dlp':
            return self._check_youtube_stack()

        # Специальная обработка для ffmpeg (проверяем локальную папку)
        if name == 'ffmpeg':
            import platform
            exe_ext = '.exe' if platform.system().lower() == 'windows' else ''
            local_ffmpeg = Path('tools') / 'ffmpeg' / f'ffmpeg{exe_ext}'
            if local_ffmpeg.exists():
                return DependencyStatus(name, True, path=str(local_ffmpeg.absolute()))
        
        if config['check_method'] == 'command':
            return self._check_command(name, config)
        elif config['check_method'] == 'import':
            return self._check_import(name, config)
        else:
            return DependencyStatus(name, False, error="Unknown check method")
    
    def _check_command(self, name: str, config: Dict) -> DependencyStatus:
        """Check if command-line tool is available."""
        try:
            result = run_registered(
                config['command'].split(),
                label=f"dependency_check_{name}",
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                # Extract version from output
                version = result.stdout.split('\n')[0] if result.stdout else None
                return DependencyStatus(name, True, version=version)
            else:
                return DependencyStatus(name, False, error=result.stderr)
        
        except FileNotFoundError:
            return DependencyStatus(name, False, error="Command not found")
        except subprocess.TimeoutExpired:
            return DependencyStatus(name, False, error="Command timeout")
        except Exception as e:
            return DependencyStatus(name, False, error=str(e))

    def _check_youtube_stack(self) -> DependencyStatus:
        """Validate the complete current YouTube extractor stack.

        Importing ``yt_dlp`` alone is a false positive now: full format
        extraction also needs the matching EJS package and a supported JS
        runtime.  Surface that during startup instead of after a long batch has
        already begun.
        """
        try:
            import importlib.util
            import yt_dlp
            from core.youtube.download_policy import (
                detect_youtube_js_runtimes,
                is_supported_ytdlp_version,
            )

            version = str(yt_dlp.version.__version__)
            if not is_supported_ytdlp_version(version):
                return DependencyStatus(
                    'yt-dlp',
                    False,
                    version=version,
                    error='yt-dlp is outdated; update the default dependency group',
                )
            if importlib.util.find_spec('yt_dlp_ejs') is None:
                return DependencyStatus(
                    'yt-dlp',
                    False,
                    version=version,
                    error='matching yt-dlp-ejs solver package is missing',
                )
            if not detect_youtube_js_runtimes():
                return DependencyStatus(
                    'yt-dlp',
                    False,
                    version=version,
                    error='Deno 2.3+ or Node.js 22+ runtime is missing',
                )
            return DependencyStatus('yt-dlp', True, version=version)
        except ImportError as exc:
            return DependencyStatus('yt-dlp', False, error=str(exc))
        except Exception as exc:
            return DependencyStatus('yt-dlp', False, error=str(exc))
    
    def _check_import(self, name: str, config: Dict) -> DependencyStatus:
        """Check if Python module can be imported."""
        try:
            module = importlib.import_module(config['import_name'])
            version = getattr(module, '__version__', None)
            return DependencyStatus(name, True, version=version)
        
        except ImportError as e:
            return DependencyStatus(name, False, error=str(e))
        except Exception as e:
            return DependencyStatus(name, False, error=str(e))
    
    def get_missing_required(self) -> List[str]:
        """Get list of missing required dependencies."""
        missing = []
        for name, status in self.results.items():
            if name in REQUIRED_DEPENDENCIES and not status.installed:
                missing.append(name)
        return missing
    
    def get_install_instructions(self, package: str) -> str:
        """Get installation instructions for a package."""
        if package in REQUIRED_DEPENDENCIES:
            return REQUIRED_DEPENDENCIES[package]['install']
        elif package in OPTIONAL_DEPENDENCIES:
            return OPTIONAL_DEPENDENCIES[package]['install']
        else:
            return f"pip install {package}"
    
    def check_ffmpeg(self) -> Tuple[bool, Optional[str]]:
        """Check ffmpeg specifically with path detection."""
        # Сначала проверяем локальную папку tools/ffmpeg/
        import platform
        exe_ext = '.exe' if platform.system().lower() == 'windows' else ''
        local_ffmpeg = Path('tools') / 'ffmpeg' / f'ffmpeg{exe_ext}'
        if local_ffmpeg.exists():
            return True, str(local_ffmpeg.absolute())
        
        # Если нет локально, проверяем в PATH
        status = self._check_command('ffmpeg', REQUIRED_DEPENDENCIES['ffmpeg'])
        
        if status.installed:
            # Try to get full path
            try:
                if sys.platform == 'win32':
                    result = run_registered(
                        ['where', 'ffmpeg'],
                        label="dependency_locate_ffmpeg",
                        capture_output=True,
                        text=True,
                    )
                else:
                    result = run_registered(
                        ['which', 'ffmpeg'],
                        label="dependency_locate_ffmpeg",
                        capture_output=True,
                        text=True,
                    )
                
                path = result.stdout.strip().split('\n')[0] if result.returncode == 0 else None
                return True, path
            except:
                return True, None
        
        return False, None
    
    def check_yt_dlp(self) -> Tuple[bool, Optional[str]]:
        """Check yt-dlp specifically with version check."""
        status = self._check_youtube_stack()
        return status.installed, status.version
    
    def print_summary(self) -> None:
        """Print formatted summary of all checks."""
        if not self.results:
            self.check_all()
        
        print("\n" + "="*60)
        print("DEPENDENCY CHECK SUMMARY")
        print("="*60)
        
        # Required
        print("\n📦 Required Dependencies:")
        for name in REQUIRED_DEPENDENCIES.keys():
            status = self.results.get(name)
            if status:
                if status.installed:
                    version_str = f" ({status.version})" if status.version else ""
                    print(f"  ✅ {name}{version_str}")
                else:
                    print(f"  ❌ {name} - {status.error}")
        
        # Optional
        print("\n🔧 Optional Dependencies:")
        for name, config in OPTIONAL_DEPENDENCIES.items():
            status = self.results.get(name)
            if status:
                if status.installed:
                    version_str = f" ({status.version})" if status.version else ""
                    print(f"  ✅ {name}{version_str}")
                else:
                    print(f"  ⚠️ {name} - {config['fallback']}")
        
        print("="*60 + "\n")
