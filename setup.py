"""Build the optional C fast path for the random-number generator.

The extension is *optional*: the package works without it, using the Python
implementation in :mod:`openstat.rng`, which produces the identical
sequence of draws.  The extension is roughly a hundred times faster and
matters for programs that generate large variables.

Two things the build has to get right:

* ``-ffp-contract=off``.  A fused multiply-add rounds differently from a
  multiply followed by an add, and the generator has to match the Python
  implementation bit for bit.
* the ziggurat tables are generated from :mod:`openstat.rng_tables` before
  the compiler runs, so the numbers have a single source of truth.
"""
from __future__ import annotations

import os
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

HERE = os.path.dirname(os.path.abspath(__file__))


def _generate_tables() -> None:
    """Write ``openstat/rngtables.h`` from the Python tables.

    The generator is loaded by path rather than as a package member, so
    that the build does not need the package's own dependencies.
    """
    import importlib.util

    path = os.path.join(HERE, "openstat", "_rngtables.py")
    spec = importlib.util.spec_from_file_location("_openstat_rngtables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


class BuildExt(build_ext):
    """Generate the table header, then build the extension."""

    def run(self):
        _generate_tables()
        super().run()

    def build_extensions(self):
        _generate_tables()
        super().build_extensions()


# -O3 without FMA contraction, so the results match the Python reference.
EXTRA_COMPILE_ARGS = ["-O3", "-ffp-contract=off"]
if sys.platform == "darwin":
    EXTRA_COMPILE_ARGS.append("-mmacosx-version-min=10.13")

_rngfast = Extension(
    "openstat._rngfast",
    sources=[os.path.join("openstat", "_rngfast.c")],
    include_dirs=[os.path.join(HERE, "openstat")],
    extra_compile_args=EXTRA_COMPILE_ARGS,
    optional=True,
)

setup(
    cmdclass={"build_ext": BuildExt},
    ext_modules=[_rngfast],
)
