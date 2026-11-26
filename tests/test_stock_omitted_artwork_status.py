"""Stock-only omissions never create impossible artwork sync obligations."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import unittest

from PIL import Image
import soundfile
from clickwheel.ipod import get_ipod_tracks, read_ipod

from soundbox import stock_ipod
from soundbox.artwork import ArtCache, ArtResult
from soundbox.library import Track
from soundbox.stock_ipod_info import read_stock_ipod_snapshot
from soundbox.stock_ipod_shadow import StockIpodShadowStore
from tests import test_stock_ipod as fixtures


def cover(color):
    data = BytesIO()
    Image.new('RGB', (64, 64), color).save(data, 'JPEG')
    return ArtResult(data.getvalue(), 'image/jpeg', 1., 'file:///generated/manual.jpg', 'Manual')


class StockOmittedArtworkStatus(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.StockIpodSync()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.library.source = self.f.sources / 'master.db'
        self.f.library.source_kind = 'rekordbox-db'
        self.f.library.source_library_id = '123'
        self.f.library.source_playlist_catalog = {'nodes': [
            {'ID': self.f.playlist.pid, 'UUID': 'generated-playlist', 'rb_local_deleted': 0}]}
        wav = self.f.sources / 'stock-unsupported.wav'
        soundfile.write(wav, [[0., 0.]] * 441, 44100, format='WAV', subtype='PCM_24')
        mp3 = self.f.sources / 'stock-unsupported.mp3'
        # Mutagen parses these real MPEG-1 Layer II frames; stock requires Layer III.
        mp3.write_bytes((b'\xff\xfd\x80\x64' + bytes(413)) * 100)
        m4a = self.f.sources / 'stock-unsupported.m4a'
        # Stock rejects the container before media inspection, independent of codec.
        m4a.write_bytes(b'\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A mp42')
        self.omitted = {}
        for index, path in enumerate((wav, mp3, m4a)):
            tid = 'omitted-' + path.suffix[1:]
            self.f.library.tracks_by_id[tid] = Track(tid, 'Generated omitted ' + path.suffix,
                'Generated', path.as_uri(), source_uuid=f'00000000-0000-4000-8000-{index + 1:012d}')
            self.f.playlist.keys.append(tid)
            self.omitted[tid] = path
        self.art = ArtCache(self.f.base / 'state/art').for_library(self.f.library)
        self.art.store_ok('new', cover('green'))
        for tid in self.omitted:
            self.art.store_ok(tid, cover('red'))
        self.shadow = StockIpodShadowStore(self.f.base / 'shadow')
        self.identity = read_stock_ipod_snapshot(self.f.ipod, model_number='MA446', filesystem='fat32',
                                                volume_uuid='generated-volume').identity_key

    def sync(self):
        self.display_rows = {tid: dict(id=tid, title=track.name, artist=track.artist, album='',
                                      genre='', comments='', year=0, bpm=0)
                             for tid, track in self.f.library.tracks_by_id.items()}
        plan = stock_ipod.plan_stock_ipod_sync(self.f.library, [self.f.playlist], self.f.ipod,
            model_number='MA446', filesystem='fat32', usb_family='iPod Video', identity_key=self.identity,
            shadow_store=self.shadow, art_cache=self.art, stock_art_cache=self.f.stock_art_cache,
            display_rows=self.display_rows)
        return plan, stock_ipod.commit_stock_ipod_sync(plan)

    def status(self, witness):
        native = stock_ipod.capture_stock_ipod_native(self.f.ipod, include_artwork=True)
        return stock_ipod.inspect_stock_ipod_sync_status(self.f.library, self.f.ipod,
            identity_key=self.identity, model_number='MA446', serial_number='', shadow_store=self.shadow,
            native_capture=native, catalog_witness=witness, art_cache=self.art,
            display_rows=self.display_rows)

    def test_first_sync_with_unsupported_wav_mp3_m4a_covers_is_clean(self):
        original = {path: sha256(path.read_bytes()).hexdigest() for path in self.omitted.values()}
        plan, result = self.sync()
        self.assertEqual({row.track_id for row in plan.omissions}, set(self.omitted))
        self.assertEqual(len(get_ipod_tracks(read_ipod(self.f.ipod))), 2)
        self.assertFalse(set(self.omitted) & {row.local_id for row in result.catalog_witness.rows})
        status = self.status(result.catalog_witness)
        self.assertEqual((status.state, status.local_changes, status.change_summary), ('up_to_date', 0, ()))
        self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in original}, original)

    def test_new_manual_cover_on_omitted_track_does_not_require_stock_sync(self):
        _plan, result = self.sync()
        self.art.store_ok('omitted-wav', cover('blue'))
        status = self.status(result.catalog_witness)
        self.assertEqual((status.state, status.local_changes), ('up_to_date', 0))

    def test_exported_track_without_old_art_still_detects_a_new_manual_cover(self):
        _plan, result = self.sync()
        self.art.store_ok('old', cover('blue'))
        status = self.status(result.catalog_witness)
        self.assertEqual((status.state, status.local_changes), ('local_changes', 1))
        self.assertEqual(status.change_summary, (('artwork', 1),))

    def test_witness_linked_export_without_shadow_source_mapping_keeps_its_art_obligation(self):
        _plan, result = self.sync()
        shadow = self.shadow.load(self.identity)
        self.shadow.save(replace(shadow, sources={path: row for path, row in shadow.sources.items()
                                                if row.get('track_id') != 'old'}))
        self.art.store_ok('old', cover('blue'))
        status = self.status(result.catalog_witness)
        self.assertEqual((status.state, status.local_changes), ('local_changes', 1))
        self.assertEqual(status.change_summary, (('artwork', 1),))

    def test_full_source_playlist_membership_changes_remain_pending(self):
        _plan, result = self.sync()
        self.f.playlist.keys.remove('omitted-wav')
        status = self.status(result.catalog_witness)
        self.assertEqual((status.state, status.local_changes), ('local_changes', 1))
        self.assertEqual(status.change_summary, (('playlist', 1),))
