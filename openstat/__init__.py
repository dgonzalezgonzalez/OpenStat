"""OpenStat independent statistical runtime."""

__version__ = "0.1.0"

from .engine import Engine, RunResult, StataError

__all__ = ["Engine", "RunResult", "StataError", "__version__"]
