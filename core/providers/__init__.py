"""Lyrics provider implementations and base interfaces."""

from core.providers.base import BaseLyricsProvider
from core.providers.kugou import KugouProvider

__all__ = ["BaseLyricsProvider", "KugouProvider"]
