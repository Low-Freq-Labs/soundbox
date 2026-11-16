"""Real inline editor signals restore saved metadata when persistence refuses."""
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QWidget

from desktop.app import MainWindow
from desktop.model import COLUMNS,TrackModel
from soundbox.library import Library,Track


class Host(QWidget):
    _on_cell_edited=MainWindow._on_cell_edited
    _merge_artist=MainWindow._merge_artist
    _remove_alias=MainWindow._remove_alias

    def __init__(self):
        super().__init__()
        self.lib=Library({'song':Track('song','Native Title','Native Artist','file:///synthetic/song.wav')},[],
                         '/synthetic/library.xml')
        self.titles={'song':'Saved Title'};self.overrides={'song':'Saved Artist'};self.aliases={'Variant':'Canonical'}
        self.model=TrackModel();self.model.cell_edited.connect(self._on_cell_edited)
        self._save_state=mock.Mock(return_value=False)
        self._invalidate_rows=mock.Mock();self._build_artist_registry=mock.Mock()
        self._art_fetch_one=mock.Mock();self._refresh_devices=mock.Mock();self._flash_status=mock.Mock()
        self.art_cache=SimpleNamespace(status_kind=lambda _tid:'miss')
        self._reload_current_view=mock.Mock(side_effect=self.render)
        self.render()

    def render(self):
        self.model.set_rows([dict(id='song',title=self.titles.get('song','Native Title'),
                                 artist=self.overrides.get('song','Native Artist'))])


class MetadataPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.host=Host();self.addCleanup(self.host.deleteLater)

    def test_failed_title_or_artist_inline_save_restores_original_override_and_visible_row(self):
        h=self.host
        for field,values in (('title',h.titles),('artist',h.overrides)):
            with self.subTest(field=field):
                previous=dict(values);column=next(i for i,row in enumerate(COLUMNS) if row[0]==field)
                h.model.setData(h.model.index(0,column),'Unsaved change',Qt.EditRole)
                self.app.processEvents()
                self.assertEqual(values,previous)
                self.assertEqual(h.model._rows[0][field],previous['song'])
        h._art_fetch_one.assert_not_called();h._build_artist_registry.assert_not_called()

    def test_failed_clear_restores_override_and_absent_override_stays_absent(self):
        h=self.host
        self.assertFalse(h._on_cell_edited('title','song',''))
        self.assertEqual(h.titles['song'],'Saved Title')
        h.titles.clear()
        self.assertFalse(h._on_cell_edited('title','song','Unsaved new override'))
        self.assertEqual(h.titles,{})
        self.app.processEvents();self.assertEqual(h.model._rows[0]['title'],'Native Title')

    def test_failed_alias_merge_and_removal_preserve_current_mapping_without_presentation(self):
        h=self.host
        with mock.patch('desktop.app.QInputDialog.getItem',return_value=('Different',True)):
            h._merge_artist('Variant')
        self.assertEqual(h.aliases,{'Variant':'Canonical'})
        h._remove_alias('Variant')
        self.assertEqual(h.aliases,{'Variant':'Canonical'})
        h._build_artist_registry.assert_not_called();h._flash_status.assert_not_called()


if __name__=='__main__':unittest.main()
