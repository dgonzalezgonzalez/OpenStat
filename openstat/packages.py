"""Descriptor-driven SSC/net acquisition; installation never executes payloads.

Upstream licenses remain in force. SHA-256 records bytes, not authenticity of
an HTTP archive. Local and HTTPS sources preserve normal TLS validation.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


class PackageError(ValueError):
    pass


MANIFEST = "openstat-package.json"
# Runtime support files. Other suffixes are ancillary unless F/G forces them
# into the installation. Compiled payloads are stored as data, never loaded.
SYSTEM_SUFFIXES = frozenset({
    ".ado", ".hlp", ".sthlp", ".ihlp", ".dlg", ".idlg", ".class",
    ".mata", ".mo", ".mlib", ".plugin", ".dll", ".so", ".dylib",
    ".scheme", ".style", ".def", ".maint", ".mnu", ".key", ".toc",
    ".py", ".jar",
})


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_limit() -> int:
    try:
        limit = int(os.environ.get("OPENSTAT_PACKAGE_MAX_BYTES", 512 * 1024 * 1024))
    except ValueError as exc:
        raise PackageError("OPENSTAT_PACKAGE_MAX_BYTES must be an integer") from exc
    if limit < 1:
        raise PackageError("OPENSTAT_PACKAGE_MAX_BYTES must be positive")
    return limit


def download(url: str) -> bytes:
    """Acquire HTTP(S) bytes without importing or executing the result."""
    if urlparse(url).scheme not in {"https", "http"}:
        raise PackageError("package URLs must use HTTP or HTTPS")
    request = Request(url, headers={"User-Agent": "OpenStat/0.1 (independent statistical workbench)"})
    limit = _read_limit()
    with urlopen(request, timeout=30) as response:
        if urlparse(response.geturl()).scheme not in {"https", "http"}:
            raise PackageError("unsupported package redirect")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise PackageError("package file exceeds OPENSTAT_PACKAGE_MAX_BYTES")
    return data


def _host_platform() -> str:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        return "WIN64" if machine in {"amd64", "x86_64", "arm64", "aarch64"} else "WIN"
    if system == "Darwin":
        return "MACARM64" if machine in {"arm64", "aarch64"} else "MACINTEL64"
    return "LINUX64P" if machine in {"amd64", "x86_64", "arm64", "aarch64"} else "LINUX"


def _name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) or name in {".", ".."}:
        raise PackageError("invalid package name")
    if name != _filename(name):
        raise PackageError("invalid package name")
    return name


def _relative(value: str) -> str:
    """Validate remote references independently of their flat local name."""
    decoded = unquote(value)
    parsed, path = urlparse(decoded), PurePosixPath(decoded)
    if (not decoded or parsed.scheme or parsed.netloc or path.is_absolute()
            or any(c in decoded for c in "\\:?\x00#")
            or any(ord(c) < 32 for c in decoded) or decoded.endswith("/")):
        raise PackageError("unsafe package path")
    return value


def _filename(value: str) -> str:
    leaf = PurePosixPath(unquote(_relative(value))).name
    stem = leaf.split(".", 1)[0].upper()
    if (leaf in {"", ".", "..", MANIFEST} or leaf.endswith((".", " "))
            or any(c in leaf for c in '<>"|*')
            or stem in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", stem)):
        raise PackageError("unsafe package destination")
    return leaf


class PackageManager:
    def __init__(self, root: Path | None = None, *, platform_name: str | None = None):
        self.root = Path(root or os.environ.get("OPENSTAT_ADO", Path.home() / ".openstat" / "ado"))
        self.platform_name = (platform_name or _host_platform()).upper()

    def _source(self, name: str, source: str | None):
        _name(name)
        if source is None:
            letter = name[0].lower() if name[0].isalpha() else "_"
            source = f"http://fmwww.bc.edu/repec/bocode/{letter}/"
        source = str(source)
        if urlparse(source).scheme in {"http", "https"}:
            base = source.rstrip("/") + "/"
            return base, urljoin(base, name + ".pkg")
        if "://" in source:
            raise PackageError("package source must be HTTP(S) or a local directory")
        base = Path(source).expanduser().resolve()
        return base, str(base / (name + ".pkg"))

    def _acquire(self, base, reference: str) -> bytes:
        _relative(reference)
        if isinstance(base, Path):
            path = (base / unquote(reference)).resolve()
            if not path.is_relative_to(base):
                raise PackageError("local package path leaves source directory")
            if path.stat().st_size > _read_limit():
                raise PackageError("package file exceeds OPENSTAT_PACKAGE_MAX_BYTES")
            return path.read_bytes()
        remote = urljoin(base, reference)
        if urlparse(remote)[:2] != urlparse(base)[:2]:
            raise PackageError("package file changes source origin")
        return download(remote)

    def describe(self, name: str, source: str | None = None) -> dict:
        """Parse a v1/v2/v3 descriptor and select current-platform files."""
        base, location = self._source(name, source)
        descriptor = self._acquire(base, name + ".pkg")
        text = descriptor.decode("utf-8-sig", errors="replace")
        description, candidates, required, ignored = [], [], [], []
        version = None
        for lineno, raw in enumerate(text.splitlines(), 1):
            raw = raw.strip()
            if not raw or raw.startswith("*"):
                continue
            if raw == "OFF":
                raise PackageError("package source is temporarily unavailable")
            fields = raw.split(None, 1)
            directive, body = fields[0], fields[1] if len(fields) > 1 else ""
            if directive == "e":
                break
            if directive == "v":
                if body not in {"1", "2", "3"}:
                    raise PackageError(f"unsupported package descriptor version: {body}")
                version = int(body)
            elif directive == "d":
                description.append(body)
            elif directive in {"f", "F", "g", "G", "h"}:
                try:
                    lexer = shlex.shlex(body, posix=True)
                    lexer.whitespace_split = True
                    lexer.commenters = ""
                    lexer.escape = ""
                    # Descriptions after f/F filenames are arbitrary text and
                    # must not be parsed as shell-like quoting or extra files.
                    if directive in {"f", "F", "h"}:
                        word = lexer.get_token()
                        words = [word] if word else []
                    else:
                        words = list(lexer)
                except ValueError as exc:
                    raise PackageError(f"invalid package record at line {lineno}") from exc
                if not words or (directive in {"g", "G"} and len(words) < 2):
                    raise PackageError(f"missing package filename at line {lineno}")
                if directive == "h":
                    required.append(_filename(words[0]))
                    continue
                platform_id = words.pop(0).upper() if directive in {"g", "G"} else None
                reference = _relative(words[0])
                destination = _filename(words[1] if platform_id and len(words) > 1 else words[0])
                candidates.append({"source": reference, "destination": destination,
                    "directive": directive, "platform": platform_id,
                    "ancillary": directive not in {"F", "G"} and PurePosixPath(destination).suffix.lower() not in SYSTEM_SUFFIXES})
            else:
                ignored.append({"line": lineno, "directive": directive})
        if not candidates:
            raise PackageError("empty package manifest")
        aliases = {"WIN64": {"WIN64", "WIN64A"}, "WIN64A": {"WIN64", "WIN64A"}}
        accepted = aliases.get(self.platform_name, {self.platform_name})
        exact = {r["destination"].casefold() for r in candidates if r["platform"] in accepted}
        selected = {}
        for record in candidates:
            pid, dest = record["platform"], record["destination"].casefold()
            fallback = self.platform_name == "LINUX64P" and pid == "LINUX64" and dest not in exact
            if pid is not None and pid not in accepted and not fallback:
                continue
            prior = selected.get(dest)
            if prior is not None:
                if prior["source"] != record["source"] or prior["ancillary"] != record["ancillary"]:
                    raise PackageError(f"conflicting package destination: {record['destination']}")
                continue
            selected[dest] = record
        for filename in required:
            if filename.casefold() not in selected:
                raise PackageError(f"package unavailable for {self.platform_name}: required file {filename}")
        return {"name": name, "source": location, "descriptor_sha256": _digest(descriptor),
            "descriptor_version": version, "platform": self.platform_name,
            "title": description[0] if description else name, "description": description,
            "files": list(selected.values()), "required": required, "ignored_directives": ignored}

    def _target(self, name: str) -> Path:
        target = self.root / _name(name)
        if self.root.is_symlink() or target.is_symlink():
            raise PackageError("package install directory must not be a symlink")
        if target.exists() and not target.is_dir():
            raise PackageError("package install target is not a directory")
        return target

    @staticmethod
    def _manifest(target: Path) -> dict:
        try:
            result = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("files"), dict):
                raise ValueError("missing ownership list")
            for filename, digest in result["files"].items():
                if filename != _filename(filename) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("invalid ownership record")
            return result
        except (OSError, ValueError, TypeError) as exc:
            raise PackageError("invalid package ownership manifest") from exc

    def install(self, name: str, source: str | None = None, replace: bool = False,
                all_files: bool = False, ancillary_dir: Path | None = None) -> dict:
        target = self._target(name)
        if target.exists() and not replace:
            raise PackageError(f"{name} already installed; specify replace")
        old = self._manifest(target) if target.exists() else {"files": {}}
        if target.exists() and any(p.is_symlink() for p in target.rglob("*")):
            raise PackageError("package contains symlinks; replacement refused")
        parsed = self.describe(name, source)
        base, _ = self._source(name, source)
        records = [r for r in parsed["files"] if not r["ancillary"] or all_files]
        contents = {r["destination"]: self._acquire(base, r["source"]) for r in records}
        system = [r for r in records if not r["ancillary"]]
        ancillary = [r for r in records if r["ancillary"]]
        other = Path(ancillary_dir or Path.cwd()).resolve()
        for record in ancillary:
            dest = other / record["destination"]
            if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise PackageError("unsafe ancillary destination")
            if dest.exists() and not replace:
                raise PackageError(f"ancillary file {dest.name} already exists; specify replace")
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = {k: v for k, v in parsed.items() if k != "files"}
        manifest.update({"files": {r["destination"]: _digest(contents[r["destination"]]) for r in system},
            "file_sources": {r["destination"]: r["source"] for r in system},
            "ancillary": [{"name": r["destination"], "sha256": _digest(contents[r["destination"]])} for r in ancillary],
            "downloaded_bytes": sum(map(len, contents.values())),
            "execution_compatibility": "unverified; installation does not execute or load payloads",
            "license": "upstream; not relicensed by OpenStat"})
        with tempfile.TemporaryDirectory(prefix=".openstat-package-", dir=self.root) as work:
            stage, backup = Path(work) / "package", Path(work) / "previous"
            if target.exists():
                shutil.copytree(target, stage)
                for filename, checksum in old["files"].items():
                    path = stage / filename
                    if path.is_file() and _digest(path.read_bytes()) == checksum:
                        path.unlink()
            else:
                stage.mkdir()
            for record in system:
                filename = record["destination"]
                path = stage / filename
                if path.exists() and filename not in old["files"]:
                    raise PackageError(f"unowned file would be overwritten: {filename}")
                path.write_bytes(contents[filename])
            (stage / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            pending, committed = [], []
            moved_old, moved_new = False, False
            try:
                if ancillary:
                    other.mkdir(parents=True, exist_ok=True)
                for record in ancillary:
                    dest = other / record["destination"]
                    fd, tmp = tempfile.mkstemp(prefix=".openstat-", dir=other)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(contents[record["destination"]])
                    pending.append((Path(tmp), dest, dest.read_bytes() if dest.exists() else None))
                if target.exists():
                    target.rename(backup)
                    moved_old = True
                stage.rename(target)
                moved_new = True
                for tmp, dest, previous in pending:
                    os.replace(tmp, dest)
                    committed.append((dest, previous))
            except BaseException:
                for dest, previous in reversed(committed):
                    if previous is None:
                        dest.unlink(missing_ok=True)
                    else:
                        dest.write_bytes(previous)
                if moved_new:
                    shutil.rmtree(target)
                if moved_old:
                    backup.rename(target)
                raise
            finally:
                for tmp, _, _ in pending:
                    tmp.unlink(missing_ok=True)
        return manifest

    def uninstall(self, name: str) -> dict:
        """Remove unchanged owned files; preserve user edits and ancillary data."""
        target = self._target(name)
        if not target.exists():
            raise PackageError(f"{name} is not installed")
        manifest = self._manifest(target)
        removed, preserved = [], []
        for filename, checksum in manifest["files"].items():
            path = target / filename
            if path.is_symlink() or (path.exists() and (not path.is_file() or _digest(path.read_bytes()) != checksum)):
                preserved.append(filename)
            elif path.is_file():
                path.unlink()
                removed.append(filename)
        (target / MANIFEST).unlink()
        for path in sorted(target.rglob("*"), reverse=True):
            if path.is_dir() and not path.is_symlink():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            target.rmdir()
        except OSError:
            pass
        return {"name": name, "removed": removed, "preserved": preserved}

    def find(self, name: str, recursive: bool = True):
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            return None
        direct = self.root / (name + ".ado")
        if direct.is_file():
            return direct
        if self.root.is_dir():
            matches = self.root.rglob(name + ".ado") if recursive else self.root.glob("*/" + name + ".ado")
            for path in sorted(matches):
                if path.is_file() and not any(p.startswith(".openstat-") for p in path.relative_to(self.root).parts):
                    return path
        return None

    def installed(self):
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.glob("*/" + MANIFEST)):
            if path.parent.name.startswith(".openstat-") or path.parent.is_symlink():
                continue
            result.append(self._manifest(path.parent))
        return result
