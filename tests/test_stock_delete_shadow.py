"""Device-only Delete renews only proven Mac acceleration facts."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from soundbox import stock_ipod, stock_ipod_shadow
from soundbox.ipod_export_witness import rebase_export_witness
from soundbox.stock_ipod_shadow import StockIpodShadowStore, rebase_deleted_shadow
from tests import test_export_playlist_baseline as fixture


class DeletedShadow(unittest.TestCase):
    rows = fixture.SuccessiveExportWitnesses.rows
    export = fixture.SuccessiveExportWitnesses.export

    def setUp(self):
        fixture.SuccessiveExportWitnesses.setUp(self)
        cover = self.f.base / 'new-distinct.jpg'
        fixture.fixture.Image.new('RGB', (300, 300), (10, 210, 60)).save(cover, 'JPEG', quality=90)
        self.f.art_cache.paths['new'] = cover
        first = self.export([self.f.playlist], artwork=True)
        second = self.export([self.favorites], first.catalog_witness, artwork=True)
        self.delete(self.export([self.live], second.catalog_witness, artwork=True))

    def delete(self, result, *, extra_ids=(), integrate=False):
        self.result = result
        self.old = self.shadow.load(self.key)
        self.before = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        self.before_ithmb = self.ithmb()
        self.deleted_id = next(binding.device_playlist_id for binding in self.result.catalog_witness.bindings
                               if binding.key == self.f.playlist.pid)
        self.deleted_ids = [self.deleted_id, *extra_ids]
        self.deletion = stock_ipod.plan_stock_ipod_playlist_delete(self.f.ipod, self.deleted_ids,
            model_number='MA446', filesystem='msdos', usb_family='iPod Video',
            stock_art_cache=self.f.stock_art_cache,
            **(dict(catalog_witness=result.catalog_witness, identity_key=self.key, shadow_store=self.shadow)
               if integrate else {}))
        self.deletion_result = stock_ipod.commit_stock_ipod_playlist_delete(self.deletion)
        self.after = (self.f.ipod / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        artwork = self.f.ipod / 'iPod_Control/Artwork/ArtworkDB'
        self.art_sha = hashlib.sha256(artwork.read_bytes()).hexdigest() if artwork.exists() else ''

    def ithmb(self):
        result = {}
        for format_id in (1028, 1029):
            path = self.f.ipod / f'iPod_Control/Artwork/F{format_id}_1.ithmb'
            if path.is_file():
                st = path.stat()
                result[path.name] = dict(size=st.st_size, mtime_ns=st.st_mtime_ns)
        return result

    def arguments(self):
        rendered = self.deletion.rendered_artwork
        return dict(identity_key=self.key, model='MA446', serial_number='',
            previous_database=self.before, previous_artwork_sha256=self.old.artwork_database_sha256,
            previous_ithmb=self.before_ithmb, rendered=self.after, artwork_sha256=self.art_sha,
            ithmb=self.ithmb(), previous_witness=self.result.catalog_witness,
            deleted_playlist_ids=self.deleted_ids, artwork_updates={dbid:
                (slot.image_id, slot.source_size, {str(k): v for k, v in slot.offsets})
                for slot in (rendered.slots if rendered else ()) for dbid in slot.track_dbids or (slot.song_id,)})

    def test_actual_delete_and_reexport_reuses_retained_sources_without_audio_acquisition(self):
        arguments = self.arguments(); original = deepcopy(self.old)
        with mock.patch.object(Path, 'open', side_effect=AssertionError('Pure renewal opened a file')), \
                mock.patch.object(Path, 'stat', side_effect=AssertionError('Pure renewal statted a file')):
            renewed = rebase_deleted_shadow(self.old, **arguments)
        self.assertIsNotNone(renewed)
        self.assertEqual(self.old, original)
        self.assertEqual(renewed.sources, self.old.sources)
        self.assertEqual(set(renewed.playlists), {'cust:Favorites', 'cust:Live Sets'})
        self.assertEqual(renewed.generation, self.old.generation + 1)
        self.assertEqual(renewed.database_sha256, hashlib.sha256(self.after).hexdigest())
        self.assertEqual(renewed.artwork_database_sha256, self.art_sha)
        self.assertEqual(renewed.ithmb, self.ithmb())
        self.assertEqual({key: row['cache_key'] for key, row in renewed.artwork.items()},
                         {key: row['cache_key'] for key, row in self.old.artwork.items()})
        for path, row in renewed.sources.items():
            self.assertIsNot(row, self.old.sources[path])
            self.assertIsNot(row['source_epoch'], self.old.sources[path]['source_epoch'])
        self.shadow.save(renewed)
        previous = self.deletion.catalog_witness
        # The caller's Delete witness wiring is independent of the pure cache
        # helper; use its actual result once it has been renewed by commit.
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Retained source reacquired')):
            self.export([self.f.playlist], previous, artwork=True)
        self.assertEqual(self.last_plan.copies, ())

    def test_wrong_baseline_identity_ithmb_and_unknown_playlist_mapping_fall_back(self):
        arguments = self.arguments()
        cases = [dict(identity_key='different-device'), dict(model='MC297'),
                 dict(previous_database=self.before + b'changed'),
                 dict(previous_artwork_sha256='0' * 64),
                 dict(previous_ithmb={}), dict(deleted_playlist_ids=[]),
                 dict(previous_witness=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(rebase_deleted_shadow(self.old, **{**arguments, **changes}))
        missing = replace(self.result.catalog_witness, bindings=())
        self.assertIsNone(rebase_deleted_shadow(self.old, **{**arguments, 'previous_witness': missing}))
        with_serial = replace(self.old, serial_number='known-device')
        self.assertIsNone(rebase_deleted_shadow(with_serial, **{**arguments, 'serial_number': 'different'}))

    def test_replaced_source_record_or_unproven_art_offsets_do_not_create_fast_proof(self):
        arguments = self.arguments()
        for field, changed in (('device_path', 'iPod_Control/Music/F99/OTHER.wav'),
                               ('dbid', 999999), ('size', 999999)):
            with self.subTest(field=field):
                rows = deepcopy(self.old.sources); rows[next(iter(rows))][field] = changed
                self.assertIsNone(rebase_deleted_shadow(replace(self.old, sources=rows), **arguments))
        updates = dict(arguments['artwork_updates'])
        dbid = int(next(iter(self.old.artwork)))
        old_art = self.old.artwork[str(dbid)]
        updates[dbid] = (99999, old_art['source_size'], old_art['offsets'])
        self.assertIsNone(rebase_deleted_shadow(self.old, **{**arguments, 'artwork_updates': updates}))
        self.assertIsNone(rebase_deleted_shadow(self.old,
            **{**arguments, 'artwork_updates': {}, 'artwork_sha256': '1' * 64}))

    def test_actual_orphan_cleanup_prunes_only_deleted_source_and_preserves_survivor_offsets(self):
        prior = rebase_export_witness(self.result.catalog_witness, device_key=self.key,
            previous_database=self.before, previous_artwork_sha256=self.old.artwork_database_sha256,
            rendered=self.after, artwork_sha256=self.art_sha)
        restored = self.export([self.f.playlist], prior.witness, artwork=True)
        self.f.playlist.keys[:] = ['old']
        changed = self.export([self.f.playlist], restored.catalog_witness, artwork=True)
        self.favorites.keys[:] = ['new']; self.live.keys[:] = ['new']
        changed = self.export([self.favorites], changed.catalog_witness, artwork=True)
        changed = self.export([self.live], changed.catalog_witness, artwork=True)
        artwork=self.f.ipod/'iPod_Control/Artwork'
        pixels_before={path.name:path.read_bytes() for path in artwork.glob('*.ithmb')}
        self.delete(changed, extra_ids=(202,))  # Explicitly remove generated Keep Me too.
        self.assertEqual(self.deletion.deleted_track_count, 1)
        self.assertIsNotNone(self.deletion.rendered_artwork)
        renewed = rebase_deleted_shadow(self.old, **self.arguments())
        self.assertIsNotNone(renewed)
        source = str(self.f.new_source.resolve())
        dbid = renewed.sources[source]['dbid']; art_key = str(dbid)
        self.assertEqual(set(renewed.sources), {source})
        self.assertEqual(set(renewed.artwork), {art_key})
        self.assertEqual(renewed.sources[source], self.old.sources[source])
        slot = next(slot for slot in self.deletion.rendered_artwork.slots if dbid in slot.track_dbids)
        self.assertEqual(renewed.artwork[art_key]['offsets'], {str(key): value for key, value in slot.offsets})
        self.assertEqual(renewed.artwork[art_key]['offsets'], self.old.artwork[art_key]['offsets'])
        self.assertEqual(self.ithmb(),self.before_ithmb)
        self.assertEqual({path.name:path.read_bytes() for path in artwork.glob('*.ithmb')},pixels_before)
        self.assertEqual(renewed.artwork[art_key]['cache_key'], self.old.artwork[art_key]['cache_key'])
        self.assertEqual(set(renewed.playlists), {'cust:Favorites', 'cust:Live Sets'})

    def test_actual_delete_commit_publishes_verified_survivor_witness_and_fast_shadow(self):
        prior = rebase_export_witness(self.result.catalog_witness, device_key=self.key,
            previous_database=self.before, previous_artwork_sha256=self.old.artwork_database_sha256,
            rendered=self.after, artwork_sha256=self.art_sha)
        restored = self.export([self.f.playlist], prior.witness, artwork=True)
        restored = self.export([self.favorites], restored.catalog_witness, artwork=True)
        restored = self.export([self.live], restored.catalog_witness, artwork=True)
        self.delete(restored, integrate=True)
        self.assertTrue(self.deletion_result.shadow_updated, self.deletion_result.shadow_warning)
        self.assertFalse(self.deletion_result.shadow_warning)
        witness = self.deletion_result.catalog_witness
        self.assertEqual({binding.key for binding in witness.bindings}, {'cust:Favorites', 'cust:Live Sets'})
        current = self.shadow.load(self.key)
        self.assertEqual(current.database_sha256, hashlib.sha256(self.after).hexdigest())
        self.assertEqual(set(current.playlists), {'cust:Favorites', 'cust:Live Sets'})
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=AssertionError('Retained source reacquired')):
            reexported = self.export([self.f.playlist], witness, artwork=True)
        self.assertEqual(self.last_plan.copies, ())
        self.assertEqual(len(reexported.catalog_witness.bindings), 3)


class GuardedDeleteShadowStore(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        self.store = StockIpodShadowStore(self.root / 'shadow')
        self.store.committed(identity_key='generated-ipod', model='MA446', serial_number='',
            database_sha256='a' * 64, artwork_database_sha256='', ithmb={}, sources={}, artwork={}, playlists={})
        self.before = self.store.load('generated-ipod')
        self.path = self.store.path_for(self.before.identity_key)
        self.renewed = replace(self.before, generation=self.before.generation + 1, database_sha256='b' * 64)

    def test_exact_generation_renewal_and_retirement_preserve_other_device_cache(self):
        self.store.save(replace(self.before, identity_key='unrelated-device'))
        other = self.store.path_for('unrelated-device'); other_bytes = other.read_bytes()
        self.assertTrue(self.store.replace_if_current(self.before, self.renewed))
        self.assertEqual(self.store.load('generated-ipod'), self.renewed)
        self.assertNotIn('_storage_token', self.path.read_text())
        current = self.store.load('generated-ipod')
        self.assertTrue(self.store.replace_if_current(current, None))
        self.assertFalse(self.path.exists())
        self.assertEqual(other.read_bytes(), other_bytes)
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_newer_generation_and_foreign_same_bytes_replacement_are_not_retired(self):
        self.store.save(self.renewed)
        newer = self.path.read_bytes()
        self.assertFalse(self.store.replace_if_current(self.before, None))
        self.assertEqual(self.path.read_bytes(), newer)
        captured = self.store.load('generated-ipod')
        replacement = self.path.with_name('foreign'); replacement.write_bytes(newer)
        old = self.path.stat(); os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        os.replace(replacement, self.path)
        self.assertFalse(self.store.replace_if_current(captured, None))
        self.assertEqual(self.path.read_bytes(), newer)

    def test_parent_aliases_share_writer_serialization_without_following_the_leaf(self):
        alias = self.root / 'alias'; alias.symlink_to(self.store.root, target_is_directory=True)
        alias_store = StockIpodShadowStore(alias)
        alias_path = alias_store.path_for(self.before.identity_key)
        self.assertIs(stock_ipod_shadow._store_lock(self.path), stock_ipod_shadow._store_lock(alias_path))
        lock = stock_ipod_shadow._store_lock(self.path)
        displaced = self.root / 'displaced'; os.replace(self.path, displaced)
        target = self.root / 'foreign'; target.write_bytes(b'foreign')
        self.path.symlink_to(target)
        self.assertIs(stock_ipod_shadow._store_lock(self.path), lock)
        self.assertIsNone(alias_store.load(self.before.identity_key))

    def test_leaf_swap_inside_claim_restores_foreign_occupant_without_unlinking_it(self):
        move = stock_ipod_shadow._exclusive_cache_move; displaced = self.root / 'displaced-original'
        original = self.path.read_bytes(); foreign = b'foreign replacement must survive'
        fired = False
        def swap(parent, name, destination, destination_name):
            nonlocal fired
            if name == 'manifest.json' and not fired:
                fired = True
                os.replace(self.path, displaced); self.path.write_bytes(foreign)
            return move(parent, name, destination, destination_name)
        with mock.patch.object(stock_ipod_shadow, '_exclusive_cache_move', side_effect=swap):
            self.assertFalse(self.store.replace_if_current(self.before, None))
        self.assertTrue(fired)
        self.assertEqual(self.path.read_bytes(), foreign)
        self.assertEqual(displaced.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_racing_new_occupant_before_exclusive_publish_is_preserved(self):
        move = stock_ipod_shadow._exclusive_cache_move; foreign = b'newer racing cache'
        fired = False
        def occupy(parent, name, destination, destination_name):
            nonlocal fired
            if name == 'replacement.json':
                fired = True; self.path.write_bytes(foreign)
            return move(parent, name, destination, destination_name)
        with mock.patch.object(stock_ipod_shadow, '_exclusive_cache_move', side_effect=occupy):
            self.assertFalse(self.store.replace_if_current(self.before, self.renewed))
        self.assertTrue(fired)
        self.assertEqual(self.path.read_bytes(), foreign)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_symlink_hardlink_missing_or_uncaptured_cache_never_supplies_retirement_authority(self):
        self.assertFalse(self.store.replace_if_current(replace(self.before, _storage_token=None), None))
        outside = self.root / 'foreign'; outside.write_bytes(b'outside bytes')
        displaced = self.root / 'displaced'; os.replace(self.path, displaced)
        self.path.symlink_to(outside)
        self.assertIsNone(self.store.load(self.before.identity_key))
        with self.assertRaises(OSError):
            self.store.replace_if_current(self.before, None)
        self.assertTrue(self.path.is_symlink()); self.assertEqual(outside.read_bytes(), b'outside bytes')
        self.path.unlink(); os.replace(displaced, self.path)
        os.link(self.path, self.root / 'second-link')
        self.assertFalse(self.store.replace_if_current(self.before, None))
        self.assertIsNone(self.store.load(self.before.identity_key))
        self.path.unlink()
        self.assertFalse(self.store.replace_if_current(self.before, None))
