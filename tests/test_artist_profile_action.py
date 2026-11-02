"""Selected-track artist profile art, strict provider and newer-choice protection."""
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from soundbox.artwork import ArtResult
from soundbox.library import Library,Track
from soundbox.providers import Providers


class ArtistProfileAction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from desktop.app import MainWindow
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        with mock.patch('soundbox.rekordbox_db.db_available',return_value=False),mock.patch.object(MainWindow,'_refresh_devices'):
            self.window=MainWindow(isolated_dir=Path(temp.name).resolve()/'state')
        w=self.window;self.addCleanup(self.close)
        track=Track('chosen','Edited title','Original Artist','file:///synthetic/chosen.wav')
        w.lib=Library({'chosen':track},[],'synthetic');w.overrides['chosen']='Correct Artist'
        w._state_maintenance=False;w._shutting_down=False
        self.provider=SimpleNamespace(fetch_profile_art=mock.Mock(),fetch_best=mock.Mock())
        w.providers=Providers('personal',artwork=self.provider,actions=('artwork.search','artwork.url'))
        def picture(color,url):
            buf=BytesIO();Image.new('RGB',(24,24),color).save(buf,'PNG')
            return ArtResult(buf.getvalue(),'image/png',1,url,'Profile')
        self.old=picture('red','file:///old.png');self.new=picture('blue','https://soundcloud.com/correct-artist')
        w.art_cache.store_ok('chosen',self.old);self.provider.fetch_profile_art.return_value=self.new
        self.enterContext(mock.patch.object(w,'_queue_stock_artwork'))
        self.enterContext(mock.patch.object(w,'_refresh_model_art'))
        self.enterContext(mock.patch.object(w,'_maybe_autostart_art_fill'))
        self.enterContext(mock.patch.object(w,'_maybe_prepare_stock_artwork'))

    def close(self):
        self.window.shutdown();self.window.close();self.window.deleteLater();self.app.processEvents()

    def test_profile_fetch_uses_effective_artist_and_is_saved_as_manual_avatar(self):
        w=self.window
        with mock.patch.object(w,'_run_oneshot') as run:
            w._art_artist_profile('chosen')
        tid,fetch=run.call_args.args;self.assertEqual(tid,'chosen');self.assertTrue(run.call_args.kwargs['guard']())
        result=fetch();self.provider.fetch_profile_art.assert_called_once_with('Correct Artist')
        self.provider.fetch_best.assert_not_called()
        w._on_oneshot(tid,result)
        self.assertEqual(w.art_cache.index[tid]['origin'],'manual');self.assertEqual(w.art_cache.status_kind(tid),'avatar')
        w.art_cache.retry_weak();self.assertEqual(w.art_cache.index[tid]['origin'],'manual')

    def test_late_profile_result_cannot_overwrite_a_newer_remove_or_artist_edit(self):
        w=self.window;started=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def fetch(_artist):started.set();release.wait(3);return self.new
        self.provider.fetch_profile_art.side_effect=fetch;w._art_artist_profile('chosen')
        limit=time.monotonic()+3
        while not started.is_set() and time.monotonic()<limit:QTest.qWait(5)
        self.assertTrue(started.is_set());w.art_cache.block('chosen');release.set()
        while w._oneshots and time.monotonic()<limit:QTest.qWait(5)
        self.assertFalse(w._oneshots);self.assertEqual(w.art_cache.index['chosen']['status'],'blocked')
        with mock.patch.object(w,'_run_oneshot') as run:w._art_artist_profile('chosen')
        w.overrides['chosen']='Different Artist';self.assertFalse(run.call_args.kwargs['guard']())

    def test_menu_removes_old_actions_and_places_artist_profile_directly_above_remove(self):
        w=self.window;w.model.set_rows([w._row_for(w.lib.tracks_by_id['chosen'])])
        w.table.setCurrentIndex(w.model.index(0,0));menu=mock.Mock();actions=[]
        menu.addAction.side_effect=lambda title,*args:actions.append(title)
        with mock.patch('desktop.app.QMenu',return_value=menu),mock.patch.object(w.table,'indexAt',return_value=w.model.index(0,0)):
            w._table_context(QPoint())
        self.assertNotIn('Rebuild Waveform',actions);self.assertNotIn('Copy File Name',actions)
        self.assertNotIn('Set cover art from URL…',actions)
        self.assertEqual(actions[actions.index('Remove cover art')-1],'Set cover art to artist profile')

    def test_unavailable_provider_or_unknown_artist_never_launches_a_request(self):
        w=self.window
        with mock.patch.object(w,'_run_oneshot') as run:
            w.providers=Providers('public');w._art_artist_profile('chosen');run.assert_not_called()
            w.providers=Providers('personal',artwork=self.provider,actions=('artwork.search',))
            w.overrides['chosen']='Unknown Artist';w._art_artist_profile('chosen');run.assert_not_called()
