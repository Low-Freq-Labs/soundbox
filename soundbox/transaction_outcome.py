"""Explicit transaction evidence, mirrored locally when a mount disappears."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

LOG = logging.getLogger("soundbox")


def record_outcome(session: Any, recovery: Path, state: str, *,
                   error: str = "", local_root: Path | None = None) -> None:
    if state not in {"executing", "committed", "rolled back", "recovery required"}:
        raise ValueError(state)
    row = {"target": str(session.target), "mount_identity": session.mount_identity,
           "recovery": str(recovery), "transaction_state": state, "error": error}
    # A local record survives an unplug and never authorizes automatic writes.
    if local_root is not None:
        try:
            root = local_root / "transaction-outcomes-v1"
            root.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256((str(session.target) + str(recovery)).encode()).hexdigest()
            fd, name = tempfile.mkstemp(dir=root, prefix=".outcome-")
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(row, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, root / f"{key}.json")
            finally:
                Path(name).unlink(missing_ok=True)
        except OSError as exc:
            LOG.warning("Could not persist local transaction outcome: %s", exc)
    try:
        session.revalidate()
        journal = session.guard(recovery / "plan.json")
        data = json.loads(journal.read_text())
        data.update(row)
        session.atomic_write(journal, json.dumps(data, sort_keys=True, indent=2).encode() + b"\n")
    except (OSError, RuntimeError, ValueError) as exc:
        LOG.warning("Could not publish %s transaction evidence on device: %s", state, exc)
