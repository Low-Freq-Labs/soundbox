"""Pure per-component reconciliation contract; no persistence or write authority.

A verified native value is the current state. Pending intention compares against
its captured component base. App origin and wall-clock order are not inputs.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from soundbox.rekordbox_write import ConflictError, WriteProofError, _json


@dataclass(frozen=True)
class Component:
    binding: tuple[str, str, str]  # explicit source, library ID, track UUID/logical binding
    name: str
    revision: str
    value: object
    locked: bool = False
    manual: bool = False

    def validate(self):
        if (not isinstance(self.binding, tuple) or len(self.binding) != 3
                or any(not isinstance(x, str) or not x for x in self.binding)
                or not isinstance(self.name, str) or not self.name
                or not isinstance(self.revision, str) or not self.revision
                or type(self.locked) is not bool or type(self.manual) is not bool):
            raise WriteProofError('A verified component identity/revision is required')
        try:
            _json(self.value)
        except (TypeError, ValueError) as exc:
            raise WriteProofError('Component values must be finite canonical JSON data') from exc


@dataclass(frozen=True)
class Intention:
    base: Component
    value: object
    cause: Literal['manual', 'analysis', 'migration']


@dataclass(frozen=True)
class Resolution:
    status: Literal['satisfied', 'unchanged', 'apply', 'conflict', 'blocked']
    current: Component
    intended: object
    reason: str = ''


def resolve(current: Component, intention: Intention) -> Resolution:
    current.validate(); intention.base.validate()
    base = intention.base
    if current.binding != base.binding or current.name != base.name:
        raise ConflictError('Component source/track identity changed; verified rebinding is required')
    if intention.cause not in ('manual', 'analysis', 'migration'):
        raise WriteProofError('Unknown component intention')
    try:
        wanted = _json(intention.value)
    except (TypeError, ValueError) as exc:
        raise WriteProofError('Intended component must be finite canonical JSON data') from exc
    old, now = _json(base.value), _json(current.value)
    if current.revision == base.revision and now != old:
        raise ConflictError('One component revision cannot describe two verified values')
    result = lambda status, reason='': Resolution(status, deepcopy(current), deepcopy(intention.value), reason)
    if wanted == now:
        return result('satisfied')  # either editor may already have committed it
    if wanted == old:
        return result('unchanged')  # a no-op draft never masks newer native data
    if current.locked:
        return result('blocked', 'Component is locked')
    if intention.cause == 'analysis' and current.manual:
        return result('blocked', 'Analysis preserves a manual component')
    if now != old:
        return result('conflict', 'Both the native component and the pending intention changed')
    return result('apply')  # the publisher still needs current native revision + separate authority


def resolve_components(current: dict[str, Component], intentions: dict[str, Intention]):
    """Return decisions without mutating supplied native snapshots or drafts."""
    if any(name != snapshot.name for name, snapshot in current.items()):
        raise WriteProofError('Component map has an inconsistent identity')
    if any(name != intent.base.name for name, intent in intentions.items()):
        raise WriteProofError('Intention map has an inconsistent identity')
    if not intentions.keys() <= current.keys():
        raise ConflictError('A pending component has no verified native base')
    return {name: resolve(current[name], intent) for name, intent in intentions.items()}
