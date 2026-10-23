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

A missing C compiler is **not** a build failure.  Many machines -- Windows
build agents in particular -- have no compiler at all.  There the extension
is skipped with a warning, the package installs, and the pure-Python RNG in
:mod:`openstat.rng` is used.
"""
from __future__ import annotations

import os
import shutil
import subprocess
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


def _find_msvc_compiler() -> str | None:
    """Locate ``cl.exe`` on ``PATH`` or inside a Visual Studio install."""
    found = shutil.which("cl.exe")
    if found:
        return found
    vswhere = None
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        candidate = os.path.join(base, "Microsoft Visual Studio", "Installer", "vswhere.exe")
        if os.path.isfile(candidate):
            vswhere = candidate
            break
    if vswhere is None:
        return None
    try:
        completed = subprocess.run(
            [vswhere, "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    installation = (completed.stdout or "").strip()
    if not installation:
        return None
    tools = os.path.join(installation, "VC", "Tools", "MSVC")
    if not os.path.isdir(tools):
        return None
    for version in sorted(os.listdir(tools), reverse=True):
        for host, target in (("Hostx64", "x64"), ("Hostx86", "x86"), ("Hostx64", "arm64")):
            compiler = os.path.join(tools, version, "bin", host, target, "cl.exe")
            if os.path.isfile(compiler):
                return compiler
    return None


def find_c_compiler() -> str | None:
    """The C compiler to build with, or ``None`` when the machine has none."""
    if sys.platform == "win32":
        return _find_msvc_compiler()
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def compile_args() -> list[str]:
    """Optimisation and floating-point flags for the host compiler."""
    if sys.platform == "win32":
        # MSVC does not contract a*b+c into an FMA under /fp:precise, so the
        # generator still matches the Python reference bit for bit.
        return ["/O2", "/fp:precise"]
    # -O3 without FMA contraction, so the results match the Python reference.
    args = ["-O3", "-ffp-contract=off"]
    if sys.platform == "darwin":
        args.append("-mmacosx-version-min=10.13")
    return args


class BuildExt(build_ext):
    """Generate the table header, then build the extension.

    Every failure here is a *skip*, not an error: the package has to install
    and work with the pure-Python RNG when the toolchain is missing.
    """

    def run(self):
        if not self._tables_ready():
            return
        try:
            super().run()
        except Exception as error:  # a missing or broken toolchain is a skip
            self.warn(f"skipping the optional C accelerator: {error}")

    def build_extensions(self):
        if not self._tables_ready():
            return
        try:
            super().build_extensions()
        except Exception as error:  # a missing or broken toolchain is a skip
            self.warn(f"skipping the optional C accelerator: {error}")

    def build_extension(self, ext):
        try:
            super().build_extension(ext)
        except Exception as error:  # a missing or broken toolchain is a skip
            self.warn(f"skipping the optional C accelerator {ext.name}: {error}")

    def _tables_ready(self) -> bool:
        try:
            _generate_tables()
        except Exception as error:
            self.warn(f"skipping the optional C accelerator: {error}")
            return False
        return True


COMPILER = find_c_compiler()

_rngfast = Extension(
    "openstat._rngfast",
    sources=[os.path.join("openstat", "_rngfast.c")],
    include_dirs=[os.path.join(HERE, "openstat")],
    extra_compile_args=compile_args(),
    optional=True,
)

if COMPILER is None:
    print("openstat: no C compiler found -- the optional _rngfast accelerator is "
          "skipped and the pure-Python RNG in openstat/rng.py is used instead.")

setup(
    cmdclass={"build_ext": BuildExt},
    ext_modules=[] if COMPILER is None else [_rngfast],
)