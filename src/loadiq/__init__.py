"""
LoadIQ package.

This module exposes the key entrypoints needed by downstream integrations while
keeping internal implementation details organized by concern.
"""

from .config import LoadIQConfig

__all__ = ["LoadIQConfig"]
