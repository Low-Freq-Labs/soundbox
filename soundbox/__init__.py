"""Qt-free library, preparation and guarded export engine for SoundBox.

The desktop app (the `desktop` package) reads the LIVE rekordbox database via a
read-only snapshot (rekordbox_db) — an XML export (rekordbox_xml) is the
fallback and what the debug CLI uses. Ordinary reads, playback and exports
preserve desktop sources. Separately accepted native transaction domains own
their narrowly scoped source edits; conversion/disposal have independent
authorities and recovery. Device exports write only within a guarded explicit
target. See AGENTS.md and docs/rekordbox-prep.md for the authority boundaries.
"""

__version__ = "1.0.0"
