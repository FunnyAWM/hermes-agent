"""Fork version arithmetic for the ``<upstream>-fork.<N>`` scheme (Q7 / PRD §5.4 v0.8).

Two spellings describe the same release and both are checked into the tree:

* display (SemVer-ish, used by ``apps/desktop/package.json`` and the git tag): ``0.22.5-fork.1``
* PEP 440 (used by ``pyproject.toml``): ``0.22.5+fork1``

Rules: the base follows the upstream stable release exactly; ``N`` starts at 1 for a base, resets to 1
on a rebase, and only increments for another package cut from the same base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DISPLAY_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"-fork\.(?P<fork>[1-9]\d*)$"
)
_PEP440_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"\+fork\.?(?P<fork>[1-9]\d*)$"
)


@dataclass(frozen=True)
class ForkVersion:
    major: int
    minor: int
    patch: int
    fork: int | None

    @property
    def base(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def display(self) -> str:
        if self.fork is None:
            return f"{self.base}+kaltsit"
        return f"{self.base}-fork.{self.fork}"

    @property
    def pep440(self) -> str:
        if self.fork is None:
            return self.display
        return f"{self.base}+fork{self.fork}"

    @property
    def tag(self) -> str:
        return f"v{self.display}"


def parse(text: str) -> ForkVersion:
    """Parse either spelling (an optional leading ``v`` is accepted); raise ValueError otherwise."""
    value = (text or "").strip()
    if value[:1] in ("v", "V"):
        value = value[1:]
    if match := re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\+kaltsit", value):
        return ForkVersion(*(int(part) for part in match.groups()), None)
    for pattern in (_DISPLAY_RE, _PEP440_RE):
        match = pattern.fullmatch(value)
        if match:
            return ForkVersion(int(match["major"]), int(match["minor"]),
                               int(match["patch"]), int(match["fork"]))
    raise ValueError(
        f"not a fork version: {text!r}; expected '<base>-fork.<N>' or '<base>+fork<N>' with N >= 1")


def equivalent(left: str, right: str) -> bool:
    """True when two spellings describe the same base and fork number."""
    try:
        return parse(left) == parse(right)
    except ValueError:
        return False


def canonical_pep440(text: str) -> str:
    return parse(text).pep440


def canonical_display(text: str) -> str:
    return parse(text).display


def tag_for(text: str) -> str:
    return parse(text).tag


def is_stable_upstream_tag(tag: str) -> bool:
    """Delegate to the updater's rule so a fork tag can never be mistaken for upstream stable."""
    from hermes_cli.update_channel import STABLE_TAG_RE

    return bool(STABLE_TAG_RE.fullmatch(tag or ""))
