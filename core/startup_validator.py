"""
Startup Validator - orchestrates all startup checks and reports system status.
Main entry point for system validation.
"""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path

from core.system_profiler import SystemProfiler
from core.dependency_checker import DependencyChecker


@dataclass
class SystemStatus:
    """Complete system status after validation."""
    ready: bool
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    features_available: Dict[str, bool] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    startup_time: float = 0.0
    profile_loaded: bool = False
    dependencies_ok: bool = False


class StartupValidator:
    """Orchestrates all startup checks and reports status."""
    
    def __init__(self, verbose: bool = True, profile_path: str = ".system_profile.json"):
        """Initialize StartupValidator."""
        self.verbose = verbose
        self.profiler = SystemProfiler(profile_path)
        self.checker = DependencyChecker(verbose=verbose)
        self.status = SystemStatus(ready=False)
    
    def run_all_checks(self) -> SystemStatus:
        """Run full validation (all tests, update profile)."""
        start_time = time.time()
        
        if self.verbose:
            print("\n" + "="*60)
            print("🚀 SYSTEM STARTUP VALIDATION")
            print("="*60 + "\n")
        
        # Step 1: Load or create profile
        if self.verbose:
            print("📋 Loading system profile...")
        
        self.profiler.load_profile()
        self.status.profile_loaded = True
        
        if self.verbose:
            is_new = not self.profiler.is_profile_fresh(max_age_days=365)
            if is_new:
                print("   ✅ Created new system profile")
            else:
                print("   ✅ Loaded existing profile")
        
        # Step 2: Check dependencies
        if self.verbose:
            print("\n📦 Checking dependencies...")
        
        self.checker.check_all()
        required_ok = self.checker.check_required()
        optional_status = self.checker.check_optional()
        
        self.status.dependencies_ok = required_ok
        
        # Update profile with dependency status
        dep_updates = {
            'dependencies': {
                'required': {},
                'optional': {}
            }
        }
        
        for name, status in self.checker.results.items():
            dep_info = {
                'installed': status.installed,
                'version': status.version,
                'error': status.error
            }
            
            if name in self.checker.results:
                from core.dependency_checker import REQUIRED_DEPENDENCIES, OPTIONAL_DEPENDENCIES
                if name in REQUIRED_DEPENDENCIES:
                    dep_updates['dependencies']['required'][name] = dep_info
                elif name in OPTIONAL_DEPENDENCIES:
                    dep_updates['dependencies']['optional'][name] = dep_info
        
        self.profiler.update_profile(dep_updates)
        
        # Step 3: Check YouTube cookies
        if self.verbose:
            print("\n🍪 Checking YouTube cookies...")
        
        from core.cookies_manager import CookiesManager
        cookies_mgr = CookiesManager(verbose=self.verbose)
        cookies_valid = cookies_mgr.check_and_fix()
        
        # Step 4: Test YouTube methods (if yt-dlp available and cookies valid)
        youtube_methods = []
        if self.checker.results.get('yt-dlp', None) and self.checker.results['yt-dlp'].installed:
            if self.verbose:
                print("\n🎬 Testing YouTube download methods...")
            
            from core.youtube_method_tester import YouTubeMethodTester
            tester = YouTubeMethodTester(verbose=self.verbose)
            
            cookies_path = str(Path(__file__).parent.parent / "youtube_cookies.txt") if cookies_valid else None
            youtube_methods = tester.test_all_methods(cookies_path)
        
        # Step 5: Determine available features
        self._determine_features(optional_status, youtube_methods)
        
        # Step 6: Check for errors and warnings
        self._collect_issues(required_ok, optional_status, cookies_valid, youtube_methods)
        
        # Step 7: Generate recommendations
        self._generate_recommendations(cookies_valid, youtube_methods)
        
        # Step 8: Determine if system is ready
        self.status.ready = required_ok and len(self.status.errors) == 0
        
        # Update performance metrics
        elapsed = time.time() - start_time
        self.status.startup_time = elapsed
        
        perf_updates = {
            'performance': {
                'startup_time': elapsed
            }
        }
        self.profiler.update_profile(perf_updates)
        
        # Print status report
        if self.verbose:
            self.print_status_report(self.status)
        
        return self.status
    
    def run_quick_checks(self) -> SystemStatus:
        """Run quick validation using cached profile (dependencies only)."""
        start_time = time.time()
        
        if self.verbose:
            print("\n🚀 Quick startup check (using cached profile)...\n")
        
        # Load profile
        profile = self.profiler.load_profile()
        self.status.profile_loaded = True
        
        # Quick dependency check (required only)
        required_ok = self.checker.check_required()
        self.status.dependencies_ok = required_ok
        
        # Load features from profile
        self.status.features_available = profile.get('features', {})
        
        # Check for critical errors
        if not required_ok:
            missing = self.checker.get_missing_required()
            for dep in missing:
                self.status.errors.append(f"Missing required dependency: {dep}")
        
        self.status.ready = required_ok
        self.status.startup_time = time.time() - start_time
        
        if self.verbose and not self.status.ready:
            self.print_status_report(self.status)
        
        return self.status
    
    def _determine_features(self, optional_status: Dict[str, bool], youtube_methods: Optional[List[str]] = None) -> None:
        """Determine which features are available based on dependencies."""
        youtube_methods = youtube_methods or []
        features = {
            'youtube_download': self.checker.results.get('yt-dlp', None) and 
                              self.checker.results['yt-dlp'].installed and
                              len(youtube_methods) > 0,
            'slideshow_detection': optional_status.get('opencv', False),
            'auto_cookies_export': optional_status.get('browser-cookie3', False),
            'motion_analysis': optional_status.get('opencv', False)
        }
        
        self.status.features_available = features
        
        # Update profile
        feature_updates = {'features': features}
        self.profiler.update_profile(feature_updates)
    
    def _collect_issues(self, required_ok: bool, optional_status: Dict[str, bool], 
                       cookies_valid: bool = False, youtube_methods: Optional[List[str]] = None) -> None:
        """Collect errors and warnings."""
        youtube_methods = youtube_methods or []
        # Errors (critical issues)
        if not required_ok:
            missing = self.checker.get_missing_required()
            for dep in missing:
                self.status.errors.append(f"Missing required dependency: {dep}")
        
        # Warnings (non-critical issues)
        for name, installed in optional_status.items():
            if not installed:
                from core.dependency_checker import OPTIONAL_DEPENDENCIES
                fallback = OPTIONAL_DEPENDENCIES[name]['fallback']
                self.status.warnings.append(f"{name} not available - {fallback}")
        
        youtube_available = len(youtube_methods) > 0

        # YouTube warnings
        if self.checker.results.get('yt-dlp', None) and self.checker.results['yt-dlp'].installed:
            if not cookies_valid and not youtube_available:
                self.status.warnings.append("YouTube cookies not valid - some videos may fail")
            if not youtube_available:
                self.status.warnings.append("No working YouTube methods found - downloads may fail")
    
    def _generate_recommendations(self, cookies_valid: bool = False, youtube_methods: Optional[List[str]] = None) -> None:
        """Generate recommendations based on system status."""
        youtube_methods = youtube_methods or []
        recommendations = []
        
        # Missing dependencies
        missing = self.checker.get_missing_required()
        if missing:
            recommendations.append("Install missing dependencies:")
            for dep in missing:
                install_cmd = self.checker.get_install_instructions(dep)
                recommendations.append(f"  • {dep}: {install_cmd}")
        
        # YouTube cookies
        youtube_available = len(youtube_methods) > 0

        if not cookies_valid and not youtube_available:
            from pathlib import Path
            cookies_path = Path(__file__).parent.parent / "youtube_cookies.txt"
            if not cookies_path.exists():
                recommendations.append("YouTube cookies not found - some videos may fail to download")
                recommendations.append("  • Export cookies using browser extension (see docs)")
            else:
                recommendations.append("YouTube cookies invalid - re-export from browser")
        
        # YouTube methods
        if not youtube_available and self.checker.results.get('yt-dlp', None):
            recommendations.append("No working YouTube methods - check internet connection")
        
        # Profile age
        if not self.profiler.is_profile_fresh(max_age_days=30):
            recommendations.append("System profile is old - consider running full validation")
            recommendations.append("  • Use --reset-profile flag to re-test everything")
        
        self.status.recommendations = recommendations
    
    def print_status_report(self, status: SystemStatus) -> None:
        """Print formatted status report."""
        print("\n" + "="*60)
        print("SYSTEM STATUS REPORT")
        print("="*60)
        
        # Overall status
        if status.ready:
            print("\n✅ System Ready")
        else:
            print("\n❌ System Not Ready")
        
        # Errors
        if status.errors:
            print("\n🚨 Errors:")
            for error in status.errors:
                print(f"  • {error}")
        
        # Warnings
        if status.warnings:
            print("\n⚠️ Warnings:")
            for warning in status.warnings:
                print(f"  • {warning}")
        
        # Features
        print("\n🎯 Available Features:")
        for feature, available in status.features_available.items():
            icon = "✅" if available else "❌"
            print(f"  {icon} {feature.replace('_', ' ').title()}")
        
        # Recommendations
        if status.recommendations:
            print("\n💡 Recommendations:")
            for rec in status.recommendations:
                print(f"  {rec}")
        
        # Performance
        print(f"\n⏱️ Startup time: {status.startup_time:.2f}s")
        
        print("="*60 + "\n")
    
    def get_startup_recommendations(self) -> List[str]:
        """Get list of startup recommendations."""
        return self.status.recommendations
