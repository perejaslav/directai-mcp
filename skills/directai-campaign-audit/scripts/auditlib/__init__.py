"""Внутренняя библиотека аудита DirectAI Campaign Audit v2.0.

Только стандартная библиотека (+ openpyxl для Excel). Сетевых вызовов нет.
"""
from .model import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
