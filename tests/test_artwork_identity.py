"""Generated ownership, saved-owner migration and source-ABA controls."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from soundbox.artwork import ArtCache, ArtResult
from soundbox.artwork_identity import prepare_legacy_restore, validate_scoped_artwork
from soundbox.library import Library, Track
from soundbox.state_io import StateChangedError


def image(color):
    out = BytesIO()
    Image.new('RGB', (32, 32), color).save(out, 'JPEG')
    return out.getvalue()


def result(color):
    return ArtResult(image(color), 'image/jpeg', 1., 'https://example.test/cover', 'Cover')


def library(source='A', *, uuid='uuid-A', tid='1', path='/generated/song.wav'):
    track = Track(tid, 'Song', 'Artist', Path(path).as_uri(), source_uuid=uuid)
    return Library({tid: track}, [], Path('/generated') / source, source_kind='rekordbox-db',
                   source_library_id='library-' + source)


class ArtworkIdentity(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name) / 'art'
        self.legacy = ArtCache(self.root)

    def test_same_numeric_id_and_different_source_have_independent_covers_and_blocks(self):
        a = self.legacy.for_library(library())
        b = a.for_library(library('B', uuid='uuid-B'))
        a.store_ok('1', result('red'))
        self.assertIsNone(b.image_path('1'))
        b.store_ok('1', result('blue'))
        self.assertNotEqual(a.image_path('1'), b.image_path('1'))
        a_bytes = a.image_path('1').read_bytes()
        b.block('1')
        self.assertEqual(a.image_path('1').read_bytes(), a_bytes)
        self.assertEqual(b.status('1'), 'blocked')

    def test_same_source_reused_id_and_local_path_are_separate_owners(self):
        a = self.legacy.for_library(library())
        a.store_ok('1', result('red'))
        replacement = a.for_library(library(uuid='uuid-new'))
        self.assertIsNone(replacement.image_path('1'))
        replacement.store_none('1')
        self.assertEqual(a.for_library(library()).status('1'), 'ok')
        old_local = a.for_library(library(uuid='', path='/generated/old.wav'))
        old_local.store_ok('1', result('green'))
        self.assertIsNone(old_local.for_library(library(uuid='', path='/generated/new.wav')).image_path('1'))

    def test_title_artist_refresh_reuses_cache_and_selected_manual_owner(self):
        lib = library()
        a = self.legacy.for_library(lib)
        a.store_ok('1', result('red'))
        token = a.selection_token(['1'])
        refreshed = library()
        refreshed.tracks_by_id['1'].name = 'A different title'
        refreshed.tracks_by_id['1'].artist = 'A different artist'
        self.assertIs(a.for_library(refreshed), a)
        self.assertTrue(a.selection_current(token))

    def test_alias_appearance_and_disappearance_never_rekeys_existing_cover(self):
        original = library()
        a = self.legacy.for_library(original)
        a.store_ok('1', result('red'))
        original_path = a.image_path('1')
        augmented = library()
        augmented.tracks_by_id['archived-alias'] = Track('archived-alias', 'Song', 'Artist',
            'file:///generated/song.wav', source_uuid='uuid-A')
        b = a.for_library(augmented)
        self.assertEqual(b.image_path('1'), original_path)
        self.assertIsNone(b.image_path('archived-alias'))
        self.assertEqual(b.for_library(original).image_path('1'), original_path)

    def test_stable_import_id_keeps_cover_after_conversion_and_native_registration(self):
        tid = 'sb-012345abcdef'
        original = library(uuid='', tid=tid, path='/generated/original.wav')
        original.tracks_by_id[tid].imported = True
        a = self.legacy.for_library(original)
        a.store_ok(tid, result('red'))
        converted = library(uuid='', tid=tid, path='/generated/converted.mp3')
        converted.tracks_by_id[tid].imported = True
        self.assertIs(a.for_library(converted), a)
        registered = library(uuid='new-native-uuid', tid=tid, path='/generated/converted.mp3')
        registered.tracks_by_id[tid].source_native_id = '99'
        self.assertIs(a.for_library(registered), a)
        self.assertEqual(a.cover_snapshot(tid).data, result('red').data)

    def test_adoption_augmented_with_historical_alias_retains_current_only_cover(self):
        self.legacy.store_ok('1', result('red'))
        saved = library()
        saved.tracks_by_id['archived-alias'] = Track('archived-alias', 'Song', 'Artist',
            'file:///generated/song.wav', source_uuid='uuid-A')
        adopted = self.legacy.adopt_legacy(saved)
        self.assertEqual(adopted.for_library(library()).cover_snapshot('1').data, result('red').data)

    def test_source_aba_old_worker_cannot_publish_to_reopened_source(self):
        a = self.legacy.for_library(library())
        token = a.selection_token(['1'])
        a.retire()
        b = a.for_library(library('B', uuid='B'))
        reopened = b.for_library(library())
        self.assertIsNot(reopened, a)
        with self.assertRaises(StateChangedError):
            a.store_auto_if_unfetched('1', result('red'))
        self.assertFalse(a.selection_current(token))
        reopened.store_ok('1', result('blue'))
        self.assertEqual(reopened.status('1'), 'ok')

    def test_failed_atomic_publication_has_no_ghost_row_or_file(self):
        a = self.legacy.for_library(library())
        with patch.object(a._area, 'publish', side_effect=OSError('generated failure')):
            with self.assertRaises(OSError):
                a.store_ok('1', result('red'))
        self.assertEqual(a.index, {})
        self.assertEqual(a._stored_entries, {})
        a.store_none('1')
        self.assertEqual(a.for_library(library('B', uuid='B')).for_library(library()).status('1'), 'none')
        self.assertFalse(a._image_file('1', 'jpg').exists())

    def test_saved_owner_adoption_preserves_all_originals_and_recovery_and_defaults(self):
        saved = library()
        for tid in ('miss', 'block', 'fallback'):
            saved.tracks_by_id[tid] = Track(tid, tid, 'Artist', f'file:///generated/{tid}.wav', source_uuid=tid)
        self.legacy.store_ok('1', result('red'))
        before = self.legacy.cover_snapshot('1')
        self.legacy.apply_review('1', before, result('blue'), context=('Artist', 'Song'))
        recovery = self.root / 'source-recovery'
        recovery.mkdir()
        (recovery / '1.json').write_text(json.dumps(
            {'before': before.entry, 'before_sha256': before.digest}))
        (recovery / '1.image').write_bytes(before.data)
        (recovery / '1.review.json').write_bytes((self.root / 'review/1.json').read_bytes())
        (recovery / '1.review.image').write_bytes((self.root / 'review/1.image').read_bytes())
        self.legacy.store_none('miss')
        self.legacy.block('block')
        self.legacy.store_ok('unknown', result('purple'))
        generic = self.root.parent / 'generic.jpg'
        generic.write_bytes(image('green'))
        self.legacy.import_default_photos([generic])
        self.legacy.configure_default_tracks(saved.tracks_by_id)
        fallback_bytes = self.legacy.default_image_path('fallback').read_bytes()
        self.legacy.exclude_source('https://soundcloud.com/excluded/song')
        before_files = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        first_b = self.legacy.for_library(library('B', uuid='B'))
        self.assertIsNone(first_b.canonical_image_path('1'))
        adopted = self.legacy.adopt_legacy(saved)
        self.assertEqual(adopted.cover_snapshot('1'), self.legacy.cover_snapshot('1'))
        self.assertTrue(adopted.has_review_undo('1'))
        for suffix in ('json', 'image', 'review.json', 'review.image'):
            self.assertEqual((adopted._records_root / 'source-recovery' /
                f"{adopted._track_key('1')}.{suffix}").read_bytes(),
                (recovery / f'1.{suffix}').read_bytes())
        self.assertEqual(adopted.kept_covers(), self.legacy.kept_covers())
        self.assertEqual(adopted.status('miss'), 'none')
        self.assertEqual(adopted.status('block'), 'blocked')
        self.assertEqual(adopted.source_exclusions(), ('excluded',))
        adopted.configure_default_tracks(saved.tracks_by_id)
        self.assertEqual(adopted.default_image_path('fallback').read_bytes(), fallback_bytes)
        without_history = library()
        self.assertEqual(adopted.for_library(without_history).historical_image_path(
            'fallback', source_uuid='fallback').read_bytes(), fallback_bytes)
        for relative, original in before_files.items():
            if relative != 'defaults/index.json':
                self.assertEqual((self.root / relative).read_bytes(), original, relative)
        manifest = json.loads((self.root / 'ownership.json').read_text())
        self.assertEqual(manifest['unmapped'], ['unknown'])
        self.assertEqual(set(manifest['adopted']), {'1', 'miss', 'block'})
        adopted.undo_review('1', adopted.cover_snapshot('1'))
        self.assertEqual(adopted.cover_snapshot('1').data, before.data)
        self.assertTrue(self.legacy.has_review_undo('1'))
        validate_scoped_artwork(self.root)
        with self.assertRaises(ValueError):
            self.legacy.adopt_legacy(library('B', uuid='B'))

    def test_unknown_legacy_is_never_adopted_by_first_open_and_explicit_adoption_is_idempotent(self):
        self.legacy.store_ok('1', result('red'))
        b = self.legacy.for_library(library('B', uuid='B'))
        self.assertIsNone(b.image_path('1'))
        a = self.legacy.adopt_legacy(library())
        a.store_ok('1', result('blue'))
        before = a.cover_snapshot('1')
        again = self.legacy.adopt_legacy(library())
        self.assertEqual(again.cover_snapshot('1'), before)

    def test_historical_adopted_uuid_remains_visible_but_reused_id_cannot_borrow(self):
        self.legacy.store_ok('1', result('red'))
        adopted = self.legacy.adopt_legacy(library())
        empty = library()
        empty.tracks_by_id.clear()
        current = adopted.for_library(empty)
        self.assertEqual(current.historical_image_path('1', source_uuid='uuid-A').read_bytes(), image('red'))
        replacement = current.for_library(library(uuid='replacement'))
        self.assertIsNone(replacement.historical_image_path('1', source_uuid='uuid-A'))

    def test_global_photo_pool_and_policy_shared_but_source_assignments_are_not_erased(self):
        generic = self.root.parent / 'generic.jpg'
        generic.write_bytes(image('green'))
        a = self.legacy.for_library(library())
        a.import_default_photos([generic])
        a.configure_default_tracks(['1'])
        a.exclude_source('https://soundcloud.com/excluded/song')
        b = a.for_library(library('B', uuid='B'))
        b.configure_default_tracks(['1'])
        raw = json.loads((self.root / 'defaults/index.json').read_text())
        self.assertEqual(len(raw['assignments']), 2)
        self.assertEqual(a.default_image_path('1').read_bytes(), b.default_image_path('1').read_bytes())
        self.assertEqual(b.source_exclusions(), ('excluded',))
        self.assertEqual(len(b.default_photos()), 1)

    def test_persisted_default_choice_is_ready_before_unrelated_view_handoff(self):
        a = self.legacy.for_library(library())
        paths = []
        for color in ('red', 'blue', 'green'):
            path = self.root.parent / f'{color}.jpg'
            path.write_bytes(image(color))
            paths.append(path)
        a.import_default_photos(paths)
        a.configure_default_tracks(['1'])
        choice = a._default_assignments['1']
        token = a.selection_token(['1'])
        added = library()
        added.tracks_by_id['neighbor'] = Track('neighbor', 'Other', '', 'file:///generated/other.wav')
        b = a.for_library(added)
        self.assertEqual(b._default_assignments['1'], choice)
        self.assertEqual(b.default_image_path('1'), a.default_image_path('1'))
        self.assertIsNotNone(b.handoff_selection(token))

    def test_scope_owner_survives_compatible_fork_but_not_source_aba_or_restore(self):
        a = self.legacy.for_library(library())
        changed = library()
        changed.tracks_by_id['neighbor'] = Track('neighbor', 'Other', '', 'file:///generated/other.wav')
        compatible = a.for_library(changed)
        self.assertIs(compatible.scope_owner, a.scope_owner)
        b = compatible.for_library(library('B', uuid='B'))
        again = b.for_library(library())
        self.assertIsNot(again.scope_owner, a.scope_owner)
        again._area.generation += 1
        restored = again.for_library(library())
        self.assertIsNot(restored.scope_owner, again.scope_owner)

    def test_validator_rejects_missing_image_and_wrong_identity_key(self):
        a = self.legacy.for_library(library())
        a.store_ok('1', result('red'))
        validate_scoped_artwork(self.root)
        path = a.image_path('1')
        data = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            validate_scoped_artwork(self.root)
        path.write_bytes(data)
        raw = json.loads(a._index_path.read_text())
        next(iter(raw['tracks'].values()))['identity'] = ['uuid', 'other']
        a._index_path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            validate_scoped_artwork(self.root)

    def test_proved_legacy_restore_resets_selected_source_including_absence_only(self):
        current = library()
        current.tracks_by_id['2'] = Track('2', 'Second', 'Artist', 'file:///generated/2.wav', source_uuid='uuid-2')
        a = self.legacy.for_library(current)
        a.store_ok('1', result('red'))
        a.store_ok('2', result('green'))
        b = a.for_library(library('B', uuid='B'))
        b.store_ok('1', result('purple'))
        b_files = {p: p.read_bytes() for p in b._records_root.rglob('*') if p.is_file()}
        selected = ArtCache(self.root.parent / 'selected' / 'art')
        selected.store_ok('1', result('blue'))
        receipt = prepare_legacy_restore(selected.root, self.root,
            source_identity=a.source_identity, track_identities=a._bindings)
        self.assertEqual(receipt['adopted'], 1)
        reopened = ArtCache(self.root).for_library(current)
        self.assertEqual(reopened.cover_snapshot('1').data, result('blue').data)
        self.assertIsNone(reopened.image_path('2'))
        for path, data in b_files.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual((selected.root / '1.jpg').read_bytes(), result('blue').data)

    def test_unproved_legacy_restore_rejects_before_any_staged_mutation(self):
        a = self.legacy.for_library(library())
        a.store_ok('1', result('red'))
        selected = ArtCache(self.root.parent / 'selected' / 'art')
        selected.store_ok('unproved', result('blue'))
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'unproved song owner'):
            prepare_legacy_restore(selected.root, self.root,
                source_identity=a.source_identity, track_identities=a._bindings)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
