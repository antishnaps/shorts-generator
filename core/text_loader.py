#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Text Loader for Custom Text Mode
Loads and validates user-provided texts for video generation
"""

import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class TextFile:
    """Represents a loaded text file"""
    filename: str
    filepath: Path
    title: str
    text: str
    duration: int  # seconds
    valid: bool
    error: Optional[str] = None
    is_fallback_title: bool = False
    remake_source_video: Optional[str] = None
    first_shot_image_path: Optional[str] = None


class TextLoader:
    """Loads and processes custom text files for video generation"""
    
    # Validation constants
    MIN_TEXT_LENGTH = 50
    MAX_TEXT_LENGTH = 500000  # 500k символов (~5-6 часов видео)
    WORDS_PER_MINUTE = 150  # Average TTS speed for Russian
    DURATION_BUFFER = 2  # Extra seconds for intro/outro
    
    def __init__(self):
        """Initialize TextLoader"""
        pass
    
    def scan_folder(self, folder: Path) -> List[TextFile]:
        """
        Scan folder for text files and load them
        
        Args:
            folder: Path to folder containing text files
            
        Returns:
            List of TextFile objects (sorted by filename)
        """
        if not folder.exists():
            raise FileNotFoundError(f"Folder not found: {folder}")
        
        if not folder.is_dir():
            raise NotADirectoryError(f"Not a directory: {folder}")
        
        # Find all .txt files
        txt_files = sorted(folder.glob("*.txt"))
        
        if not txt_files:
            return []
        
        # Load each file
        results = []
        for filepath in txt_files:
            text_file = self.load_text(filepath)
            results.append(text_file)
        
        return results
    
    def load_text(self, filepath: Path) -> TextFile:
        """
        Load and parse a single text file
        
        Args:
            filepath: Path to text file
            
        Returns:
            TextFile object with parsed data
        """
        filename = filepath.name
        
        try:
            # Read file with UTF-8 encoding
            with open(filepath, 'rb') as f:
                raw_content = f.read()
            
            # Remove BOM if present
            if raw_content.startswith(b'\xef\xbb\xbf'):
                raw_content = raw_content[3:]
            
            # Decode to string
            content = raw_content.decode('utf-8').strip()
            
            # Parse format
            title, text, is_fallback = self.parse_format(content)
            
            # Validate text
            valid, error = self.validate_text(text)
            
            # Calculate duration
            duration = self.calculate_duration(text) if valid else 0
            
            return TextFile(
                filename=filename,
                filepath=filepath,
                title=title,
                text=text,
                duration=duration,
                valid=valid,
                error=error,
                is_fallback_title=is_fallback
            )
            
        except Exception as e:
            # Return invalid TextFile on error
            return TextFile(
                filename=filename,
                filepath=filepath,
                title="",
                text="",
                duration=0,
                valid=False,
                error=f"Failed to load file: {str(e)}"
            )
    
    def parse_format(self, content: str) -> Tuple[str, str, bool]:
        """
        Parse text file format
        
        Supports two formats:
        1. With headers: "Заголовок: ...\nТекст: ..."
        2. Plain text: uses first 50 chars as title
        
        Args:
            content: File content
            
        Returns:
            Tuple of (title, text, is_fallback_title)
        """
        # Try to parse format with headers
        title_match = re.search(r'^Заголовок:\s*(.+?)$', content, re.MULTILINE | re.IGNORECASE)
        
        # Ищем "Текст:" и берём всё после него до конца файла
        text_match = re.search(r'^Текст:\s*(.+)', content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
        
        if title_match and text_match:
            # Format 1: With headers
            title = title_match.group(1).strip()
            # Берём весь текст после "Текст:" (group(1) содержит всё до конца благодаря DOTALL)
            text = text_match.group(1).strip()
            return title, text, False
        
        # Format 2: Plain text (no headers)
        # Use first 50 chars as title
        title = content[:50].strip()
        if len(title) == 50 and len(content) > 50:
            title += "..."
        # Убираем префикс (N) из названия (файлы часто нумерованные)
        title = re.sub(r'^\(\d+\)\s*', '', title)
        
        text = content.strip()
        
        return title, text, True
    
    def validate_text(self, text: str) -> Tuple[bool, Optional[str]]:
        """
        Validate text content
        
        Args:
            text: Text to validate
            
        Returns:
            Tuple of (valid: bool, error: Optional[str])
        """
        if not text:
            return False, "Text is empty"
        
        text_length = len(text)
        
        if text_length < self.MIN_TEXT_LENGTH:
            return False, f"Text too short ({text_length} chars, min {self.MIN_TEXT_LENGTH})"
        
        if text_length > self.MAX_TEXT_LENGTH:
            return False, f"Text too long ({text_length} chars, max {self.MAX_TEXT_LENGTH})"
        
        # Check if text contains only whitespace
        if not text.strip():
            return False, "Text contains only whitespace"
        
        return True, None
    
    def calculate_duration(self, text: str) -> int:
        """
        Calculate estimated video duration from text length
        
        Formula: (words / WORDS_PER_MINUTE) * 60 + DURATION_BUFFER
        
        Args:
            text: Text content
            
        Returns:
            Duration in seconds
        """
        # Count words (split by whitespace)
        words = len(text.split())
        
        # Calculate TTS duration
        tts_duration = (words / self.WORDS_PER_MINUTE) * 60
        
        # Add buffer for intro/outro
        total_duration = int(tts_duration + self.DURATION_BUFFER)
        
        # Minimum 5 seconds
        return max(5, total_duration)
    
    def get_summary(self, text_files: List[TextFile]) -> Dict[str, int]:
        """
        Get summary statistics for loaded texts
        
        Args:
            text_files: List of TextFile objects
            
        Returns:
            Dict with statistics
        """
        total = len(text_files)
        valid = sum(1 for tf in text_files if tf.valid)
        invalid = total - valid
        
        return {
            'total': total,
            'valid': valid,
            'invalid': invalid
        }
    
    def get_valid_texts(self, text_files: List[TextFile]) -> List[TextFile]:
        """
        Filter only valid text files
        
        Args:
            text_files: List of TextFile objects
            
        Returns:
            List of valid TextFile objects
        """
        return [tf for tf in text_files if tf.valid]
    
    def get_invalid_texts(self, text_files: List[TextFile]) -> List[TextFile]:
        """
        Filter only invalid text files
        
        Args:
            text_files: List of TextFile objects
            
        Returns:
            List of invalid TextFile objects
        """
        return [tf for tf in text_files if not tf.valid]


# Example usage
if __name__ == "__main__":
    # Test the loader
    loader = TextLoader()
    
    # Create test folder
    test_folder = Path("custom_texts")
    if test_folder.exists():
        texts = loader.scan_folder(test_folder)
        
        print(f"Loaded {len(texts)} text files:")
        for tf in texts:
            status = "✅" if tf.valid else "❌"
            print(f"{status} {tf.filename}: {tf.title[:50]}... ({tf.duration}s)")
            if not tf.valid:
                print(f"   Error: {tf.error}")
    else:
        print(f"Test folder not found: {test_folder}")
        print("\nCreate test files:")
        print("mkdir custom_texts")
        print('echo "Заголовок: Test\\nТекст: This is a test text with enough words to pass validation." > custom_texts/001.txt')
