"""Device publication uses disposable local fixtures, never mounted devices."""
import errno
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from soundbox import device_staging
from soundbox import manifest
from soundbox import sync
from soundbox.cdj_transaction import CdjSession

from soundbox.sync import DeviceSession
from soundbox.sync import clean_applefiles


class DeviceStaging(unittest.TestCase):
    def test_known_copy_digest_needs_one_source_read_and_independent_destination_proof(self):
        with tempfile.TemporaryDirectory() as area:
            root=Path(area).resolve();sources=root/'sources';sources.mkdir();target=root/'target';target.mkdir()
            source=sources/'payload';source.write_bytes(b'captured bytes'*1000)
            destination=target/'copied';session=DeviceSession(target,[source],create=False)
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            with mock.patch.object(sync,'_sha256',side_effect=AssertionError('Redundant source hash')):
                session.atomic_copy(source,destination,expected_sha256=digest)
            self.assertEqual(destination.read_bytes(),source.read_bytes())
            with self.assertRaisesRegex(OSError,'verification'):
                session.atomic_copy(source,destination,expected_sha256='0'*64)
            self.assertEqual(destination.read_bytes(),source.read_bytes())
            self.assertFalse(list(target.glob('*.sbpart')))

    def test_source_replacement_or_changed_copy_blocks_publication_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as area:
            root=Path(area).resolve();sources=root/'sources';sources.mkdir();target=root/'target';target.mkdir()
            source=sources/'payload';source.write_bytes(b'captured bytes'*1000)
            destination=target/'copied';destination.write_bytes(b'keep old payload')
            session=DeviceSession(target,[source],create=False)
            digest=hashlib.sha256(source.read_bytes()).hexdigest();copy=sync.copy_stream
            def replace_after_copy(input_file,output):
                copy(input_file,output)
                replacement=source.with_suffix('.replacement');replacement.write_bytes(source.read_bytes())
                replacement.replace(source)
            with mock.patch.object(sync,'copy_stream',side_effect=replace_after_copy):
                with self.assertRaisesRegex(OSError,'Source payload changed'):
                    session.atomic_copy(source,destination,expected_sha256=digest)
            self.assertEqual(destination.read_bytes(),b'keep old payload')
            def corrupt_copy(_source,output):output.write(b'wrong copied bytes')
            with mock.patch.object(sync,'copy_stream',side_effect=corrupt_copy):
                with self.assertRaisesRegex(OSError,'verification'):
                    session.atomic_copy(source,destination,expected_sha256=digest)
            self.assertEqual(destination.read_bytes(),b'keep old payload')
            self.assertFalse(list(target.glob('*.sbpart')))

    @contextmanager
    def allocation_identity(self):
        """Model the FD and name transition measured on a real FAT32 image."""
        raw_open, raw_fstat, raw_stat = os.open, os.fstat, os.stat
        handles = set()
        observations = []
        empty_inode = 18446744073709551604

        def allocated(info):
            if not stat.S_ISREG(info.st_mode):
                return info
            values = list(info)
            if not info.st_size:
                values[1] = empty_inode
            return os.stat_result(values)

        def open_stage(path, *args, **kwargs):
            handle = raw_open(path, *args, **kwargs)
            if Path(path).name.startswith('.soundbox-'):
                handles.add(handle)
                observations.append(allocated(raw_fstat(handle)).st_ino)
            return handle

        def fstat_stage(handle):
            info = raw_fstat(handle)
            if handle in handles:
                info = allocated(info)
                observations.append(info.st_ino)
            return info

        def stat_stage(path, *args, **kwargs):
            info = raw_stat(path, *args, **kwargs)
            if Path(path).name.startswith('.soundbox-'):
                info = allocated(info)
            return info

        with mock.patch.object(device_staging.os, 'open', side_effect=open_stage), \
                mock.patch.object(device_staging.os, 'fstat', side_effect=fstat_stage), \
                mock.patch.object(device_staging.os, 'stat', side_effect=stat_stage):
            yield observations

    def test_allocation_identity_changes_allow_empty_and_nonempty_publication(self):
        for kind in ('generic', 'cdj'):
            for payload in (b'', b'new generation' * 20):
                with self.subTest(kind=kind, empty=not payload), tempfile.TemporaryDirectory() as area:
                    target = Path(area).resolve()
                    session = (DeviceSession(target, []) if kind == 'generic' else
                               CdjSession(target, [], token=target.stat().st_dev, volume_uuid='fixture'))
                    destination = target / 'selected.wav'
                    destination.write_bytes(b'previous generation')
                    with self.allocation_identity() as observations:
                        session.atomic_write(destination, payload)
                    self.assertEqual(destination.read_bytes(), payload)
                    self.assertEqual(list(target.glob('.soundbox-*.sbpart')), [])
                    if payload:
                        self.assertNotEqual(observations[0], observations[-1])
                    else:
                        self.assertEqual(len(set(observations)), 1)

    def test_allocation_identity_failure_cleans_before_original_descriptor_closes(self):
        for failure in ('body', 'fsync'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as area:
                target = Path(area).resolve()
                session = DeviceSession(target, [])
                destination = target / 'selected.wav'
                destination.write_bytes(b'previous generation')
                with self.allocation_identity():
                    with self.assertRaisesRegex(OSError, 'deliberate failure'):
                        if failure == 'fsync':
                            with mock.patch.object(device_staging.os, 'fsync',
                                                   side_effect=OSError('deliberate failure')):
                                session.atomic_write(destination, b'allocated generation')
                        else:
                            with session._staged_output(destination) as output:
                                output.write(b'allocated generation')
                                output.flush()
                                raise OSError('deliberate failure')
                self.assertEqual(destination.read_bytes(), b'previous generation')
                self.assertEqual(list(target.glob('.soundbox-*.sbpart')), [])

    def test_replaced_regular_stage_is_neither_published_nor_removed(self):
        for original in (b'', b'owned allocated payload'):
            for foreign in (b'', b'foreign replacement'):
                with self.subTest(original=original, foreign=foreign), tempfile.TemporaryDirectory() as area:
                    target = Path(area).resolve()
                    session = DeviceSession(target, [])
                    destination = target / 'selected.wav'
                    destination.write_bytes(b'previous generation')
                    occupant = target / '.soundbox-replaced.sbpart'
                    with mock.patch.object(device_staging.secrets, 'token_hex', return_value='replaced'):
                        with self.assertRaisesRegex(RuntimeError, 'temporary file changed'):
                            with session._staged_output(destination) as output:
                                output.write(original)
                                output.flush()
                                occupant.unlink()
                                occupant.write_bytes(foreign)
                    self.assertEqual(destination.read_bytes(), b'previous generation')
                    self.assertEqual(occupant.read_bytes(), foreign)

    def test_reused_identity_during_name_check_cannot_claim_a_replaced_stage(self):
        with tempfile.TemporaryDirectory() as area:
            target = Path(area).resolve()
            session = DeviceSession(target, [])
            destination = target / 'selected.wav'
            destination.write_bytes(b'previous generation')
            occupant = target / '.soundbox-replaced.sbpart'
            raw_stat, raw_fstat = os.stat, os.fstat
            replaced = False
            stage_handle = None

            def identity(info):
                values = list(info)
                values[1] = 1001
                return os.stat_result(values)

            def replace_during_stat(path, *args, **kwargs):
                nonlocal replaced
                if Path(path).name == occupant.name:
                    if not replaced:
                        replaced = True
                        occupant.unlink()
                        occupant.write_bytes(b'')
                    return identity(raw_stat(path, *args, **kwargs))
                return raw_stat(path, *args, **kwargs)

            def fstat(handle):
                info = raw_fstat(handle)
                return identity(info) if handle == stage_handle else info

            with mock.patch.object(device_staging.secrets, 'token_hex', return_value='replaced'), \
                    mock.patch.object(device_staging.os, 'stat', side_effect=replace_during_stat), \
                    mock.patch.object(device_staging.os, 'fstat', side_effect=fstat):
                with self.assertRaisesRegex(RuntimeError, 'temporary file changed'):
                    with session._staged_output(destination) as output:
                        stage_handle = output.fileno()
            # APFS exposes the unlinked descriptor as nlink0. FAT32 may retain
            # nlink1, so actual FAT replacement identity has separate coverage.
            self.assertTrue(replaced)
            self.assertEqual(destination.read_bytes(), b'previous generation')
            self.assertEqual(occupant.read_bytes(), b'')

    def test_cleanup_parent_swap_after_listing_preserves_both_original_folders(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            target = root / 'device'
            folder = target / 'Music' / 'Artist'
            folder.mkdir(parents=True)
            (folder / '._cover').write_bytes(b'original device metadata')
            outside = root / 'outside'
            outside.mkdir()
            (outside / '._cover').write_bytes(b'outside original metadata')
            identity = folder.stat().st_ino
            original_scan = os.scandir
            @contextmanager
            def swap_after_listing(fd):
                with original_scan(fd) as scan:
                    yield scan
                if os.fstat(fd).st_ino == identity:
                    folder.rename(target / 'retired-parent')
                    folder.symlink_to(outside, target_is_directory=True)
            with mock.patch('soundbox.sync.os.scandir', side_effect=swap_after_listing):
                self.assertEqual(clean_applefiles(target, [folder]), 0)
            self.assertEqual((outside / '._cover').read_bytes(), b'outside original metadata')
            self.assertEqual((target / 'retired-parent' / '._cover').read_bytes(),
                             b'original device metadata')

    def test_cleanup_cannot_follow_a_music_root_or_nested_directory_outside_target(self):
        for scoped in (False, True):
            for linked in ('Music', 'Music/Artist'):
                with self.subTest(scoped=scoped, linked=linked), tempfile.TemporaryDirectory() as area:
                    root = Path(area).resolve()
                    target = root / 'device'
                    target.mkdir()
                    outside = root / 'outside'
                    outside.mkdir()
                    protected = {outside / '._original': b'original metadata',
                                 outside / 'original.sbpart': b'original partial data'}
                    for path, raw in protected.items():
                        path.write_bytes(raw)
                    alias = target / linked
                    alias.parent.mkdir(parents=True, exist_ok=True)
                    alias.symlink_to(outside, target_is_directory=True)
                    clean_applefiles(target, [alias] if scoped else None)
                    for path, raw in protected.items():
                        self.assertTrue(path.exists(), 'cleanup removed an outside original')
                        self.assertEqual(path.read_bytes(), raw)

    def test_range_write_rejects_multiply_linked_targets_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source = root / 'source' / 'segment'
            source.parent.mkdir()
            source.write_bytes(b'new')
            original = root / 'outside' / 'original.ithmb'
            original.parent.mkdir()
            original.write_bytes(b'protected original')
            session = DeviceSession(root / 'device', [source, original])
            destination = session.target / 'artwork.ithmb'
            os.link(original, destination)
            failure = None
            try:
                session.write_verified_range(source, destination, offset=0, size=3,
                                             sha256=hashlib.sha256(b'new').hexdigest())
            except RuntimeError as exc:
                failure = exc
            self.assertEqual(original.read_bytes(), b'protected original')
            self.assertEqual(destination.read_bytes(), b'protected original')
            self.assertIsNotNone(failure)

    def test_range_write_cannot_follow_a_replaced_leaf_or_parent(self):
        for replacement in ('leaf', 'parent'):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as area:
                root = Path(area).resolve()
                source = root / 'source' / 'segment'
                source.parent.mkdir()
                source.write_bytes(b'new')
                outside = root / 'outside'
                outside.mkdir()
                original = outside / 'artwork.ithmb'
                original.write_bytes(b'protected original')
                session = DeviceSession(root / 'device', [source, original])
                destination = session.target / 'Artwork' / original.name
                destination.parent.mkdir()
                destination.write_bytes(b'old data')
                real_open = Path.open
                def swap(path, *args, **kwargs):
                    if path == source and args == ('rb',):
                        if replacement == 'leaf':
                            destination.unlink()
                            destination.symlink_to(original)
                        else:
                            destination.parent.rename(session.target / 'retired-parent')
                            destination.parent.symlink_to(outside, target_is_directory=True)
                    return real_open(path, *args, **kwargs)
                with mock.patch.object(Path, 'open', autospec=True, side_effect=swap):
                    failure = None
                    try:
                        session.write_verified_range(source, destination, offset=0, size=3,
                                                     sha256=hashlib.sha256(b'new').hexdigest())
                    except (OSError, RuntimeError) as exc:
                        failure = exc
                self.assertEqual(original.read_bytes(), b'protected original')
                self.assertEqual(source.read_bytes(), b'new')
                self.assertIsNotNone(failure)

    def test_manifest_publication_preserves_exact_json_schema_and_external_exclusions(self):
        with tempfile.TemporaryDirectory() as area:
            target = Path(area).resolve() / 'device'
            document = {'schema': 2, 'playlists': {'é': {'name': 'Music'}},
                        'files': {'/Music/selected.wav': {'owned': True},
                                  '/PIONEER/rekordbox/export.pdb': {'owned': True}}}
            expected = {**document, 'files': {'/Music/selected.wav': {'owned': True}}}
            manifest.save(target, document)
            self.assertEqual(manifest.manifest_path(target).read_bytes(),
                             json.dumps(expected, ensure_ascii=False, indent=2,
                                        sort_keys=True).encode('utf-8'))
            self.assertIn('/PIONEER/rekordbox/export.pdb', document['files'])

    def test_manifest_parent_swap_preserves_unrelated_cleanup_occupants(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            target = root / 'device'
            parent = target / '.soundbox'
            parent.mkdir(parents=True)
            outside = root / 'outside'
            outside.mkdir()
            occupant = outside / f'.manifest.json.{os.getpid()}.sbpart'
            occupant.write_bytes(b'unrelated original')
            fsync = os.fsync
            def swap(fd):
                fsync(fd)
                parent.rename(target / 'retired-parent')
                parent.symlink_to(outside, target_is_directory=True)
            with mock.patch.object(device_staging.os, 'fsync', side_effect=swap):
                with self.assertRaises((OSError, RuntimeError)):
                    manifest.save(target, {'schema': 2, 'playlists': {}, 'files': {}})
            self.assertEqual(occupant.read_bytes(), b'unrelated original')
            self.assertFalse((outside / 'manifest.json').exists())

    def test_preexisting_staging_symlink_cannot_overwrite_an_outside_original(self):
        for operation in ('atomic_write', 'atomic_concat', 'atomic_copy'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as area:
                root = Path(area).resolve()
                source = root / 'source' / 'original.wav'
                source.parent.mkdir()
                original = b'protected original audio bytes'
                source.write_bytes(original)
                payload = root / 'candidate'
                payload.write_bytes(b'new selected export bytes')
                session = DeviceSession(root / 'device', [source])
                destination = session.target / 'Music' / 'selected.wav'
                destination.parent.mkdir()
                occupant = destination.with_name(destination.name + '.sbpart')
                occupant.symlink_to(source)

                if operation == 'atomic_write':
                    session.atomic_write(destination, payload.read_bytes())
                elif operation == 'atomic_concat':
                    session.atomic_concat([payload], destination)
                else:
                    session.atomic_copy(payload, destination)

                self.assertEqual(source.read_bytes(), original)
                self.assertTrue(occupant.is_symlink())
                self.assertEqual(occupant.readlink(), source)
                self.assertFalse(destination.is_symlink())
                self.assertEqual(destination.read_bytes(), payload.read_bytes())


    def test_parent_replacement_cannot_publish_or_clean_outside_the_pinned_parent(self):
        for kind in ('generic', 'cdj'):
            for replacement in ('symlink', 'directory', 'mount'):
                with self.subTest(kind=kind, replacement=replacement), tempfile.TemporaryDirectory() as area:
                    root = Path(area).resolve()
                    target = root / 'device'
                    target.mkdir()
                    session = (DeviceSession(target, []) if kind == 'generic' else
                               CdjSession(target, [], token=target.stat().st_dev, volume_uuid='fixture-volume'))
                    parent = target / 'Contents'
                    parent.mkdir()
                    final = parent / 'selected.wav'
                    final.write_bytes(b'previous generation')
                    outside = root / 'outside'
                    outside.mkdir()
                    (outside / final.name).write_bytes(b'outside original')
                    fsync = os.fsync
                    def swap(fd):
                        fsync(fd)
                        if replacement == 'mount':
                            target.rename(root / 'retired-device')
                            (target / 'Contents').mkdir(parents=True)
                        else:
                            parent.rename(target / 'retired-parent')
                            if replacement == 'symlink':
                                parent.symlink_to(outside, target_is_directory=True)
                            else:
                                parent.mkdir()
                    with mock.patch.object(device_staging.os, 'fsync', side_effect=swap):
                        with self.assertRaises((OSError, RuntimeError, ValueError)):
                            session.atomic_write(final, b'new selected export')
                    old_parent = (root / 'retired-device' / 'Contents' if replacement == 'mount'
                                  else target / 'retired-parent')
                    self.assertEqual((old_parent / final.name).read_bytes(), b'previous generation')
                    self.assertEqual((outside / final.name).read_bytes(), b'outside original')
                    self.assertEqual(list(old_parent.glob('*.sbpart')), [])
                    if replacement != 'symlink':
                        self.assertFalse(final.exists())

    def test_replaced_staging_occupant_is_neither_published_nor_removed(self):
        for kind in ('generic', 'cdj'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as area:
                root = Path(area).resolve()
                target = root / 'device'
                target.mkdir()
                session = (DeviceSession(target, []) if kind == 'generic' else
                           CdjSession(target, [], token=target.stat().st_dev, volume_uuid='fixture-volume'))
                destination = target / 'Contents' / 'selected.wav'
                destination.parent.mkdir()
                destination.write_bytes(b'previous generation')
                outside = root / 'outside.wav'
                outside.write_bytes(b'outside original')
                occupant = destination.parent / '.soundbox-replaced.sbpart'
                fsync = os.fsync
                def swap(fd):
                    fsync(fd)
                    occupant.unlink()
                    occupant.symlink_to(outside)
                with mock.patch.object(device_staging.secrets, 'token_hex', return_value='replaced'), \
                        mock.patch.object(device_staging.os, 'fsync', side_effect=swap):
                    with self.assertRaisesRegex(RuntimeError, 'temporary file changed'):
                        session.atomic_write(destination, b'new selected export')
                self.assertEqual(destination.read_bytes(), b'previous generation')
                self.assertEqual(outside.read_bytes(), b'outside original')
                self.assertEqual(occupant.readlink(), outside)

    def test_exclusive_creation_refuses_a_preexisting_random_staging_name(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            session = DeviceSession(root / 'device', [])
            outside = root / 'original'
            outside.write_bytes(b'outside original')
            occupant = session.target / '.soundbox-collision.sbpart'
            occupant.symlink_to(outside)
            with mock.patch.object(device_staging.secrets, 'token_hex', return_value='collision'):
                with self.assertRaises(FileExistsError):
                    session.atomic_write(session.target / 'selected.wav', b'new')
            self.assertEqual(outside.read_bytes(), b'outside original')
            self.assertTrue(occupant.is_symlink())
            self.assertFalse((session.target / 'selected.wav').exists())

    def test_failure_and_cancellation_retire_only_the_owned_temporary(self):
        for operation in ('write-flush', 'copy-corruption', 'concat-missing', 'cdj-cancel', 'cdj-hash'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as area:
                root = Path(area).resolve()
                target = root / 'device'
                target.mkdir()
                session = DeviceSession(target, [])
                destination = target / 'Contents' / 'selected.wav'
                destination.parent.mkdir()
                destination.write_bytes(b'previous generation')
                previous_stage = destination.with_name(destination.name + '.sbpart')
                previous_stage.write_bytes(b'previous interrupted stage')
                source = root / 'candidate'
                source.write_bytes(b'candidate payload')
                with self.assertRaises((OSError, RuntimeError, ValueError)):
                    if operation == 'write-flush':
                        with mock.patch.object(device_staging.os, 'fsync', side_effect=OSError('flush failed')):
                            session.atomic_write(destination, b'new')
                    elif operation == 'copy-corruption':
                        def corrupt(_source, output):
                            output.write(b'damaged candidate')
                        with mock.patch('soundbox.sync.copy_stream', side_effect=corrupt):
                            session.atomic_copy(source, destination)
                    elif operation == 'concat-missing':
                        session.atomic_concat([source, root / 'missing'], destination)
                    else:
                        cdj = CdjSession(target, [], token=target.stat().st_dev, volume_uuid='fixture-volume')
                        cdj.atomic_copy(source, destination,
                                        should_cancel=lambda: operation == 'cdj-cancel',
                                        expected_sha256=hashlib.sha256(b'wrong').hexdigest())
                self.assertEqual(destination.read_bytes(), b'previous generation')
                self.assertEqual(source.read_bytes(), b'candidate payload')
                self.assertEqual(previous_stage.read_bytes(), b'previous interrupted stage')
                self.assertEqual(list(destination.parent.glob('.soundbox-*.sbpart')), [])

    def test_parent_walk_rejects_dotdot_before_creating_any_directory(self):
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            session = DeviceSession(root / 'device', [])
            with self.assertRaisesRegex(RuntimeError, 'Invalid device publication path'):
                session.atomic_write(session.target / 'new' / '..' / 'selected.wav', b'new')
            self.assertFalse((session.target / 'new').exists())
            self.assertFalse((session.target / 'selected.wav').exists())

    def test_fast_copy_and_unsupported_fast_copy_fallback_preserve_exact_bytes(self):
        import posix
        for fallback in (False, True):
            with self.subTest(fallback=fallback), tempfile.TemporaryDirectory() as area:
                root = Path(area).resolve()
                source = root / 'source'
                raw = bytes(range(256)) * 8193
                source.write_bytes(raw)
                session = DeviceSession(root / 'device', [])
                destination = session.target / 'selected.wav'
                native_copy = getattr(posix, '_fcopyfile', None)
                calls = []
                def fast_copy(input_fd, output_fd, flags):
                    calls.append((input_fd, output_fd))
                    if fallback:
                        os.write(output_fd, b'partial fast-copy output')
                        raise OSError(errno.ENOTSUP, 'unsupported destination')
                    return native_copy(input_fd, output_fd, flags)
                with mock.patch.object(posix, '_fcopyfile', side_effect=fast_copy, create=True), \
                        mock.patch.object(posix, '_COPYFILE_DATA', getattr(posix, '_COPYFILE_DATA', 8), create=True):
                    session.atomic_copy(source, destination)
                self.assertEqual(len(calls), 1)
                self.assertEqual(source.read_bytes(), raw)
                self.assertEqual(destination.read_bytes(), raw)

    def test_concat_preserves_multiple_segments_without_whole_file_copy(self):
        import posix
        with tempfile.TemporaryDirectory() as area:
            root = Path(area).resolve()
            source_root = root / 'sources'
            source_root.mkdir()
            sources = [source_root / 'first', source_root / 'second', source_root / 'third']
            payloads = [b'first' * 8193, b'second' * 16385, b'tail']
            for source, payload in zip(sources, payloads, strict=True):
                source.write_bytes(payload)
            session = DeviceSession(root / 'device', sources)
            destination = session.target / 'concatenated.ithmb'
            with mock.patch.object(posix, '_fcopyfile',
                    side_effect=AssertionError('Concatenation must preserve stream offsets'), create=True):
                session.atomic_concat(sources, destination)
            self.assertEqual(destination.read_bytes(), b''.join(payloads))
            self.assertEqual([source.read_bytes() for source in sources], payloads)
