"""Device browser + eject (mixin for MainWindow): the on-device file
manager (rename / new folder / assign artist / threaded delete) and the
QProcess-based normal macOS eject.

Every destructive action is guarded to paths STRICTLY inside an external
device (_on_external_device) — system volumes and mount roots are unreachable.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox
from .choices import Menu as QMenu

from soundbox import imports as import_mod, sync as sync_mod
from soundbox.paths import sanitize_component
from soundbox.device_operations import DeviceCommand, DeviceSessionIdentity, Phase

from .devices import (
    _external_volume_of, _in_external_product_tree, _on_external_device,
)
from .workers import DeleteWorker, OneShotTask

LOG = logging.getLogger("soundbox")


def _browser_entry_name(raw: str) -> str:
    """Validate one literal on-device name without silently rewriting it."""
    name = raw.strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\0" in name
    ):
        raise ValueError("Enter one file or folder name, without slashes.")
    return name


class DeviceBrowserMixin:
    # Eject is independent of finalization: every required dashboard/sidebar
    # read must already be finished before the completion dialog appears. Keep
    # the full guard fail-closed in case the user explicitly starts a new read
    # or operation before ejecting.
    _EJECT_BLOCKING_WORKERS = (
        "export_worker", "_resync_worker", "_delete_worker", "_cdj_worker",
        "_stock_plan_worker", "_stock_commit_worker",
        "_dual_plan_worker", "_dual_commit_worker",
        "_stock_delete_plan_worker", "_stock_delete_commit_worker",
        "_device_import_worker", "_device_verify_worker",
        "_stock_view_worker", "_dev_scan_worker",
    )

    def _erase_device(self, dev: str):
        """Explicitly erase every child of one selected writable volume."""
        volume = _external_volume_of(dev)
        if volume is None or volume != Path(dev).resolve():
            QMessageBox.critical(
                self, "Refused", "Will only erase a mounted external volume.")
            return
        profile = getattr(self, "_device_profiles", {}).get(dev)
        if profile is not None and getattr(profile, "has_stock_ipod", False):
            QMessageBox.warning(
                self, "Stock iPod is read-only",
                "SoundBox will not erase an iTunes/stock-iPod volume.")
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._erase_device(dev)):
            return
        name = volume.name
        answer = QMessageBox.question(
            self, "Erase entire device?",
            f"Permanently erase every file and folder on:\n\n"
            f"{name}\n{volume}\n\n"
            "This includes music, playlists, unknown files, and Rockbox "
            "firmware if present. This cannot be undone.\n\n"
            "SoundBox's source library will not be changed.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._release_eject_ready()
        sources = [
            track.path for track in (
                getattr(getattr(self, "lib", None), "tracks_by_id", {}) or {}
            ).values()
        ]
        self._deleting = True
        self.progress.setVisible(False)
        self._start_pulse("ERASING")
        worker = OneShotTask(
            lambda: sync_mod.erase_device_contents(volume, sources))
        self._delete_worker = worker
        worker.finished.connect(
            lambda w=worker, d=str(volume):
            self._finish_erase_device_worker(w, d))
        self._start_device_worker(worker, dev, "erase")

    def _on_erase_device_done(self, dev: str, result):
        self._deleting = False
        self._stop_pulse()
        self.progress.setVisible(False)
        if isinstance(result, Exception):
            self._erase_eject_pending = None
            QMessageBox.critical(self, "Erase failed", str(result))
            self._refresh_devices()
            return
        self._erase_eject_pending = dev
        self._flash_status("Device erased ✓")

    def _finish_erase_device_worker(self, worker, dev: str):
        result = getattr(worker, "result", None)
        self._finish_worker("_delete_worker", worker)
        self._on_erase_device_done(dev, result)
        if getattr(self, "_erase_eject_pending", None) == dev:
            self._erase_eject_pending = None
            # Start only after QThread has fully returned and our busy guard no
            # longer sees it. Eject requests a normal macOS unmount without a library scan.
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda d=dev: self._eject(d))

    def _eject_current(self):
        path = getattr(self, "_current_device_path", "")
        if not path:
            idx = self.fs_view.rootIndex()
            path = self.fs_model.filePath(idx)
        if path:
            self._eject(path)

    def _eject(self, dev: str):
        # Never race device work. Finalization owns and finishes its dashboard
        # scan before success; Eject does not cancel or complete that work.
        readers = ("_dev_scan_worker", "_stock_view_worker", "_device_verify_worker")
        scan = next((getattr(self, name, None) for name in readers
                     if getattr(self, name, None) is not None), None)
        other_blockers = tuple(
            name for name in self._EJECT_BLOCKING_WORKERS
            if name not in readers
        )
        if (
            scan is not None
            and not any(getattr(self, name, None) is not None
                        for name in other_blockers)
            and getattr(self, "_pending_device_operation", None) is None
        ):
            # A recurring read-only health poll is not a user operation. Queue
            # Eject behind its actual QThread.finished boundary and suppress a
            # replacement poll; never punish a click with a busy modal.
            if self._device_coordinator.eject_pending:
                return
            self._device_coordinator.eject_pending = dev
            self._stock_view_pending.clear()
            self._exporting = True
            self._start_pulse("Waiting to eject")

            def resume():
                def after_finished_slots():
                    self._exporting = False
                    self._eject(dev)

                QTimer.singleShot(0, after_finished_slots)

            scan.finished.connect(resume)
            return
        if (
            any(getattr(self, name, None) is not None
                for name in self._EJECT_BLOCKING_WORKERS)
            or getattr(self, "_pending_device_operation", None) is not None
        ):
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if getattr(self, "_eject_verify_worker", None) is not None:
            return
        self._device_coordinator.eject_pending = dev
        self._exporting = True  # suppress every new periodic poll until outcome
        # macOS eject flushes the filesystem. Library integrity is independent
        # of whether an idle mount can be released.
        self._begin_macos_eject(dev)

    def _begin_macos_eject(self, dev: str):
        self.fs_view.setRootIndex(self.fs_model.setRootPath(""))
        self._start_pulse("Ejecting")

        def succeeded():
            self._stop_pulse()
            self._finish_eject_attempt(dev)
            self._device_coordinator.disconnect(dev)
            self._current_device_path = ""
            self._stock_snapshot = None
            self.stack.setCurrentIndex(0)
            self._refresh_devices()
            self._flash_status("Ejected ✓")

        def run(args, on_fail):
            p = QProcess(self)
            self._eject_proc = p  # keep a ref so it isn't GC'd mid-run
            identity = self._device_coordinator.mounts.get(dev)
            if identity is None:
                identity = DeviceSessionIdentity(dev, self._device_mount_tokens.get(dev, 0))
                self._device_coordinator.observe(identity)
            self._device_coordinator.begin(DeviceCommand(identity, "eject"), p, Phase.EJECTING)
            p.finished.connect(lambda code, _s: (succeeded() if code == 0 else on_fail(p)))
            p.errorOccurred.connect(
                lambda _error: gave_up(p)
                if p.state() == QProcess.NotRunning else None)
            p.start("diskutil", args)

        def gave_up(p):
            self._stop_pulse()
            self._finish_eject_attempt(dev)
            err = bytes(p.readAllStandardError()).decode(errors="replace")
            QMessageBox.warning(self, "Eject failed",
                                err or "Could not eject. Close any app using the device.")
            self._set_status_text("Ready")

        run(["eject", dev], gave_up)

    def _finish_eject_attempt(self, dev: str):
        self._device_coordinator.eject_pending = ""
        self._device_coordinator.finished(getattr(self, "_eject_proc", None))
        if getattr(self, "_eject_ready_device", "") == dev:
            self._eject_ready_device = ""
        self._exporting = False
        self._update_device_action_state()

    # ---------- device browser management ----------
    def _browser_menu(self, pos):
        idx = self.fs_view.indexAt(pos)
        sel = self.fs_view.selectionModel().selectedRows()
        if not sel and idx.isValid():  # nothing selected → act on row under cursor
            sel = [idx]
        paths = [Path(self.fs_model.filePath(i)) for i in sel]
        menu = QMenu(self)

        # rekordbox/CDJ owns /PIONEER. Keep it visible for honest capacity and
        # Finder-style browsing, but expose no SoundBox action within the tree.
        if any(_in_external_product_tree(path) for path in paths):
            return

        if len(paths) > 1:  # multi-select → delete only
            if all(import_mod.is_audio(p) or p.suffix.lower() == ".m3u8"
                   for p in paths):
                menu.addAction(
                    f"Import {len(paths)} to SoundBox…",
                    lambda: self._import_device_paths(paths))
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
                if import_mod.is_audio(path) or path.suffix.lower() == ".m3u8":
                    menu.addAction(
                        "Import to SoundBox…",
                        lambda p=path: self._import_device_paths([p]))
                if path.parent.name == "Unknown Artist":
                    menu.addAction("Assign Artist…", lambda p=path: self._assign_artist(p))
                menu.addAction("Delete", lambda p=path: self._delete_paths([p]))

        if not menu.isEmpty():
            menu.exec(self.fs_view.mapToGlobal(pos))

    def _import_device_paths(self, paths: list[Path]):
        if (not paths
                or any(_external_volume_of(path) is None for path in paths)
                or any(_in_external_product_tree(path) for path in paths)):
            return
        if self._device_operation_owned():
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._import_device_paths(paths)):
            return
        self._release_eject_ready()
        self._start_pulse("IMPORTING")
        w = OneShotTask(lambda: import_mod.copy_from_device(paths))
        self._device_import_worker = w
        w.finished.connect(
            lambda ww=w: self._finish_device_result_worker(
                "_device_import_worker", ww, self._on_device_imported))
        self._start_device_worker(w, str(_external_volume_of(paths[0])), "import")

    def _on_device_imported(self, result):
        self._stop_pulse()
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Import failed", str(result))
            return
        # Register the SoundBox-owned copies; the device and original Finder
        # files remain untouched.
        self._import_files([str(path) for path in result])

    def _start_browser_mutation(self, session, mutate, label: str):
        if self._device_operation_owned():
            QMessageBox.warning(self, "Device busy", "Wait for the current device operation.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._start_browser_mutation(session, mutate, label)):
            return

        def execute():
            session.revalidate()
            mutate()
            session.revalidate()

        def complete(result):
            self._deleting = False
            self._stop_pulse()
            if isinstance(result, Exception):
                QMessageBox.warning(self, f"{label} failed", str(result))
            else:
                self._flash_status(f"{label} complete")
            self._refresh_devices()

        self._release_eject_ready()
        self._deleting = True
        self._start_pulse(label)
        worker = OneShotTask(execute)
        self._delete_worker = worker
        worker.finished.connect(lambda w=worker: self._finish_device_result_worker(
            "_delete_worker", w, complete))
        self._start_device_worker(worker, str(session.target), "browser-mutation")

    def _edit_name(self, index):
        path = Path(self.fs_model.filePath(index))
        if (
            not _on_external_device(path)
            or _in_external_product_tree(path)
        ):
            QMessageBox.critical(
                self, "Refused", "Can only rename items on external devices.")
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._edit_name(index)):
            return
        volume = _external_volume_of(path)
        if volume is None:
            return
        try:
            session = sync_mod.DeviceSession(volume, [], create=False)
            source = session.guard(path)
            relative = source.relative_to(session.target)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Rename refused", str(exc))
            return
        raw, ok = QInputDialog.getText(
            self, "Rename", "Name:", text=path.name)
        if not ok:
            return
        try:
            name = _browser_entry_name(raw)
        except ValueError as exc:
            QMessageBox.critical(self, "Rename failed", str(exc))
            return

        def rename():
            destination = session.guard(relative.with_name(name))
            same_entry = False
            if destination.exists():
                same_entry = os.path.samefile(source, destination)
                if not same_entry:
                    raise FileExistsError(f"An item named {name!r} already exists.")
            if source != destination:
                source.rename(destination)

        self._start_browser_mutation(session, rename, "Rename")

    def _assign_artist(self, path: Path):
        # Move an audio file out of Unknown Artist into /Music/<Artist>/ on the device.
        if (
            not _on_external_device(path)
            or _in_external_product_tree(path)
        ):
            QMessageBox.critical(self, "Refused", "Can only edit external devices.")
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._assign_artist(path)):
            return
        volume = _external_volume_of(path)
        if volume is None:
            return
        try:
            session = sync_mod.DeviceSession(volume, [], create=False)
            source = session.guard(path)
            relative = source.relative_to(session.target)
            if (
                len(relative.parts) != 3
                or relative.parts[0].casefold() != "music"
                or relative.parts[1].casefold() != "unknown artist"
            ):
                raise RuntimeError(
                    "Assign Artist only moves /Music/Unknown Artist tracks.")
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Assign refused", str(exc))
            return
        artist, ok = QInputDialog.getText(self, "Assign Artist", "Artist:")
        artist = sanitize_component(artist.strip()) if artist.strip() else ""
        if ok and artist:
            def assign():
                dest_dir = session.guard(Path("Music") / artist)
                destination = session.guard(Path("Music") / artist / relative.name)
                if destination.exists():
                    raise FileExistsError(f"{artist}/{relative.name} already exists.")
                dest_dir.mkdir(exist_ok=True)
                session.revalidate()
                source.rename(destination)

            self._start_browser_mutation(session, assign, "Assign artist")

    def _new_folder(self, parent: Path):
        if (
            _external_volume_of(parent) is None
            or _in_external_product_tree(parent)
        ):  # external volume or inside one, excluding another product's tree
            QMessageBox.critical(self, "Refused", "Can only create folders on external devices.")
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._new_folder(parent)):
            return
        volume = _external_volume_of(parent)
        if volume is None:
            return
        try:
            session = sync_mod.DeviceSession(volume, [], create=False)
            guarded_parent = session.guard(parent)
            relative_parent = guarded_parent.relative_to(session.target)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Create refused", str(exc))
            return
        name, ok = QInputDialog.getText(self, "New Folder", f"New folder in {parent.name}:")
        if not ok:
            return
        try:
            name = _browser_entry_name(name)
        except ValueError as exc:
            QMessageBox.critical(self, "Create failed", str(exc))
            return

        def create():
            destination = session.guard(relative_parent / name)
            destination.mkdir()

        self._start_browser_mutation(session, create, "Create folder")

    def _delete_paths(self, paths: list[Path]):
        # Safety: only ever delete items strictly INSIDE an external/ejectable volume
        # (never a mount root, never a system volume, never anything reachable by a
        # symlink out of the device). See _on_external_device.
        if (
            not paths
            or any(not _on_external_device(p) for p in paths)
            or any(_in_external_product_tree(p) for p in paths)
        ):
            QMessageBox.critical(self, "Refused",
                                 "Will only delete files inside an external device.")
            return
        if self._device_operation_owned():
            QMessageBox.warning(
                self, "Device busy",
                "Wait for the current SoundBox device operation to finish.")
            return
        if self._defer_device_operation_until_scan_finishes(
                lambda: self._delete_paths(paths)):
            return
        volumes = [_external_volume_of(path) for path in paths]
        if (
            any(volume is None for volume in volumes)
            or any(volume != volumes[0] for volume in volumes[1:])
        ):
            QMessageBox.critical(
                self, "Refused",
                "One delete action cannot span multiple devices.")
            return
        volume = volumes[0]
        assert volume is not None
        try:
            # Pin the confirmed mount identity before the modal opens. The
            # worker receives this exact authority rather than resolving the
            # same /Volumes name again after confirmation.
            session = sync_mod.DeviceSession(volume, [], create=False)
            relatives = [
                session.guard(path).relative_to(session.target)
                for path in paths
            ]
            # Make the confirmation count match the actual top-level work: if
            # a selected folder already contains another selected row, deleting
            # the folder is the one operation the user is authorizing.
            normalized: list[Path] = []
            for relative in relatives:
                if any(
                    existing == relative or existing in relative.parents
                    for existing in normalized
                ):
                    continue
                normalized = [
                    existing for existing in normalized
                    if relative not in existing.parents
                ]
                normalized.append(relative)
            relatives = normalized
            paths = [session.target / relative for relative in relatives]
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "Delete refused", str(exc))
            return
        n = len(paths)
        preview = "\n".join(p.name for p in paths[:12]) + ("\n…" if n > 12 else "")
        if QMessageBox.question(self, "Delete",
                                f"Permanently delete {n} item(s)?\n\n{preview}") != QMessageBox.Yes:
            return
        self._release_eject_ready()
        dev = str(volume)
        w = DeleteWorker(session, relatives)
        self._delete_worker = w
        self._deleting = True  # owns the status label so bg art status doesn't fight it
        w.progress.connect(self._on_device_delete_progress)
        w.finished.connect(
            lambda ww=w, d=dev: self._finish_device_result_worker(
                "_delete_worker", ww,
                lambda result: self._on_delete_done(d, result)))
        # Path/target validation and explicit confirmation are the delete plan.
        self._set_device_progress(150)
        self._start_pulse("DELETING")
        self._start_device_worker(w, dev, "delete")

    def _on_device_delete_progress(self, done: int, total: int, _label: str):
        value = round(
            150 + 650 * min(max(0, done), max(1, total)) / max(1, total))
        self._set_device_progress(max(self.progress.value(), value))

    def _on_delete_done(self, dev: str, result):
        self._deleting = False
        self._stop_pulse()
        if isinstance(result, Exception):
            self.progress.setVisible(False)
            self._set_status_text("Delete failed")
            QMessageBox.critical(self, "Delete failed", str(result))
            self._refresh_devices()
            return
        errors = list(result.errors)
        complete_delete = (
            not result.cancelled
            and not errors
            and result.deleted == result.total
        )
        self._set_device_progress(800)

        def complete():
            if self.stack.currentIndex() == 1:
                self.fs_view.setRootIndex(
                    self.fs_model.setRootPath(self.fs_model.rootPath()))
            if result.cancelled:
                self._set_status_text("Delete cancelled")
                QMessageBox.information(
                    self, "Delete cancelled",
                    f"Deleted {result.deleted} of {result.total} item(s).")
            elif errors:
                self._set_status_text("Delete failed")
                QMessageBox.critical(
                    self, "Delete failed", "\n".join(errors[:10]))
            else:
                self._flash_status(f"Deleted {result.deleted}/{result.total} ✓")

        if dev:
            self._finalize_ipod_operation(
                dev, complete,
                terminal_text=(
                    "Complete ✓" if complete_delete else "Delete incomplete"))
        else:
            self.progress.setVisible(False)
            complete()
