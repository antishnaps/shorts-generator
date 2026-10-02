#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
NVENC Session Manager
Потокобезопасный менеджер NVENC сессий для GPU кодирования

Потребительские GPU NVIDIA имеют лимит ~3 одновременных сессии кодирования.
Этот модуль обеспечивает потокобезопасный контроль количества сессий.
"""

import threading


class NVENCSessionManager:
    """
    Потокобезопасный менеджер NVENC сессий (Singleton pattern).
    
    Потребительские GPU NVIDIA имеют лимит ~3 одновременных сессии кодирования.
    Этот класс обеспечивает потокобезопасный контроль количества сессий.
    
    Example:
        >>> manager = NVENCSessionManager()
        >>> if manager.acquire(timeout=60):
        ...     try:
        ...         # Выполняем кодирование с NVENC
        ...         pass
        ...     finally:
        ...         manager.release()
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_singleton()
        return cls._instance
    
    def _init_singleton(self):
        """Инициализация singleton."""
        self._semaphore = threading.Semaphore(3)  # Максимум 3 сессии
        self._sessions_lock = threading.Lock()
        self._active_sessions = 0
    
    def acquire(self, timeout: float = 60.0) -> bool:
        """
        Захватывает NVENC сессию.
        
        Args:
            timeout: Таймаут ожидания в секундах
            
        Returns:
            True если сессия захвачена, False при таймауте
        """
        acquired = self._semaphore.acquire(timeout=timeout)
        if acquired:
            with self._sessions_lock:
                self._active_sessions += 1
        return acquired
    
    def release(self) -> None:
        """Освобождает NVENC сессию."""
        with self._sessions_lock:
            if self._active_sessions > 0:
                self._active_sessions -= 1
        self._semaphore.release()
    
    @property
    def active_sessions(self) -> int:
        """Возвращает количество активных сессий."""
        with self._sessions_lock:
            return self._active_sessions


# Глобальный экземпляр менеджера
nvenc_manager = NVENCSessionManager()


# ============================================================
# Обратная совместимость со старым API
# ============================================================
_nvenc_manager = nvenc_manager
_NVENC_SEMAPHORE = nvenc_manager._semaphore
_NVENC_LOCK = threading.Lock()
_NVENC_SESSIONS_LOCK = nvenc_manager._sessions_lock
