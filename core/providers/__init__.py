"""Lyrics provider implementations and base interfaces."""

from core.providers.auto import AutoLyricsProvider
from core.providers.base import BaseLyricsProvider
from core.providers.kugou import KugouProvider
from core.providers.lrc_red import LrcRedProvider
from core.providers.unison import UnisonProvider

__all__ = [
    "AutoLyricsProvider",
    "BaseLyricsProvider",
    "KugouProvider",
    "LrcRedProvider",
    "UnisonProvider",
]
