"""Source-neutral XML inputs for disposable library/export tests."""
from pathlib import Path
from urllib.parse import quote


def loc(p: Path) -> str:
    return "file://localhost" + quote(str(p))


def write_xml(path: Path, tracks: list[tuple[str, str, str, Path]], playlist: list[str]) -> None:
    """tracks: (id, name, artist, source_path). playlist: list of track ids."""
    tnodes = "".join(
        f'<TRACK TrackID="{tid}" Name="{name}" Artist="{artist}" Location="{loc(src)}"/>'
        for tid, name, artist, src in tracks
    )
    keys = "".join(f'<TRACK Key="{k}"/>' for k in playlist)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<DJ_PLAYLISTS Version="1.0.0">'
        f'<COLLECTION Entries="{len(tracks)}">{tnodes}</COLLECTION>'
        '<PLAYLISTS><NODE Type="0" Name="ROOT" Count="1">'
        '<NODE Type="0" Name="SETS" Count="1">'
        f'<NODE Type="1" Name="My Set" KeyType="0" Entries="{len(playlist)}">{keys}</NODE>'
        '</NODE></NODE></PLAYLISTS></DJ_PLAYLISTS>'
    )
    path.write_text(xml, encoding="utf-8")
