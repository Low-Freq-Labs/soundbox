"""Detached current artwork observations preserve publisher and source fences."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from soundbox import artwork
from soundbox.artwork_identity import BoundArtCache
from soundbox.state_io import StateChangedError
from tests import test_artwork_identity as fixtures


class ArtworkObservationView(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name) / 'art'
        self.lib = fixtures.library()
        self.owner = artwork.ArtCache(self.root).for_library(self.lib)

    def alternate(self):
        return BoundArtCache(self.root, self.lib)

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def test_stale_view_reloads_complete_saved_rows_without_constructor_recovery_or_publication(self):
        self.owner.store_ok('1', fixtures.result('red'))
        old = self.owner.selection_token(['1'])
        original_index, revision = self.owner.index, self.owner._revision
        self.alternate().store_none('1')
        before = self.snapshot()
        with (mock.patch.object(artwork, 'state_area', side_effect=AssertionError('Constructed state')),
              mock.patch.object(BoundArtCache, '__init__', side_effect=AssertionError('Constructed cache')),
              mock.patch.object(self.owner._area, 'recover', side_effect=AssertionError('Recovered state')),
              mock.patch.object(self.owner._area, 'publish', side_effect=AssertionError('Published state')),
              mock.patch.object(artwork.ArtCache, '_rebuild_default_assignments',
                                side_effect=AssertionError('Rebalanced choices')),
              mock.patch.object(Path, 'mkdir', side_effect=AssertionError('Created directory'))):
            current = self.owner.observation_view(self.lib)
        self.assertTrue(current.read_only)
        self.assertEqual(current.status('1'), 'none')
        self.assertEqual(current._revision, self.owner._area.art_revision)
        self.assertIs(self.owner.index, original_index)
        self.assertEqual(self.owner._revision, revision)
        self.assertEqual(self.owner.status('1'), 'ok')
        self.assertFalse(self.owner.selection_current(old))
        self.assertTrue(current.selection_current(current.selection_token(['1'])))
        self.assertEqual(self.snapshot(), before)

    def test_current_owner_detaches_without_reading_metadata_or_invalidating_original_selection(self):
        token = self.owner.selection_token(['1'])
        with (mock.patch.object(artwork.ArtCache, '_load_defaults', side_effect=AssertionError('Reloaded defaults')),
              mock.patch.object(BoundArtCache, '_reload_index', side_effect=AssertionError('Reloaded index'))):
            current = self.owner.observation_view(self.lib)
        self.assertIsNot(current, self.owner)
        self.assertIs(current.scope_owner, self.owner.scope_owner)
        self.assertTrue(self.owner.selection_current(token))
        self.assertFalse(current.selection_current(token))
        self.assertTrue(current.selection_current(current.selection_token(['1'])))

    def test_saved_default_assignment_is_read_exactly_without_rebalance(self):
        image = self.root.parent / 'default.png'
        Image.new('RGB', (80, 80), 'blue').save(image)
        alternate = self.alternate()
        alternate.configure_default_tracks(['1'])
        alternate.import_default_photos([image])
        before = self.snapshot()
        with mock.patch.object(artwork.ArtCache, '_rebuild_default_assignments',
                               side_effect=AssertionError('Rebalanced choices')):
            current = self.owner.observation_view(self.lib)
        self.assertEqual(current._default_assignments, alternate._default_assignments)
        self.assertEqual(current.image_path('1'), alternate.image_path('1'))
        self.assertEqual(self.snapshot(), before)

    def test_current_source_policy_is_part_of_the_complete_detached_selection(self):
        result = artwork.ArtResult(fixtures.image('red'), 'image/jpeg', 1.,
                                   'https://soundcloud.com/artist/song', 'Cover')
        self.owner.store_ok('1', result)
        self.alternate().exclude_source('https://soundcloud.com/artist/song')
        current = self.owner.observation_view(self.lib)
        self.assertIsNone(current.image_path('1'))
        self.assertEqual(current._selection_exclusions, ('artist',))
        self.assertTrue(current.selection_token(['1'])._rows[0][3])

    def test_observation_cannot_publish_and_never_changes_the_publisher(self):
        current = self.owner.observation_view(self.lib)
        before = self.snapshot()
        with self.assertRaisesRegex(OSError, 'Restore a valid library'):
            current.store_none('1')
        self.assertFalse(self.owner.read_only)
        self.assertEqual(self.snapshot(), before)

    def test_retiring_origin_invalidates_existing_detached_tokens_and_refuses_new_views(self):
        current = self.owner.observation_view(self.lib)
        token = current.selection_token(['1'])
        self.owner.retire()
        self.assertFalse(current.selection_current(token))
        with self.assertRaises(StateChangedError):
            self.owner.observation_view(self.lib)
        with self.assertRaises(StateChangedError):
            current.observation_view(self.lib)

    def test_restore_generation_invalidates_detached_tokens_and_refuses_refresh(self):
        current = self.owner.observation_view(self.lib)
        token = current.selection_token(['1'])
        self.owner._area.generation += 1
        self.assertFalse(current.selection_current(token))
        with self.assertRaises(StateChangedError):
            self.owner.observation_view(self.lib)

    def test_different_source_or_song_binding_never_borrows_artwork(self):
        for library in (fixtures.library('B'), fixtures.library(uuid='replacement')):
            with self.subTest(source=str(library.source), uuid=library.tracks_by_id['1'].source_uuid):
                with self.assertRaisesRegex(ValueError, 'different source or song binding'):
                    self.owner.observation_view(library)

    def test_publication_after_observation_rejects_token_and_next_view_can_refresh(self):
        current = self.owner.observation_view(self.lib)
        token = current.selection_token(['1'])
        self.alternate().store_none('1')
        self.assertFalse(current.selection_current(token))
        refreshed = current.observation_view(self.lib)
        self.assertEqual(refreshed.status('1'), 'none')
        self.assertTrue(refreshed.selection_current(refreshed.selection_token(['1'])))

    def test_same_library_for_library_does_not_reuse_a_stale_art_revision(self):
        self.alternate().store_none('1')
        previous_revision = self.owner._revision
        current = self.owner.for_library(self.lib)
        self.assertIsNot(current, self.owner)
        self.assertIs(current.scope_owner, self.owner.scope_owner)
        self.assertEqual(current.status('1'), 'none')
        self.assertEqual(self.owner._revision, previous_revision)
        self.assertTrue(current.selection_current(current.selection_token(['1'])))
