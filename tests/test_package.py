import tomllib
from pathlib import Path

import tavernpy

ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_pyproject():
    with open(ROOT / "pyproject.toml", "rb") as f:
        assert tomllib.load(f)["project"]["version"] == tavernpy.__version__


def test_changelog_has_this_version():
    assert f"## {tavernpy.__version__}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
