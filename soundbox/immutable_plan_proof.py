"""Bounded reuse of successful proofs about exact immutable plan bytes.

Callers MUST freshly validate paths, authority/profile, raw plan and every
payload digest before calling. Only the supplied pure reconstruction may be
reused: live source snapshots, process/version checks, locks, SQL readback and
recovery/publication guards never belong here. No plan data or connections are
retained, and every miss uses the original full proof.
"""
from collections import OrderedDict
from collections.abc import Callable
import hashlib
from pathlib import Path
import threading
import time

MAX_PROOFS = 4
PROOF_SECONDS = 30.0
_verified: OrderedDict[tuple[str, str, bytes, bytes], float] = OrderedDict()
_lock = threading.Lock()


def verify_once(folder: Path, raw_plan: bytes, *, authority: bytes, profile: str,
                verify: Callable[[], None]) -> None:
    """Reuse one exact, already verified reconstruction for at most 30 seconds.

    The authority token is its exact serialized immutable value. The raw plan
    binds all expected payload hashes, source identities and reconstruction
    inputs. Folder/profile separation prevents cross-domain reuse. Expiry is
    measured from successful verification, never renewed by cache hits.
    """
    if (type(raw_plan) is not bytes or type(authority) is not bytes
            or type(profile) is not str or not profile):
        raise ValueError('Immutable proof keys require exact bytes and an explicit profile')
    key = (str(folder), profile, hashlib.sha256(raw_plan).digest(), hashlib.sha256(authority).digest())
    now = time.monotonic()
    with _lock:
        for old, stamp in list(_verified.items()):
            if not 0 <= now - stamp < PROOF_SECONDS:
                del _verified[old]
        if key in _verified:
            _verified.move_to_end(key)
            return
    # Never retain a failed or interrupted reconstruction. Concurrent identical
    # misses may repeat the pure work; they cannot authorize an unverified hit.
    verify()
    with _lock:
        _verified[key] = time.monotonic()
        _verified.move_to_end(key)
        while len(_verified) > MAX_PROOFS:
            _verified.popitem(last=False)
