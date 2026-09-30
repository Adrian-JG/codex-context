from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .doctor import doctor
from .global_setup import setup_global
from .models import ConfigError, ContextError
from .project import init_project, update_map


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="codex-context", description="Compact, safe repository context for Codex + Serena.")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup-global", help="Configure Codex + external Serena; preserve existing settings")
    setup.add_argument("--install-serena", action="store_true", help="Explicitly install Serena using uv tool install")
    setup.add_argument("--dry-run", action="store_true", help="Describe actions without writes or installation")
    for name, help_text in (("init", "Initialize local context; Serena configuration may remain pending"),
                            ("map", "Recompute deterministic repository map"),
                            ("refresh", "Refresh stale structural context"),
                            ("doctor", "Inspect local configuration and map freshness")):
        child = commands.add_parser(name, help=help_text)
        child.add_argument("path", nargs="?", default=".", help="Repository directory (default: current directory)")
        if name != "doctor":
            child.add_argument("--dry-run", action="store_true", help="Describe changes without writing any files")
            child.add_argument("--force", action="store_true", help="Bypass extraction cache; never overwrite manual or user-owned content")
        else:
            child.add_argument("--connect", action="store_true", help="Additionally check a real MCP connection (may create Serena caches)")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "setup-global":
            lines = setup_global(dry_run=arguments.dry_run, install=arguments.install_serena)
            code = 0
        else:
            root = Path(arguments.path).expanduser().resolve()
            if not root.is_dir():
                raise ContextError(f"Repository directory does not exist: {root}")
            if arguments.command == "doctor":
                lines, pending = doctor(root, check_connection=arguments.connect)
                code = 3 if pending else 0
            elif arguments.command == "init":
                lines = init_project(root, dry_run=arguments.dry_run, force=arguments.force)
                code = 0
            else:
                lines = update_map(root, refresh=arguments.command == "refresh",
                                   dry_run=arguments.dry_run, force=arguments.force)
                code = 0
        print("\n".join(lines))
        return code
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (ContextError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
