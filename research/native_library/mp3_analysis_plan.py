"""Compatibility exports for retained CBR plans; no source authority.

The original profile identifier, proposal fields and false authority flags stay
unchanged so previously reviewed/recovered pilot plans reconstruct exactly.
"""
from soundbox import native_mp3_analysis_plan as _implementation

__all__ = [name for name in vars(_implementation) if not name.startswith('__')]
globals().update({name: getattr(_implementation, name) for name in __all__})
