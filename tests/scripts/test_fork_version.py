"""Unit tests for the fork version scheme (Q7): one release, two spellings."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "fork_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("fork_version_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fork_version = _load()


@pytest.mark.parametrize("text", [
    "0.22.5-fork.1", "v0.22.5-fork.1", "0.22.5+fork1", "0.22.5+fork.1", " 0.22.5-fork.12 ",
])
def test_both_spellings_parse(text):
    parsed = fork_version.parse(text)
    assert parsed.base == "0.22.5"
    assert parsed.fork in (1, 12)


def test_canonical_forms_and_tag():
    parsed = fork_version.parse("0.22.5-fork.3")
    assert parsed.display == "0.22.5-fork.3"
    assert parsed.pep440 == "0.22.5+fork3"
    assert parsed.tag == "v0.22.5-fork.3"
    assert fork_version.parse("0.22.5+fork3") == parsed


@pytest.mark.parametrize("text", [
    "", "0.22.5", "0.22.5-fork.0", "0.22.5-fork", "0.22.5-fork.x", "0.22.5-fork.1.2",
    "1.2-fork.1", "v1.2.3-rc.1", "1.2.3+fork", "1.2.3+fork0", "01.2.3-fork.1",
])
def test_invalid_versions_rejected(text):
    with pytest.raises(ValueError):
        fork_version.parse(text)


def test_equivalent_only_across_spellings():
    assert fork_version.equivalent("0.22.5+fork1", "v0.22.5-fork.1")
    assert not fork_version.equivalent("0.22.5+fork1", "0.22.5-fork.2")
    assert not fork_version.equivalent("0.22.5+fork1", "0.22.6-fork.1")
    assert not fork_version.equivalent("bogus", "0.22.5-fork.1")


def test_fork_tags_are_not_upstream_stable():
    assert fork_version.is_stable_upstream_tag("v0.22.5")
    assert not fork_version.is_stable_upstream_tag("v0.22.5-fork.1")
