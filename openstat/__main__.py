"""CLI entry point; batch uses only OpenStat's independent runtime."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def main(argv=None):
    parser=argparse.ArgumentParser(description="OpenStat independent statistical workbench (experimental subset)")
    parser.add_argument("file",nargs="?",help="Do-file to open in the desktop editor")
    parser.add_argument("--batch",metavar="DO_FILE",help="Run do-file without opening desktop")
    parser.add_argument("--json",metavar="REPORT",help="Write batch status as JSON")
    parser.add_argument("--cwd",type=Path,help="Working directory (default: current directory)")
    parser.add_argument("--version",action="version",version=f"OpenStat {__version__}")
    parser.add_argument("--smoke-test",metavar="PNG",help="Save a desktop preview then exit")
    args=parser.parse_args(argv)
    if args.batch:
        from .engine import Engine
        engine=Engine(cwd=args.cwd)
        result=engine.run_file(args.batch)
        print(result.output)
        if args.json:
            def simple(value):
                if isinstance(value,dict):return {k:simple(v) for k,v in value.items()}
                if isinstance(value,(list,tuple)):return [simple(v) for v in value]
                if hasattr(value,"item"):value=value.item()
                if isinstance(value,float):
                    import math
                    if not math.isfinite(value):return None
                return value
            status={"version":__version__,"rc":result.rc,"line":result.line,"output":result.output,
                    "observations":len(engine.data),"variables":list(engine.data.columns),"r":simple(engine.returns),"e":simple(engine.estimates)}
            Path(args.json).write_text(json.dumps(status,indent=2,allow_nan=False)+"\n",encoding="utf-8")
        return result.rc if 0<=result.rc<=255 else 1
    if args.json:parser.error("--json requires --batch")
    try:from .gui import main as desktop
    except ImportError as error:
        print(f"Desktop requires: pip install '.[desktop]' ({error})",file=sys.stderr);return 1
    desktop_args=([args.file] if args.file else [])+(["--smoke-test",args.smoke_test] if args.smoke_test else [])
    if args.cwd:
        import os
        os.chdir(args.cwd)
    return desktop(desktop_args)


if __name__=="__main__":
    raise SystemExit(main())
