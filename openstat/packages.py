"""SSC/net package acquisition without claiming execution compatibility.

Third-party packages retain their own licenses. Installation downloads files;
loading an ado program still passes through OpenStat's supported interpreter.

SSC uses its canonical HTTP endpoint at fmwww.bc.edu/repec/bocode. Downloads are
recorded with SHA-256 hashes, which record bytes rather than authenticate HTTP.
Explicit HTTPS net sources retain normal certificate validation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class PackageError(ValueError):
    pass


def download(url: str) -> bytes:
    if urlparse(url).scheme not in {"https", "http"}:
        raise PackageError("package URLs must use HTTP or HTTPS")
    request = Request(url, headers={"User-Agent": "OpenStat/0.1 (independent statistical workbench)"})
    with urlopen(request, timeout=15) as response:
        data=response.read(16*1024*1024+1)
    if len(data)>16*1024*1024:
        raise PackageError("package file exceeds 16 MiB limit")
    return data


class PackageManager:
    def __init__(self, root: Path | None=None):
        self.root=Path(root or os.environ.get("OPENSTAT_ADO",Path.home()/".openstat"/"ado"))

    def install(self,name: str,source: str | None=None,replace=False):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):
            raise PackageError("invalid package name")
        letter=name[0].lower() if name[0].isalpha() else "_"
        base=(source or f"http://fmwww.bc.edu/repec/bocode/{letter}/").rstrip("/")+"/"
        descriptor_url=urljoin(base,name+".pkg")
        descriptor=download(descriptor_url)
        files=[]
        for raw in descriptor.decode("utf-8",errors="replace").splitlines():
            if not raw.startswith("f "):continue
            filename=raw[2:].strip()
            path=PurePosixPath(filename)
            if (not filename or path.is_absolute() or ".." in path.parts or "\\" in filename
                or ":" in filename or "?" in filename or "#" in filename):
                raise PackageError("unsafe package path")
            if path.suffix.lower() in {".dll",".exe",".plugin",".mo",".mlib",".so",".dylib"}:
                raise PackageError("compiled/native package payloads are unsupported")
            files.append(filename)
        if not files or len(files)>200:
            raise PackageError("empty or oversized package manifest")
        self.root.mkdir(parents=True,exist_ok=True)
        target=self.root/name
        if target.exists() and not replace:
            raise PackageError(f"{name} already installed; specify replace")
        # Acquire everything first. An interrupted download leaves no partial install.
        contents={filename:download(urljoin(base,filename)) for filename in files}
        with tempfile.TemporaryDirectory(prefix="openstat-ssc-",dir=self.root) as work:
            stage=Path(work)/name
            stage.mkdir()
            for filename,data in contents.items():
                path=stage/filename
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(data)
            manifest={"name":name,"source":descriptor_url,"files":{f:hashlib.sha256(b).hexdigest() for f,b in contents.items()},
                      "execution_compatibility":"unverified; unsupported syntax raises an error", "license":"upstream; not relicensed by OpenStat"}
            (stage/"openstat-package.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
            if target.exists():
                import shutil
                backup=Path(work)/"previous"
                target.rename(backup)
                try:stage.rename(target)
                except OSError:
                    backup.rename(target)
                    raise
                shutil.rmtree(backup)
            else:
                stage.rename(target)
        return manifest

    def find(self,name: str):
        if not re.fullmatch(r'[A-Za-z_]\w*',name):return None
        direct=self.root/(name+".ado")
        if direct.is_file():return direct
        if self.root.is_dir():
            for path in sorted(self.root.glob(f"*/{name}.ado")):
                if path.is_file():return path
        return None

    def installed(self):
        if not self.root.exists():return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.root.glob("*/openstat-package.json"))]
