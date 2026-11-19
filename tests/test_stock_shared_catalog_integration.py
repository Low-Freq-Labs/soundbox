"""Copied native shared covers across export capture, planning and publication."""
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import wave

from clickwheel.ipod import get_ipod_tracks, read_ipod
from clickwheel.ipod.ipod_models import capabilities_for_family_gen
from clickwheel.ipod.itunesdb_writer import ChecksumType, PlaylistInfo, TrackInfo, write_itunesdb
from PIL import Image

from soundbox import stock_ipod
from soundbox.artwork import ArtCache, ArtResult
from soundbox.export_snapshot import capture_export
from soundbox.library import Library, Playlist, Track
from soundbox.stock_artwork import StockArtworkCache, render_stock_artwork
from soundbox.stock_ipod_capture import capture_stock_ipod_native
from soundbox.stock_ipod_catalog import compare_stock_catalog
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_shadow import StockIpodShadowStore


class SharedCatalogIntegration(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory(); self.addCleanup(area.cleanup)
        self.base = Path(area.name).resolve()
        self.target = self.base / 'ipod'; self.target.mkdir()
        self.sources = self.base / 'sources'
        self.art = ArtCache(self.base / 'state/art')
        self.cache = StockArtworkCache(self.base / 'pixels')
        for local, color in (('alpha', 'red'), ('beta', 'red'), ('foreign', 'green')):
            self.cover(local, color)
        self.infos = [TrackInfo(title=title, artist='Artist', dbid=dbid,
            location=f':iPod_Control:Music:F00:{dbid}.wav', size=1808,
            length=10, filetype='wav', sample_rate=44100, rating=80,
            play_count=7, skip_count=2, composer='Untouched composer')
            for dbid, title in ((101, 'Alpha'), (102, 'Beta'), (103, 'Foreign'))]
        folder = self.target / 'iPod_Control/Artwork'; folder.mkdir(parents=True)
        rendered = render_stock_artwork([self.infos[0], self.infos[2]],
            {101: self.cache.prepare(self.art.image_path('alpha')),
             103: self.cache.prepare(self.art.image_path('foreign'))},
            cache=self.cache, existing_artwork_dir=self.base / 'absent')
        (folder / 'ArtworkDB').write_bytes(rendered.artwork_db)
        for item in rendered.ithmb_files:
            (folder / item.relative_path.name).write_bytes(
                b''.join(part.path.read_bytes() for part in item.segments))
        owner, alias, foreign = self.infos
        owner.artwork_size -= 1; foreign.artwork_size -= 1
        alias.mhii_link = owner.mhii_link
        alias.artwork_count = owner.artwork_count; alias.artwork_size = owner.artwork_size
        self.playlist = Playlist('Favorites', [], ['alpha', 'beta'], pid='native-favs',
                                 export_key='cust:FAVORITES')
        self.library = Library({local: Track(local, title, 'Artist',
            (self.sources / f'{local}.wav').as_uri(), total_time=0.01, size=1808,
            source_native_id=f'native-{local}', source_uuid=f'uuid-{local}')
            for local, title in (('alpha', 'Alpha'), ('beta', 'Beta'), ('foreign', 'Foreign'))},
            [self.playlist], self.sources / 'master.db', source_kind='rekordbox-db',
            source_library_id='generated-library')
        self.library.source_playlist_catalog = {'nodes': [
            {'ID': 'native-favs', 'UUID': 'playlist-uuid', 'rb_local_deleted': 0}]}
        self.write_catalog()
        pacer = mock.patch.object(stock_ipod, '_pace_classic_usb')
        pacer.start(); self.addCleanup(pacer.stop)

    def cover(self, local, color):
        data = io.BytesIO(); Image.new('RGB', (240, 240), color).save(data, 'JPEG')
        self.art.store_ok(local, ArtResult(data.getvalue(), 'image/jpeg', 1.0, '', local))

    def write_catalog(self, members=(101, 102)):
        (self.target / 'iPod_Control/iTunes').mkdir(parents=True, exist_ok=True)
        self.assertTrue(write_itunesdb(str(self.target), self.infos, backup=False,
            force_checksum=ChecksumType.NONE,
            playlists=[PlaylistInfo('Favorites', list(members), playlist_id=901),
                       PlaylistInfo('Foreign list', [103], playlist_id=902)],
            capabilities=capabilities_for_family_gen('iPod Video', '5.5th Gen')))

    def snapshot(self):
        return read_stock_ipod_snapshot(self.target, model_number='MA446',
            filesystem='fat32', volume_uuid='generated-volume',
            native_capture=capture_stock_ipod_native(self.target, include_artwork=True))

    def plan(self, *, witness=None):
        frozen = capture_export(self.library, [self.playlist], playlist_identities=True)
        inputs = frozen.materialize()
        self.assertEqual(inputs.library.source_playlist_catalog['nodes'][0]['UUID'], 'playlist-uuid')
        return stock_ipod.plan_stock_ipod_catalog_update(inputs.library, list(inputs.playlists),
            self.target, snapshot=self.snapshot(), model_number='MA446', filesystem='fat32',
            usb_family='iPod Video', art_cache=self.art, stock_art_cache=self.cache,
            update_metadata=True, incremental=True, catalog_witness=witness)

    def rows(self):
        return {row['dbid']: row for row in get_ipod_tracks(read_ipod(self.target))}

    def art_fields(self):
        return {dbid: tuple(row.get(key) for key in ('mhiiLink', 'artworkCount', 'artworkSize'))
                for dbid, row in self.rows().items()}

    def pixels(self):
        return {path.name: path.read_bytes()
                for path in (self.target / 'iPod_Control/Artwork').glob('*.ithmb')}

    def assert_prefixes(self, before):
        after = self.pixels()
        for name, payload in before.items():
            self.assertEqual(after[name][:len(payload)], payload)

    def test_shared_noop_is_byte_exact_after_gui_capture_and_fresh_verification(self):
        original = (self.target / 'iPod_Control/iTunes/iTunesDB').read_bytes()
        fields = self.art_fields(); pixels = self.pixels()
        plan = self.plan()
        self.assertFalse(plan.requires_commit)
        self.assertEqual(plan.rendered_database, original)
        self.assertEqual(plan.rendered_artwork.updated_dbids, ())
        stock_ipod.verify_stock_ipod_catalog_noop(plan)
        self.assertEqual(self.art_fields(), fields); self.assertEqual(self.pixels(), pixels)

    def test_changed_shared_owner_keeps_unselected_alias_and_foreign_cover(self):
        self.playlist.keys = ['alpha']; self.write_catalog((101,))
        before = self.art_fields(); pixels = self.pixels()
        self.cover('alpha', 'blue')
        plan = self.plan(); stock_ipod.commit_stock_ipod_sync(plan)
        fields = self.art_fields()
        self.assertNotEqual(fields[101][0], before[101][0])
        self.assertEqual(fields[102], before[102]); self.assertEqual(fields[103], before[103])
        self.assert_prefixes(pixels)
        repeated = self.plan(witness=plan.catalog_witness)
        self.assertFalse(repeated.requires_commit)
        stock_ipod.verify_stock_ipod_catalog_noop(repeated)

    def test_both_shared_tracks_get_independent_art_and_metadata_then_warm_noop(self):
        before = self.rows(); pixels = self.pixels()
        self.cover('alpha', 'blue'); self.cover('beta', 'yellow')
        frozen = capture_export(self.library, [self.playlist],
            titles={'alpha': 'Curated Alpha'}, playlist_identities=True).materialize()
        plan = stock_ipod.plan_stock_ipod_catalog_update(frozen.library, list(frozen.playlists),
            self.target, snapshot=self.snapshot(), model_number='MA446', filesystem='fat32',
            usb_family='iPod Video', art_cache=self.art, stock_art_cache=self.cache,
            title_overrides=frozen.titles, update_metadata=True, incremental=True)
        self.assertFalse(plan.copies or plan.rewrites or plan.media_deletes)
        stock_ipod.commit_stock_ipod_sync(plan)
        rows = self.rows()
        self.assertEqual(rows[101]['title'], 'Curated Alpha')
        self.assertNotEqual(rows[101]['mhiiLink'], rows[102]['mhiiLink'])
        for dbid in before:
            for key in ('location', 'playCount', 'skipCount', 'rating', 'Composer'):
                self.assertEqual(rows[dbid].get(key), before[dbid].get(key))
        self.assert_prefixes(pixels)
        self.library.tracks_by_id['alpha'].name = 'Curated Alpha'
        repeated = self.plan(witness=plan.catalog_witness)
        self.assertFalse(repeated.requires_commit)
        stock_ipod.verify_stock_ipod_catalog_noop(repeated)

    def test_unknown_extra_survives_partial_sync_and_remains_honestly_pending(self):
        self.write_catalog((101, 102, 103)); pixels = self.pixels()
        self.cover('alpha', 'blue')
        plan = self.plan(); self.assertTrue(plan.catalog_warning)
        self.assertFalse(plan.copies or plan.rewrites or plan.media_deletes)
        stock_ipod.commit_stock_ipod_sync(plan)
        snapshot = self.snapshot()
        favorites = next(row for row in snapshot.playlists if row.playlist_id == 901)
        self.assertEqual(tuple(row.dbid for row in favorites.entries), (101, 102, 103))
        comparison = compare_stock_catalog(self.library, [self.playlist], snapshot,
            incremental=True, catalog_witness=plan.catalog_witness)
        self.assertTrue(comparison.needs_sync)
        self.assertEqual(comparison.playlist_changes[0].unknown_device_ids, (103,))
        self.assert_prefixes(pixels)

    def test_associated_addition_acquires_only_new_audio_without_retained_scan(self):
        first = self.plan(); stock_ipod.verify_stock_ipod_catalog_noop(first)
        new = self.sources / 'new.wav'; self.wave(new, 25)
        self.library.tracks_by_id['new'] = Track('new', 'New', 'Artist', new.as_uri())
        self.playlist.keys.insert(1, 'new'); self.cover('new', 'yellow')
        acquire = stock_ipod.acquire_source; acquired = []
        def observe(contract, *args, **kwargs):
            self.assertEqual(contract.canonical, new)
            acquired.append(contract.canonical)
            return acquire(contract, *args, **kwargs)
        with mock.patch.object(stock_ipod, 'acquire_source', side_effect=observe), \
                mock.patch.object(Path, 'glob', side_effect=AssertionError('media scan')), \
                mock.patch.object(Path, 'rglob', side_effect=AssertionError('media scan')):
            plan = self.plan(witness=first.catalog_witness)
            self.assertEqual(len(plan.copies), 1)
            self.assertEqual(plan.reused_track_count, 2)
            stock_ipod.commit_stock_ipod_sync(plan)
        self.assertEqual(acquired, [new, new])
        fresh = self.snapshot()
        comparison = compare_stock_catalog(self.library, [self.playlist], fresh,
            incremental=True, catalog_witness=plan.catalog_witness)
        self.assertFalse(comparison.needs_sync)
        for dbid in (101, 102, 103):
            self.assertFalse((self.target / f'iPod_Control/Music/F00/{dbid}.wav').exists())
        self.assertEqual((self.target / plan.copies[0].relative_path).read_bytes(), new.read_bytes())

    @staticmethod
    def wave(path, sample):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(2); handle.setsampwidth(2); handle.setframerate(44100)
            handle.writeframes(sample.to_bytes(2, 'little', signed=True) * 2 * 441)

    def test_full_sync_shadow_tracks_logical_alias_after_original_owner_splits(self):
        for dbid, local, sample in ((101, 'alpha', 25), (102, 'beta', 50), (103, 'foreign', 75)):
            source = self.sources / f'{local}.wav'; self.wave(source, sample)
            destination = self.target / f'iPod_Control/Music/F00/{dbid}.wav'
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, destination)
        self.cover('alpha', 'blue')
        store = StockIpodShadowStore(self.base / 'shadow')
        common = dict(model_number='MA446', filesystem='fat32', usb_family='iPod Video',
            art_cache=self.art, stock_art_cache=self.cache,
            identity_key='stock-ipod-v1:generated:shadow', shadow_store=store)
        first = stock_ipod.plan_stock_ipod_sync(self.library, [self.playlist], self.target, **common)
        self.assertFalse(first.copies)
        stock_ipod.commit_stock_ipod_sync(first)
        rows = self.rows(); shadow = store.load(common['identity_key'])
        self.assertIsNotNone(shadow)
        self.assertNotEqual(rows[101]['mhiiLink'], rows[102]['mhiiLink'])
        for dbid, local in ((101, 'alpha'), (102, 'beta')):
            self.assertEqual(shadow.artwork[str(dbid)]['image_id'], rows[dbid]['mhiiLink'])
            self.assertEqual(shadow.artwork[str(dbid)]['track_id'], local)
        warm = stock_ipod.plan_stock_ipod_sync(self.library, [self.playlist], self.target, **common)
        self.assertTrue(warm.shadow_fast_path); self.assertFalse(warm.requires_commit)
        self.cover('alpha', 'yellow')
        changed = stock_ipod.plan_stock_ipod_sync(self.library, [self.playlist], self.target, **common)
        self.assertEqual(changed.rendered_artwork.updated_dbids, (101,))
        self.assertFalse(changed.copies or changed.rewrites)
        stock_ipod.commit_stock_ipod_sync(changed)
        last = stock_ipod.plan_stock_ipod_sync(self.library, [self.playlist], self.target, **common)
        self.assertFalse(last.requires_commit)
