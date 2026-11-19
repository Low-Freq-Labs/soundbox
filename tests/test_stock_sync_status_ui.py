"""Prepared status-only publication preserves the loaded iPod view and models."""
from unittest import mock
import unittest

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from desktop.stock_device_view import StockIpodPages
from tests import test_stock_first_export_actions as fixture


class StockSyncStatusUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def pages(self):
        pages=StockIpodPages();self.addCleanup(pages.deleteLater)
        return pages

    def values(self,pages):
        tree=pages.detail_trees['SoundBox']
        return {tree.topLevelItem(index).text(0):tree.topLevelItem(index).text(1)
                for index in range(tree.topLevelItemCount())}

    def test_pending_and_resolved_status_update_only_existing_detail_cells(self):
        pages=self.pages()
        pages.set_snapshot(fixture.snapshot(fixture.FAVORITES,fixture.VIDEOS,
            sync_state='local_changes',sync_local_changes=3))
        pages.set_page(2);tree=pages.playlist_tree
        selected=tree.topLevelItem(0);tree.setCurrentItem(selected)
        position=tree.verticalScrollBar().value();revision=pages._sync_revision
        details=pages.detail_trees['SoundBox']
        items=tuple(details.topLevelItem(i) for i in range(details.topLevelItemCount()))
        music_reset=QSignalSpy(pages.music_model.modelReset)
        playlist_reset=QSignalSpy(tree.model().modelReset)
        detail_reset=QSignalSpy(details.model().modelReset)
        with mock.patch.object(pages.music_model,'set_snapshot',side_effect=AssertionError('Music rebuilt')), \
             mock.patch.object(tree,'clear',side_effect=AssertionError('Playlists rebuilt')), \
             mock.patch.object(pages,'_set_detail_rows',side_effect=AssertionError('Details rebuilt')):
            pages.set_sync_status('comparison_pending','Comparing saved iPod metadata',3,'')
            self.assertEqual(self.values(pages)['Sync state'],'Sync status pending')
            pages.set_sync_status('up_to_date','No pending changes',0,'2026-11-19 02:00')
        values=self.values(pages)
        self.assertEqual(values['Sync state'],'Up to date')
        self.assertEqual(values['Sync detail'],'No pending changes')
        self.assertEqual(values['Pending local changes'],'0')
        self.assertEqual(values['Last Sync'],'2026-11-19 02:00')
        self.assertEqual(tuple(details.topLevelItem(i) for i in range(details.topLevelItemCount())),items)
        self.assertIs(tree.currentItem(),selected)
        self.assertEqual(tree.verticalScrollBar().value(),position)
        self.assertEqual(pages.stack.currentIndex(),2)
        self.assertEqual(pages._sync_revision,revision)
        self.assertEqual((music_reset.count(),playlist_reset.count(),detail_reset.count()),(0,0,0))

    def test_initial_snapshot_uses_the_same_pending_status_label(self):
        pages=self.pages()
        pages.set_snapshot(fixture.snapshot(sync_state='comparison_pending'))
        self.assertEqual(self.values(pages)['Sync state'],'Sync status pending')

    def test_status_before_snapshot_does_not_initialize_or_create_rows(self):
        pages=self.pages()
        pages.set_sync_status('comparison_pending','Comparing',0,'')
        self.assertEqual(pages.detail_trees['SoundBox'].topLevelItemCount(),0)
        self.assertFalse(pages._has_snapshot)


if __name__=='__main__':
    unittest.main()
