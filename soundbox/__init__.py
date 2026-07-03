"""Soundbox engine: a safe bridge from rekordbox to Rockbox/iPod.

The desktop app (the `desktop` package) reads the LIVE rekordbox database via a
read-only snapshot (rekordbox_db) — an XML export (rekordbox_xml) is the
fallback and what the debug CLI uses. This package NEVER touches the rekordbox
database, rekordbox app folders, or any source audio file: sources are only
ever read, and every write lands under an explicit export target (see the
safety contract at the top of soundbox.sync).
"""

__version__ = "1.0.0"
