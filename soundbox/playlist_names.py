"""Shared playlist-name comparison and unique flat device titles."""
from collections.abc import Iterable
import unicodedata


def normalize_playlist_name(value: str) -> str:
    return unicodedata.normalize('NFC', str(value).strip()).casefold()


def unique_playlist_names(names: Iterable[str]) -> list[str]:
    output = [str(name).strip() for name in names]
    seen = set()
    for name in output:
        key = normalize_playlist_name(name)
        if not key or key in seen:
            raise ValueError(f'Playlist names must be unique: {name or "Unnamed playlist"}')
        seen.add(key)
    return output
