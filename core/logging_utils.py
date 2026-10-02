#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Logging utilities for video generator modules.

Provides default logger functions for modules that need optional logging callbacks.
"""


def get_default_logger():
    """
    Возвращает дефолтный логгер для модулей.
    
    Используется когда log_callback не передан в функцию.
    Просто выводит сообщения в консоль через print.
    
    Returns:
        Callable: Функция логирования (msg: str) -> None
        
    Example:
        >>> log = get_default_logger()
        >>> log("Hello, world!")
        Hello, world!
        
        >>> # В модуле:
        >>> from core.logging_utils import get_default_logger
        >>> def my_function(log_callback=None):
        ...     log = log_callback or get_default_logger()
        ...     log("Processing...")
    """
    return lambda msg: print(msg)
