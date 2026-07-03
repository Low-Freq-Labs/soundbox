"""Device browser + eject (mixin for MainWindow): the on-device file
manager (rename / new folder / assign artist / threaded delete) and the
QProcess-based eject with the unmount-force fallback.

Every destructive action is guarded to paths STRICTLY inside an external
device (_on_external_device) — system volumes and mount roots are unreachable.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from soundbox.paths import sanitize_component

from .devices import _external_volume_of, _on_external_device
from .workers import DeleteWorker, _running

LOG = logging.getLogger("soundbox")


class DeviceBrowserMixin:
    def _eject_current(self):
        idx = self.fs_view.rootIndex()
        path = self.fs_model.filePath(idx)
        if path:
            self._eject(path)

    def _eject(self, dev: str):
        # Async via QProcess so the UI never beachballs while diskutil unmounts.
        self._start_pulse("Ejecting")  # animated dots like Finalizing/Cancelling

        def succeeded():
            self._stop_pulse()
            self.stack.setCurrentIndex(0)
            self._refresh_devices()
            self._flash_status("Ejected ✓")

        def run(args, on_fail):
            p = QProcess(self)
            self._eject_proc = p  # keep a ref so it isn't GC'd mid-run
            p.finished.connect(lambda code, _s: (succeeded() if code == 0 else on_fail(p)))
            p.start("diskutil", args)

        def gave_up(p):
            self._stop_pulse()
            err = bytes(p.readAllStandardError()).decode(errors="replace")
            QMessageBox.warning(self, "Eject failed",
                                err or "Could not eject. Close any app using the device.")
            self.status_label.setText("Ready")

        def try_force(_p):
            # Normal eject was dissented (often Spotlight 'mds'). Force-unmount —
            # safe to physically remove afterward (it's only being indexed).
            self._start_pulse("Forcing")
            run(["unmount", "force", dev], gave_up)

        run(["eject", dev], try_force)

    # ---------- device browser management ----------
    def _browser_menu(self, pos):
        idx = self.fs_view.indexAt(pos)
        sel = self.fs_view.selectionModel().selectedRows()
        if not sel and idx.isValid():  # nothing selected → act on row under cursor
            sel = [idx]
        paths = [Path(self.fs_model.filePath(i)) for i in sel]
        menu = QMenu(self)

        if len(paths) > 1:  # multi-select → delete only
            menu.addAction(f"Delete {len(paths)} items", lambda: self._delete_paths(paths))
        elif not paths:  # empty space → new folder at root
            root = Path(self.fs_model.filePath(self.fs_view.rootIndex()))
            menu.addAction("New Folder…", lambda d=root: self._new_folder(d))
        else:
            path = paths[0]
            if path.is_dir():
                menu.addAction("Edit", lambda i=idx: self._edit_name(i))
                menu.addAction("New Folder…", lambda p=path: self._new_folder(p))
                menu.addAction("Delete", lambda p=path: self._delete_paths([p]))
            else:  # audio file
                if path.parent.name == "Unknown Artist":
                    menu.addAction("Assign Artist…", lambda p=path: self._assign_artist(p))
                menu.addAction("Delete", lambda p=path: self._delete_paths([p]))

        if not menu.isEmpty():
            menu.exec(self.fs_view.mapToGlobal(pos))

    def _edit_name(self, index):
        # Inline rename (like the playlist cells) — QFileSystemModel renames on disk.
        name_idx = index.sibling(index.row(), 0)
        self.fs_view.setCurrentIndex(name_idx)
        self.fs_view.edit(name_idx)

    def _assign_artist(self, path: Path):
        # Move an audio file out of Unknown Artist into /Music/<Artist>/ on the device.
        if not _on_external_device(path):
            QMessageBox.critical(self, "Refused", "Can only edit external devices.")
            return
        artist, ok = QInputDialog.getText(self, "Assign Artist", "Artist:")
        artist = sanitize_component(artist.strip()) if artist.strip() else ""
        if ok and artist:
            dest_dir = path.parent.parent / artist  # .../Music/<Artist>
            try:
                dest_dir.mkdir(exist_ok=True)
                shutil.move(str(path), str(dest_dir / path.name))
            except OSError as exc:
                QMessageBox.critical(self, "Assign failed", str(exc))

    def _new_folder(self, parent: Path):
        if _external_volume_of(parent) is None:  # external volume or inside one
            QMessageBox.critical(self, "Refused", "Can only create folders on external devices.")
            return
        name, ok = QInputDialog.getText(self, "New Folder", f"New folder in {parent.name}:")
        name = name.strip()
        if ok and name:
            try:
                (parent / name).mkdir()
            except OSError as exc:
                QMessageBox.critical(self, "Create failed", str(exc))

    def _delete_paths(self, paths: list[Path]):
        # Safety: only ever delete items strictly INSIDE an external/ejectable volume
        # (never a mount root, never a system volume, never anything reachable by a
        # symlink out of the device). See _on_external_device.
        if not paths or any(not _on_external_device(p) for p in paths):
            QMessageBox.critical(self, "Refused",
                                 "Will only delete files inside an external device.")
            return
        n = len(paths)
        preview = "\n".join(p.name for p in paths[:12]) + ("\n…" if n > 12 else "")
        if QMessageBox.question(self, "Delete",
                                f"Permanently delete {n} item(s)?\n\n{preview}") != QMessageBox.Yes:
            return
        if _running(self._delete_worker):
            return  # a delete is already in progress
        w = DeleteWorker(paths)
        self._delete_worker = w
        self._deleting = True  # owns the status label so bg art status doesn't fight it
        w.done.connect(self._on_delete_done)
        w.finished.connect(lambda ww=w: self._finish_worker("_delete_worker", ww))
        # NO progress bar for delete: it's usually a few folders where each rmtree is
        # one big opaque step — the bar just sat at 0% then jumped to 100%. The animated
        # "DELETING…" label reads as clearly-working without a lying %.
        self.progress.setVisible(False)
        self._start_pulse("DELETING")
        w.start()

    def _on_delete_done(self, errors: list):
        self._deleting = False
        self._stop_pulse()
        self.progress.setVisible(False)
        self._flash_status("Deleted ✓")
        self._refresh_devices()  # update free space + drift badge
        if self.stack.currentIndex() == 1:  # refresh the open browser view
            self.fs_view.setRootIndex(self.fs_model.setRootPath(self.fs_model.rootPath()))
        if errors:
            QMessageBox.critical(self, "Delete failed", "\n".join(errors[:10]))

