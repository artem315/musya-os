"""Command line interface: ``python -m musya_runtime <command>``.

Commands::

    run FILE [--engine] [--headed] [--allow PERM ...] [--timeout SEC]
    test FILE
    check FILE            parse only, report syntax errors
    ast FILE              print the AST as JSON
    validate MANIFEST     validate a .yml or .driver file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import (ManifestError, MusyaError, SandboxHost, __version__, load_manifest_file, parse)
from .interpreter import Interpreter, Limits
from .parser.ast import to_dict


def _host(path: Path, allow: list[str] | None, engine: bool, headed: bool):
    perms = set(allow) if allow else None
    if not engine:
        return SandboxHost(path.parent, perms), None
    try:
        from service_engine import EngineHost, ServiceEngine
    except ImportError:
        sys.exit("service_engine is not installed: pip install -e service-engine")
    eng = ServiceEngine(headless=not headed, downloads_dir=path.parent / "Downloads",
                        screenshots_dir=path.parent / "screenshots")
    # A local CLI user may automate their own machine's local servers.
    eng.allow_private = True
    return EngineHost(path.parent, eng, perms), eng


async def _run(args) -> int:
    path = Path(args.file)
    source = path.read_text(encoding="utf-8")
    host, engine = _host(path, args.allow, args.engine, args.headed)
    interp = Interpreter(host, Limits())
    if hasattr(host, "attach"):
        host.attach(interp)  # forward engine events (page_loaded, ...) to `on` handlers
    try:
        result = await asyncio.wait_for(
            interp.run_source(source, path.name, test_mode=args.command == "test"), timeout=args.timeout)
    except MusyaError as exc:
        print(f"✖ {exc}", file=sys.stderr)
        return 1
    except asyncio.TimeoutError:
        print(f"✖ script exceeded {args.timeout}s", file=sys.stderr)
        return 1
    finally:
        if hasattr(host, "detach"):
            host.detach()
        if engine is not None:
            await engine.shutdown()
    if args.command == "test":
        failed = [t for t in result.tests if not t.passed]
        for t in result.tests:
            mark = "✔" if t.passed else "✖"
            extra = f" - {t.error['message']} (line {t.error['line']})" if t.error else ""
            print(f"{mark} {t.name}{extra}")
        print(f"{len(result.tests) - len(failed)} passed, {len(failed)} failed")
        return 1 if failed else 0
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="musya", description=f"MUSYA Runtime {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "test"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--engine", action="store_true", help="attach a local Playwright Service Engine")
        p.add_argument("--headed", action="store_true", help="show the browser window")
        p.add_argument("--allow", nargs="*", help="restrict permissions (default: all)")
        p.add_argument("--timeout", type=float, default=300.0)
    for name in ("check", "ast"):
        p = sub.add_parser(name)
        p.add_argument("file")
    p = sub.add_parser("validate")
    p.add_argument("manifest")
    args = ap.parse_args(argv)

    if args.command in ("run", "test"):
        return asyncio.run(_run(args))
    if args.command in ("check", "ast"):
        path = Path(args.file)
        try:
            program = parse(path.read_text(encoding="utf-8"), path.name)
        except MusyaError as exc:
            print(f"✖ {exc}", file=sys.stderr)
            return 1
        if args.command == "ast":
            print(json.dumps(to_dict(program), ensure_ascii=False, indent=2))
        else:
            print(f"✔ {path.name}: {len(program.body)} top-level statements, syntax OK")
        return 0
    if args.command == "validate":
        path = Path(args.manifest)
        try:
            m = load_manifest_file(path, project_root=path.parent)
        except ManifestError as exc:
            for e in exc.errors:
                print(f"✖ {e}", file=sys.stderr)
            return 1
        print(f"✔ {m.kind} '{m.name}' {m.version} - entry {m.entry}")
        for p in m.permissions:
            print(f"  • {p}")
        for w in m.warnings:
            print(f"  ⚠ {w}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
