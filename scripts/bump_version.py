"""Bump the `[project] version` in pyproject.toml.

Usage: python scripts/bump_version.py <current|patch|minor|major>

Prints the resulting version. `current` leaves the file untouched and only
prints the version. Stdlib only, so it runs on a bare GitHub Actions runner.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
VERSION_LINE = re.compile(r'^(version\s*=\s*")(\d+)\.(\d+)\.(\d+)(")\s*$', re.MULTILINE)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"current", "patch", "minor", "major"}:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    bump = argv[1]

    text = PYPROJECT.read_text()
    current = tomllib.loads(text)["project"]["version"]

    if bump == "current":
        print(current)
        return 0

    matches = VERSION_LINE.findall(text)
    if len(matches) != 1:
        print(f'expected exactly one `version = "X.Y.Z"` line, found {len(matches)}', file=sys.stderr)
        return 1
    major, minor, patch = (int(matches[0][i]) for i in (1, 2, 3))
    if f"{major}.{minor}.{patch}" != current:
        print(f"version line {major}.{minor}.{patch} does not match parsed version {current}", file=sys.stderr)
        return 1

    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"

    PYPROJECT.write_text(VERSION_LINE.sub(rf"\g<1>{new}\g<5>", text, count=1))
    assert tomllib.loads(PYPROJECT.read_text())["project"]["version"] == new
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
