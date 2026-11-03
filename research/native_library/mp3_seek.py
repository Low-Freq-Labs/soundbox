"""Compatibility exports for retained MP3 research proofs; no source publisher.

Implementation globals live in soundbox.mp3_scan. Fault-injection tests must
patch that module, rather than this compatibility view.
"""
from soundbox import mp3_scan as _implementation

# Preserve the complete former module surface, including private proof helpers
# and imported constants used by retained scripts. Objects remain identical.
__all__ = [name for name in vars(_implementation) if not name.startswith('__')]
globals().update({name: getattr(_implementation, name) for name in __all__})
