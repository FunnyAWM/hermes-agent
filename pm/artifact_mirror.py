"""Public, content-addressed copies of reviewed binary inputs."""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re

from pm.downloader import Source
from pm.index_config import npm_registry, npm_registry_url
from pm.package import InstallError

_LAYOUT = json.loads(Path(__file__).with_name("artifact-mirror.json").read_text(encoding="utf-8-sig"))
KEY_PREFIX = _LAYOUT["prefix"]
PUBLIC_PREFIX = _LAYOUT["origin"] + "/" + KEY_PREFIX

# pm bootstraps before any dependency exists, so like the uv/npm index knobs
# this mirror is env-only (see pm.index_config).
DOWNLOAD_MIRROR_ENV = "HERMES_PM_DOWNLOAD_MIRROR"


def object_key(sha256: str) -> str:
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("A pinned input requires a full lowercase SHA256")
    return KEY_PREFIX + sha256


def mirror_url(sha256: str) -> str:
    object_key(sha256)
    return PUBLIC_PREFIX + sha256


def download_mirror_prefix(env: Mapping[str, str]) -> str:
    """The user's download-mirror prefix (ghproxy-style: ``<prefix><original-url>``), or "".

    The bytes stay sha256-pinned end to end, so any mirror serving the pinned
    content is integrity-equivalent; one that cannot serve a pin falls back to
    the original URL and the public archive.
    """
    value = (env.get(DOWNLOAD_MIRROR_ENV) or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("https://", "http://")):
        raise InstallError(DOWNLOAD_MIRROR_ENV, "must be an absolute http(s) URL",
                           "unset it or fix it, e.g. https://mirror.example.com")
    return value + "/"


def pinned_source(url: str, dest: Path, sha256: str) -> Source:
    archive = mirror_url(sha256)
    prefix = download_mirror_prefix(os.environ)
    if prefix:
        # The user's mirror wins: they set it because the upstream hosts are
        # unreachable from their network. A plain-http prefix is user-configured
        # and hash-pinned, the same trust model as the LAN npm registry (#150032).
        return Source(prefix + url, dest, sha256, fallbacks=(url, archive),
                      http_mirror=prefix if prefix.startswith("http://") else "")
    # The lock records registry.npmjs.org; a user's npm mirror serves the same pinned bytes (#123132).
    mirrored = npm_registry_url(url, os.environ)
    # A LAN registry is often plain http (#150032). Only its rewritten URL may use it.
    registry = npm_registry(os.environ) if mirrored != url else ""
    return Source(mirrored, dest, sha256, fallbacks=() if mirrored == archive else (archive,),
                  http_mirror=registry if registry.startswith("http://") else "")
