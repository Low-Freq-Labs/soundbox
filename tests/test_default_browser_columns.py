"""Fresh browser geometry uses the reference proportions without touching saves."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QPoint, QRect, QStandardPaths, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider, QStyleOptionViewItem

from desktop.app import MainWindow
from desktop.model import COLUMNS
from soundbox.library import Library, Track
from soundbox.providers import Providers


class DefaultBrowserColumns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enterContext(mock.patch.object(QStandardPaths, 'isTestModeEnabled', return_value=True))
        self.enterContext(mock.patch.object(MainWindow, '_refresh_devices'))
        self.enterContext(mock.patch('soundbox.rekordbox_db.load_live', side_effect=AssertionError('Live source')))
        area = self.enterContext(tempfile.TemporaryDirectory(prefix='soundbox-default-columns-'))
        self.window = MainWindow(providers=Providers('public'), isolated_dir=Path(area))
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.shutdown)
        tracks = {str(i): Track(str(i), f'Generated track {i:03d}', 'Fixture Artist',
                  f'file:///generated/missing-{i}.wav', bpm=('145.00', '188.00')[i % 2],
                  key='1A', total_time=180)
                  for i in range(300)}
        self.window.lib = Library(tracks, [], 'generated-layout')
        self.window._apply_loaded_library()
        self.window.show()
        self.window._show_collection()
        self.app.processEvents()

    def settle(self):
        self.app.processEvents()
        self.app.processEvents()  # Dispatch the overlay's deferred geometry update.

    def assert_default_frame(self, view, header):
        self.assertEqual([header.logicalIndex(i) for i in range(header.count())
                          if not header.isSectionHidden(header.logicalIndex(i))], list(range(7)))
        self.assertEqual([header.sectionSize(i) for i in range(1, 7)], [170, 46, 62, 46, 60, 104])
        self.assertEqual(header.sectionSize(0), view.viewport().width() - 488)
        self.assertEqual(header.length(), view.viewport().width())
        self.assertEqual(header.sectionViewportPosition(6) + header.sectionSize(6), view.viewport().width())
        self.assertFalse(view.horizontalScrollBar().isVisible())
        self.assertEqual(view.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertEqual(view.viewport().geometry().right(), view.contentsRect().right())
        bar = view._soundbox_overlay_scrollbar
        self.assertTrue(bar.isVisible())
        self.assertGreater(bar.maximum(), bar.minimum())
        format_left = header.mapTo(view, QPoint(header.sectionViewportPosition(6), 0)).x()
        format_right = header.mapTo(view, QPoint(
            header.sectionViewportPosition(6) + header.sectionSize(6), 0)).x() - 1
        self.assertEqual(bar.geometry().right(), format_right)
        self.assertGreaterEqual(bar.x(), format_left)
        for value in (bar.minimum(), bar.maximum()):
            bar.setValue(value)
            option = QStyleOptionSlider(); option.initFrom(bar); option.orientation = Qt.Vertical
            option.minimum, option.maximum = bar.minimum(), bar.maximum()
            option.sliderPosition = option.sliderValue = value; option.pageStep = bar.pageStep()
            thumb = bar.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
            self.assertLessEqual(bar.mapTo(view, thumb.bottomRight()).x(), format_right)

    def test_fresh_regular_and_artists_frames_fit_reference_columns_at_multiple_widths(self):
        w = self.window
        for show, view, header in ((w._show_collection, w.table, w.table.horizontalHeader()),
                                   (w._show_artists, w.artists_view, w.artists_view.header())):
            show()
            if view is w.artists_view:
                view.topLevelItem(0).setExpanded(True)
            for width in (1240, 1480, 1100, 1240):
                with self.subTest(page=type(view).__name__, width=width):
                    w.resize(width, 800); self.settle()
                    self.assert_default_frame(view, header)

    def test_existing_saved_order_visibility_and_widths_restore_without_new_defaults(self):
        w = self.window; header = w.table.horizontalHeader()
        w.table.setColumnHidden(2, True)
        header.moveSection(header.visualIndex(6), 1)
        header.resizeSection(3, 83)
        w._save_columns()
        saved = w.settings['columns_state']
        self.assertEqual(w.settings['columns_keys'], [key for key, _label, _hidden in COLUMNS])
        w._apply_default_columns()
        self.assertEqual(w.settings['columns_state'], saved)
        w._restore_columns(); self.settle()
        self.assertEqual(header.logicalIndex(1), 6)
        self.assertTrue(w.table.isColumnHidden(2))
        self.assertEqual(header.sectionSize(3), 83)
        self.assertEqual(w.artists_view.header().logicalIndex(1), 6)
        self.assertTrue(w.artists_view.isColumnHidden(2))
        self.assertEqual(w.artists_view.columnWidth(3), 83)

    def test_bpm_decimals_paint_in_full_in_regular_and_artists_browsers(self):
        w = self.window
        for view, show in ((w.table, w._show_collection), (w.artists_view, w._show_artists)):
            show()
            if view is w.artists_view:
                view.topLevelItem(0).setExpanded(True)
            self.settle()
            for row, expected in enumerate(('145.00', '188.00')):
                index = (w.model.index(row, 3) if view is w.table else
                         view.indexFromItem(view.topLevelItem(0).child(row), 3))
                with self.subTest(view=type(view).__name__, value=expected):
                    self.assertEqual(index.data(), expected)
                    width = view.columnWidth(3)

                    def painted(elide):
                        option = QStyleOptionViewItem()
                        # The view supplies its effective font. initFrom alone
                        # misses it and can incorrectly claim that text fits.
                        view.initViewItemOption(option)
                        option.rect = QRect(0, 0, width, 24)
                        option.widget = view
                        option.textElideMode = elide
                        image = QImage(width * 2, 48, QImage.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(2)
                        image.fill(Qt.transparent)
                        painter = QPainter(image)
                        view.itemDelegate().paint(painter, option, index)
                        painter.end()
                        return bytes(image.constBits())

                    self.assertEqual(painted(view.textElideMode()), painted(Qt.ElideNone),
                                     'Default BPM cells must paint the complete decimal value')

    def test_library_defaults_do_not_resize_the_independent_usb_playlist_frame(self):
        w = self.window
        tree = w.usb_device_pages.playlist_tree
        before = [tree.columnWidth(1), tree.columnWidth(2)]
        self.assertEqual(tree.columnCount(), 3)
        self.assertEqual(before, [70, 150])
        for width in (1100, 1480, 1240):
            w.resize(width, 800); w._apply_default_columns(); self.settle()
            self.assertEqual([tree.columnWidth(1), tree.columnWidth(2)], before)
