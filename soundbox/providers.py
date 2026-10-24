"""Explicit first-party provider composition; no network or persistence authority.

Frozen builds use their bundled manifest exclusively. Source runs default to the
personal profile; SOUNDBOX_PROFILE=public exercises core without optional imports.
These trusted in-process interfaces are not an untrusted-code sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from functools import lru_cache
import importlib
import json
import logging
import os
from pathlib import Path
import sys
from threading import Event
from typing import Protocol, TypedDict, cast

from .artwork import ArtResult

LOG = logging.getLogger("soundbox.providers")
API_VERSION = 1
MODULE_ID = "lowfreqlabs.soundcloud"
ACTIONS = ("artwork.search", "artwork.url", "artists.suggest", "connections.soundcloud")


class ConnectionFailure(Exception):
    """Provider errors safe to show in the connection UI (no credentials)."""


class Candidate(TypedDict):
    title: str
    uploader: str
    thumb: str
    url: str
    score: float


class ArtworkProvider(Protocol):
    def fetch_best(self, artist: str, title: str) -> ArtResult | None: ...
    def search_candidates(self, artist: str, title: str) -> list[Candidate]: ...
    def preview_image(self, candidate: Candidate) -> bytes | None: ...
    def fetch_thumb(self, thumb_url: str, source_url: str = "",
                    matched_title: str = "") -> ArtResult | None: ...
    def fetch_by_url(self, url: str) -> ArtResult | None: ...


class ArtistProvider(Protocol):
    def suggest_artist(self, title: str) -> tuple[str, float] | None: ...


class ConnectionService(Protocol):
    def status(self) -> dict: ...
    def credentials(self) -> tuple[str, str]: ...
    def connect(self, client_id: str, client_secret: str, stop: Event,
                browser: Callable[[str], None], progress: Callable[[str], None]) -> None: ...
    def refresh(self, stop: Event, progress: Callable[[str], None]) -> None: ...
    def disconnect(self, stop: Event, progress: Callable[[str], None]) -> None: ...


class ConnectionFactory(Protocol):
    def create(self, root: Path) -> ConnectionService: ...


@dataclass(frozen=True)
class Providers:
    profile: str
    artwork: ArtworkProvider | None = None
    artists: ArtistProvider | None = None
    modules: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    diagnostic: str = ""
    connections: ConnectionFactory | None = None

    def allows(self, action: str) -> bool:
        return action in self.actions


def profile_path(profile: str) -> Path:
    if profile not in ("public", "personal"):
        raise ValueError(f"Unknown SoundBox profile: {profile}")
    return Path(__file__).resolve().parents[1] / "profiles" / f"{profile}.json"


def validate_manifest(data: object) -> dict:
    """Fail closed before any optional import; never import arbitrary entries."""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Unsupported build manifest schema")
    profile = data.get("profile")
    modules = data.get("modules")
    if profile not in ("public", "personal") or not isinstance(modules, list):
        raise ValueError("Invalid build profile/modules")
    if profile == "public" and modules:
        raise ValueError("No online provider is cleared for the public profile")
    if len(modules) > 1:
        raise ValueError("Duplicate or unsupported modules")
    for module in modules:
        if not isinstance(module, dict) or module != {
            "id": MODULE_ID, "version": "1.1.0", "core_api": API_VERSION,
            "actions": list(ACTIONS), "dependencies": ["yt-dlp==2026.8.19"],
        }:
            raise ValueError("Unknown or incompatible provider manifest")
    return data


def read_manifest(path: Path) -> dict:
    return validate_manifest(json.loads(path.read_text()))


def resolve(manifest: dict) -> Providers:
    """Resolve once at startup. Missing/broken modules leave local core usable."""
    profile = str(manifest.get("profile", "unavailable"))
    try:
        data = validate_manifest(manifest)
        if not data["modules"]:
            return Providers(profile)
        # The import targets are fixed by core, not supplied by library settings,
        # music folders, manifest entry points or arbitrary plugin discovery.
        importlib.import_module("yt_dlp")
        module = importlib.import_module("soundbox_personal.discovery")
        connection = importlib.import_module("soundbox_personal.connection")
        if not callable(getattr(connection, "create", None)):
            raise ValueError("Provider missing connection factory")
        for method in ("fetch_best", "search_candidates", "preview_image",
                       "fetch_thumb", "fetch_by_url", "suggest_artist"):
            if not callable(getattr(module, method, None)):
                raise ValueError(f"Provider missing {method}")
        return Providers(profile, cast(ArtworkProvider, module),
                         cast(ArtistProvider, module), (MODULE_ID,), ACTIONS,
                         connections=cast(ConnectionFactory, connection))
    except Exception as exc:
        message = f"Online providers unavailable: {exc}"
        LOG.warning(message)
        return Providers(profile, diagnostic=message)


@lru_cache(maxsize=1)
def runtime_providers() -> Providers:
    try:
        if getattr(sys, "frozen", False):
            path = Path(getattr(sys, "_MEIPASS")) / "soundbox-build.json"
        else:
            path = profile_path(os.environ.get("SOUNDBOX_PROFILE", "personal"))
        result = resolve(read_manifest(path))
    except Exception as exc:
        message = f"Online providers unavailable: {exc}"
        LOG.warning(message)
        result = Providers("unavailable", diagnostic=message)
    LOG.info("Build profile: %s; active modules: %s", result.profile,
             ", ".join(result.modules) or "none")
    return result
