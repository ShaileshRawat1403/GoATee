#!/usr/bin/env python3
"""Build standalone zero-dependency executable zipapp binary for GoATee."""

import argparse
import shutil
import stat
import sys
import tempfile
import zipapp
from pathlib import Path
from typing import Optional


def build_zipapp(
    repo_root: Optional[Path] = None,
    output_path: Optional[Path] = None
) -> Path:
    """Bundle goatee package into an executable standalone zipapp."""
    root = repo_root or Path(__file__).resolve().parent.parent
    dist_dir = root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    target = output_path or (dist_dir / "goatee")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pkg_src = root / "goatee"

        if not pkg_src.is_dir():
            raise FileNotFoundError(f"GoATee source package not found at {pkg_src}")

        # Copy goatee package, ignoring __pycache__
        shutil.copytree(
            pkg_src,
            tmp_path / "goatee",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
        )

        # Generate entrypoint __main__.py
        main_py = tmp_path / "__main__.py"
        main_py.write_text(
            "import sys\n"
            "from goatee.cli import main\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
            encoding="utf-8"
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(
            tmp_path,
            target,
            interpreter="/usr/bin/env python3"
        )

    # Make executable: rwxr-xr-x
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Build GoATee standalone executable zipapp")
    parser.add_argument("-o", "--output", help="Target output file path (default: dist/goatee)")
    args = parser.parse_args()

    out_p = Path(args.output) if args.output else None
    res = build_zipapp(output_path=out_p)
    size_kb = res.stat().st_size / 1024
    print(f"Successfully built GoATee standalone executable:")
    print(f"  Path: {res}")
    print(f"  Size: {size_kb:.1f} KB")
    print("  Interpreter: /usr/bin/env python3")


if __name__ == "__main__":
    main()
