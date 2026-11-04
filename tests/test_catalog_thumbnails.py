"""Visible, bounded thumbnail presentation and closed-dialog callback safety."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6

from desktop.artistcatalog import ArtistCatalogDialog
from desktop.defaultphotos import DefaultPhotosDialog


class CatalogThumbnailPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root = Path(area.name)
        self.paths = []
        for index in range(80):
            path = self.root / f'{index}.jpg'
            Image.new('RGB', (500, 250), (index * 3, 90, 140)).save(path)
            self.paths.append(path)
        self.dialogs = []
        self.addCleanup(self.close)

    def close(self):
        for dialog in self.dialogs:
            if shiboken6.isValid(dialog):
                dialog.reject()
                dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def catalog(self):
        dialog = ArtistCatalogDialog([(str(i), [str(i)], path)
                                      for i, path in enumerate(self.paths)])
        self.dialogs.append(dialog)
        return dialog

    def settle(self, dialog):
        self.app.processEvents()
        deadline = time.monotonic() + 2
        while dialog._thumbnails._timer.isActive() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(dialog._thumbnails._timer.isActive())

    def test_constructor_does_not_read_images_and_visible_reads_are_scaled(self):
        from desktop import thumbnails
        original = thumbnails.QImageReader
        readers = []
        def reader(path):
            value = original(path)
            readers.append(value)
            return value
        with mock.patch.object(thumbnails, 'QImageReader', side_effect=reader):
            dialog = self.catalog()
            self.assertEqual(readers, [])
            dialog.show()
            self.settle(dialog)
            visible = dialog._thumbnails._visible()
            self.assertEqual(len(readers), len(visible))
            self.assertTrue(dialog.list.topLevelItem(0).icon(0).isNull() is False)
            self.assertTrue(dialog.list.topLevelItem(79).icon(0).isNull())
            side = round(96 * dialog.list.devicePixelRatioF())
            self.assertTrue(all(r.scaledSize().width() <= side and
                                r.scaledSize().height() <= side for r in readers))
            self.assertEqual(dialog.list.iconSize().width(), 96)

    def test_scroll_eviction_is_bounded_and_new_image_replaces_existing_row(self):
        dialog = self.catalog()
        dialog._thumbnails.capacity = 8
        dialog.show()
        self.settle(dialog)
        for index in range(0, 80, 6):
            dialog.list.scrollToItem(dialog.list.topLevelItem(index))
            self.settle(dialog)
            self.assertLessEqual(len(dialog._thumbnails._loaded),
                                 max(8, len(dialog._thumbnails._visible())))
        item = dialog.list.topLevelItem(79)
        dialog.list.scrollToItem(item)
        self.settle(dialog)
        self.assertFalse(item.icon(0).isNull())
        dialog._thumbnails.set_image(item, self.root / 'missing.jpg')
        self.settle(dialog)
        self.assertTrue(item.icon(0).isNull())
        self.assertFalse(dialog._thumbnails._timer.isActive())
        dialog._thumbnails.set_image(item, self.paths[0])
        self.settle(dialog)
        self.assertFalse(item.icon(0).isNull())
        self.assertTrue(dialog.list.topLevelItem(0).icon(0).isNull())

    def test_viewport_larger_than_cache_budget_settles_without_eviction_loop(self):
        dialog = self.catalog()
        dialog._thumbnails.capacity = 1
        dialog.show()
        self.settle(dialog)
        visible = dialog._thumbnails._visible()
        self.assertGreater(len(visible), 1)
        self.assertEqual(len(dialog._thumbnails._loaded), len(visible))
        self.assertTrue(all(not item.icon(0).isNull() for item in visible))
        self.assertFalse(dialog._thumbnails._timer.isActive())

    def test_default_repopulation_discards_previous_rows_before_deferred_load(self):
        rows = [dict(id=str(i), name=str(i), tracks=1, path=p)
                for i, p in enumerate(self.paths)]
        dialog = DefaultPhotosDialog(rows)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        dialog._populate([rows[-1]])
        self.settle(dialog)
        self.assertEqual(len(dialog._thumbnails._rows), 1)
        self.assertEqual(dialog.list.topLevelItemCount(), 1)
        self.assertFalse(dialog.list.topLevelItem(0).icon(0).isNull())
        self.assertEqual(dialog.list.topLevelItem(0).data(0, Qt.UserRole + 37), '79')

    def test_late_action_callbacks_after_close_and_delete_do_not_touch_widgets(self):
        pending = []
        artist = ArtistCatalogDialog([('Artist', ['1'], None)],
                                     lambda choice, done: pending.append(done))
        self.dialogs.append(artist)
        artist.list.setCurrentItem(artist.list.topLevelItem(0))
        artist._dispatch(dict(kind='rename', artist='Artist', new='Renamed', tids=['1']))
        defaults = DefaultPhotosDialog([], lambda choice, done: pending.append(done))
        self.dialogs.append(defaults)
        defaults._dispatch(dict(kind='add', paths=[]))
        for dialog in (artist, defaults):
            dialog.reject()
            self.assertFalse(dialog._thumbnails._timer.isActive())
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(artist))
        self.assertFalse(shiboken6.isValid(defaults))
        pending[0](True, self.paths[0])
        pending[1](True, [], 'Complete')

    def test_selection_keeps_actions_disabled_until_pending_action_finishes(self):
        pending = []
        artist = ArtistCatalogDialog([('A', ['1'], None), ('B', ['2'], None)],
                                     lambda choice, done: pending.append(done))
        self.dialogs.append(artist)
        artist.list.setCurrentItem(artist.list.topLevelItem(0))
        choice = dict(kind='url', artist='A', tids=['1'], url='fixture')
        artist._dispatch(choice)
        artist.list.setCurrentItem(artist.list.topLevelItem(1))
        self.assertFalse(artist.rename_btn.isEnabled())
        artist._dispatch(choice)
        self.assertEqual(len(pending), 1)
        pending.pop()(False, None)
        self.assertTrue(artist.rename_btn.isEnabled())
        defaults = DefaultPhotosDialog([dict(id='1', name='Photo', tracks=1, path=self.paths[0])],
                                       lambda choice, done: pending.append(done))
        self.dialogs.append(defaults)
        defaults._dispatch(dict(kind='add', paths=[]))
        defaults.list.setCurrentItem(defaults.list.topLevelItem(0))
        self.assertFalse(defaults.add_btn.isEnabled())
        self.assertFalse(defaults.delete_btn.isEnabled())
        defaults._dispatch(dict(kind='delete', ids=['1']))
        self.assertEqual(len(pending), 1)
        pending.pop()(False, None)
        self.assertTrue(defaults.delete_btn.isEnabled())


if __name__ == '__main__':
    unittest.main()
