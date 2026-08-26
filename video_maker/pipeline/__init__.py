"""Пайплайн — классы-стадии обработки видео."""
from .stages import AudioStage, TranscribeStage, GeminiStage
from .master import MasterBuilder
from .branches import FinalHorizontal, FinalVertical
from .shorts import ShortsCutter
from .finalize import FinalizeStage

__all__ = [
    "AudioStage", "TranscribeStage", "GeminiStage",
    "MasterBuilder", "FinalHorizontal", "FinalVertical",
    "ShortsCutter", "FinalizeStage",
]
