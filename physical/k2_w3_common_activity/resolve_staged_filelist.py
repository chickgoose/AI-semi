#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.input.is_symlink() or args.output.exists():
        raise SystemExit("unsafe staged filelist or output exists")
    source = args.input.resolve(strict=True)
    lines: list[str] = []
    for raw in source.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("+define+"):
            lines.append(raw)
        elif line.startswith("+incdir+"):
            directory = (root / line[len("+incdir+"):]).resolve(strict=True)
            if root not in directory.parents and directory != root:
                raise SystemExit("include directory escapes staged root")
            lines.append(f"+incdir+{directory}")
        elif line.startswith("+") or line.startswith("-"):
            raise SystemExit(f"unsupported nested staged filelist option: {line}")
        else:
            path = (root / line).resolve(strict=True)
            if root not in path.parents or path.is_symlink() or not path.is_file():
                raise SystemExit("staged source escapes root or is not regular")
            lines.append(str(path))
    args.output.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
