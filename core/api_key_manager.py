#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔑 API KEY MANAGER
Singleton менеджер ротации API ключей с памятью квот на сессию.

Логика:
- Один ключ: Flash → исчерпан (запомнили) → Pro → исчерпан → ОШИБКА
- Несколько ключей: Все Flash → Все Pro → ОШИБКА
- Память на всю сессию (не time-based)
"""

from typing import List, Optional, Set, Tuple, Callable
import threading


# Импортируем дефолтный логгер из централизованного модуля
from core.logging_utils import get_default_logger

# Алиас для обратной совместимости
_dummy_log = get_default_logger()


class APIKeyManager:
    """
    Singleton менеджер ротации API ключей.
    
    Запоминает исчерпанные комбинации (key, model) на всю сессию.
    Автоматически переключается на следующий доступный ключ/модель.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    # Приоритет моделей TTS (сначала дешёвые)
    MODEL_PRIORITY = ['flash', 'pro']
    
    # Маппинг коротких имён на полные имена моделей
    MODEL_NAMES = {
        # TTS модели
        'flash': 'gemini-2.5-flash-preview-tts',
        'pro': 'gemini-2.5-pro-preview-tts',
        # Image модели
        'imagen_flash': 'gemini-2.5-flash-image',
        'imagen_pro': 'gemini-3-pro-image',
    }
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        """Инициализация (вызывается один раз)"""
        self.keys: List[str] = []
        self._exhausted: Set[Tuple[str, str]] = set()  # {(key, model), ...}
        self._exhausted_lock = threading.Lock()
        self._log_callback: Callable = _dummy_log
    
    def set_keys(self, keys: List[str]):
        """
        Установить ключи (из GUI или config).
        
        Args:
            keys: Список API ключей (может быть один или несколько)
        """
        with self._exhausted_lock:
            self.keys = [k.strip() for k in keys if k and k.strip()]
            # НЕ сбрасываем exhausted - память на сессию!
    
    def set_log_callback(self, callback: Callable):
        """Установить callback для логирования"""
        self._log_callback = callback or _dummy_log
    
    def get_best_available(self, for_model_type: str = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Возвращает лучшую доступную комбинацию (key, model).
        
        Args:
            for_model_type: Если указан ('flash' или 'pro'), ищет только этот тип
            
        Returns:
            (api_key, model_short_name) или (None, None) если всё исчерпано
        """
        if not self.keys:
            return None, None
        
        with self._exhausted_lock:
            models_to_check = [for_model_type] if for_model_type else self.MODEL_PRIORITY
            
            for model in models_to_check:
                if model not in self.MODEL_PRIORITY:
                    continue
                for key in self.keys:
                    if (key, model) not in self._exhausted:
                        return key, model
        
        return None, None
    
    def is_exhausted(self, key: str, model: str) -> bool:
        """Проверяет, исчерпана ли комбинация"""
        with self._exhausted_lock:
            return (key, model) in self._exhausted
    
    def mark_exhausted(self, key: str, model: str):
        """
        Помечает комбинацию как исчерпанную на всю сессию.
        
        Args:
            key: API ключ
            model: Короткое имя модели ('flash' или 'pro')
        """
        with self._exhausted_lock:
            if (key, model) in self._exhausted:
                return  # Уже помечено
            
            self._exhausted.add((key, model))
            
            # Логируем статус
            remaining_keys = sum(1 for k in self.keys if (k, model) not in self._exhausted)
            if remaining_keys > 0:
                self._log_callback(
                    f"⚠️ Квота {model.upper()} исчерпана на текущем ключе, "
                    f"осталось {remaining_keys} ключей"
                )
            else:
                # Проверяем есть ли следующая модель
                current_idx = self.MODEL_PRIORITY.index(model) if model in self.MODEL_PRIORITY else -1
                if current_idx < len(self.MODEL_PRIORITY) - 1:
                    next_model = self.MODEL_PRIORITY[current_idx + 1]
                    self._log_callback(f"⚠️ Квота {model.upper()} исчерпана на ВСЕХ ключах, переход на {next_model.upper()}")
                else:
                    self._log_callback(f"❌ Квота {model.upper()} исчерпана на ВСЕХ ключах, нет доступных моделей!")
    
    def get_full_model_name(self, short_name: str) -> str:
        """Возвращает полное имя модели по короткому"""
        return self.MODEL_NAMES.get(short_name, short_name)
    
    def get_status(self) -> dict:
        """Возвращает текущий статус менеджера"""
        with self._exhausted_lock:
            return {
                'total_keys': len(self.keys),
                'exhausted_combinations': len(self._exhausted),
                'exhausted_list': list(self._exhausted),
                'available_flash': sum(1 for k in self.keys if (k, 'flash') not in self._exhausted),
                'available_pro': sum(1 for k in self.keys if (k, 'pro') not in self._exhausted)
            }
    
    def reset(self):
        """Сброс состояния (для новой сессии)"""
        with self._exhausted_lock:
            self._exhausted.clear()
            self._log_callback("🔄 APIKeyManager: состояние сброшено")
    
    def has_available_keys(self) -> bool:
        """Проверяет есть ли хоть одна доступная комбинация"""
        key, model = self.get_best_available()
        return key is not None


# Глобальный экземпляр (singleton)
key_manager = APIKeyManager()


def get_key_manager() -> APIKeyManager:
    """Получить глобальный экземпляр менеджера ключей"""
    return key_manager
