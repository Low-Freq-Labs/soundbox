"""Research compatibility imports for the pure native identity planner.

No source authority, discovery, SQL writes or migration is inferred.
"""
from soundbox.native_identity import Binding, IdentityDecision, _canonical, resolve

__all__ = ['Binding', 'IdentityDecision', '_canonical', 'resolve']
