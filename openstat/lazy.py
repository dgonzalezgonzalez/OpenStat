"""Deferred loading of the heavy scientific stack.

Importing :mod:`openstat.engine` used to pull in ``scipy.stats``,
``matplotlib`` and ``statsmodels`` whether or not the program needed them.
A do-file that only draws random numbers paid about nine seconds of import
time for libraries it never touched.

The helpers here keep those imports out of the import path.  Each one hands
back a proxy that imports the real module the first time an attribute is
read, and then replaces itself with the real module so later reads cost
nothing.

Nothing here changes what the engine computes; it only changes *when* the
import happens.
"""
from __future__ import annotations

import importlib
import sys
import types


class LazyModule(types.ModuleType):
    """A module proxy that imports the real module on first use.

    ``scipy.stats`` and ``matplotlib.figure`` cost several seconds to import
    and are needed only by a handful of commands and functions.  Wrapping them
    keeps a plain ``import openstat`` fast without changing any behaviour.
    """

    def __init__(self, name: str):
        super().__init__(name)
        # __dict__ bypasses __getattr__, so the proxy's own state cannot
        # shadow an attribute of the module it stands in for.
        self.__dict__["_lazy_name"] = name
        self.__dict__["_lazy_module"] = None

    def _load(self) -> types.ModuleType:
        module = self.__dict__["_lazy_module"]
        if module is None:
            name = self.__dict__["_lazy_name"]
            # Import under a temporary name so that a module importing
            # *this* proxy cannot see itself half-initialised.
            saved = sys.modules.pop(name, None)
            try:
                module = importlib.import_module(name)
            finally:
                if saved is not None:
                    sys.modules[name] = saved
            self.__dict__["_lazy_module"] = module
        return module

    def __getattr__(self, attribute: str):
        return getattr(self._load(), attribute)

    def __dir__(self):
        return dir(self._load())

    def __repr__(self) -> str:
        state = "loaded" if self.__dict__["_lazy_module"] is not None else "deferred"
        return f"<LazyModule {self.__dict__['_lazy_name']!r} ({state})>"


def lazy_module(name: str) -> LazyModule:
    """Return a proxy for ``name`` that imports the module on first use."""
    existing = sys.modules.get(name)
    if isinstance(existing, LazyModule):
        return existing
    proxy = LazyModule(name)
    sys.modules[name] = proxy
    return proxy
