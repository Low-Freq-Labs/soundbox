"""Owned ORM readers for already captured private native database snapshots.

Only passphrase derivation is reused. Each engine reads and authenticates its
own fresh snapshot; its session, compiled statements and pool retire together.
"""
from contextlib import contextmanager

from .native_cipher import key_connection


def _key_snapshot(connection, _record):
    key_connection(connection)


def _close_snapshot(database, *, suppress=False):
    error=None
    close=getattr(database,'close',None)
    if callable(close):
        try:close()
        except BaseException as exc:error=exc
    engine=getattr(database,'engine',None)
    if engine is not None:
        for name in ('clear_compiled_cache','dispose'):
            try:getattr(engine,name)()
            except BaseException as exc:
                if error is None:error=exc
    if error is not None and not suppress:raise error


@contextmanager
def native_snapshot(path):
    """Close every owned resource, retaining an earlier reader failure verbatim."""
    from pyrekordbox import Rekordbox6Database
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    database=Rekordbox6Database(path=str(path))
    try:
        engine=getattr(database,'engine',None)
        # Source-neutral fixtures may expose only the reader methods. Real
        # engines have not opened their first connection at construction time.
        if isinstance(engine,Engine):event.listen(engine,'connect',_key_snapshot)
        yield database
    except BaseException:
        _close_snapshot(database,suppress=True)
        raise
    else:_close_snapshot(database)
