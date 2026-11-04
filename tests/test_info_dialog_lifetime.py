"""Closing modal info retires owned decode work before deleting cached images."""
from threading import Event
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel, QWidget
import shiboken6

from desktop.track_info import TrackInfoDialog, TrackInfoMixin, _exec_info


class Host(TrackInfoMixin,QWidget):
    pass


class InfoDialogLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        area=tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.root=Path(area.name)
        self.parent=Host()
        self.addCleanup(self.dispose)

    def dispose(self):
        for dialog in self.parent.findChildren(TrackInfoDialog):
            dialog.reject()
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def image(self,name,color):
        path=self.root/name
        image=QImage(1200,600,QImage.Format_RGB32);image.fill(QColor(color))
        self.assertTrue(image.save(str(path)))
        return path

    def settle(self,dialog):
        loop=QEventLoop();timeout=QTimer();timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit);dialog._owner.drained.connect(loop.quit)
        timeout.start(3000)
        if dialog._owner.pending:loop.exec()
        dialog._owner.drained.disconnect(loop.quit)
        self.assertEqual(dialog._owner.pending,0)

    def test_first_show_contains_cover_even_when_resolution_is_slow(self):
        path=self.image('first.png','red');release=Event();ticks=[];threads=[];shown=[]
        def resolve():
            threads.append(QThread.currentThread());release.wait(2);return path
        class Dialog(TrackInfoDialog):
            def showEvent(dialog,event):
                # Measure preparation only; Cocoa may deliver an overdue timer
                # after show but before the queued modal-close callback.
                heartbeat.stop()
                shown.append((dialog._covers[0].pixmap().isNull(),dialog._image.pixelColor(0,0).name()))
                super().showEvent(event)
                QTimer.singleShot(0,dialog.reject)
        dialog=Dialog({'title':'Fixture'},artwork=resolve,parent=self.parent)
        heartbeat=QTimer();heartbeat.timeout.connect(lambda:ticks.append(dialog.isVisible()));heartbeat.start(5)
        QTimer.singleShot(60,release.set)
        try:_exec_info(dialog)
        finally:heartbeat.stop()
        self.assertEqual(shown,[(False,'#ff0000')])
        self.assertGreater(len(ticks),1)
        self.assertTrue(all(not visible for visible in ticks))
        self.assertIsNot(threads[0],self.app.thread())
        self.assertIsNone(QApplication.overrideCursor())

    def test_missing_and_invalid_art_settle_before_show_without_busy_loops(self):
        bad=self.root/'bad.png';bad.write_bytes(b'not an image')
        for path in (None,self.root/'missing.png',bad):
            with self.subTest(path=path):
                dialog=TrackInfoDialog({},artwork=lambda p=path:p,parent=self.parent)
                dialog.prepare_artwork()
                self.assertTrue(dialog._art_ready)
                self.assertEqual(dialog._owner.pending,0)
                self.assertEqual(dialog._covers[0].text(),'No artwork')

    def test_info_tab_reuses_prepared_image_without_another_resolution(self):
        path=self.image('cover.png','blue');resolve=mock.Mock(return_value=path)
        dialog=TrackInfoDialog({},artwork=resolve,parent=self.parent)
        dialog.prepare_artwork();dialog.tabs.setCurrentIndex(1);dialog.prepare_artwork()
        self.assertEqual(resolve.call_count,1)
        self.assertEqual(len(dialog._covers),2)
        self.assertTrue(all(not c.pixmap().isNull() for c in dialog._covers))
        self.assertLessEqual(dialog._image.width(),max(640,round(320*dialog.devicePixelRatioF())))

    def test_new_publication_during_decode_discards_old_result_and_coalesces_reload(self):
        old=self.image('old.png','red');new=self.image('new.png','blue')
        release=Event();entered=Event();calls=[];painted=[]
        def resolve():
            calls.append(1)
            if len(calls)==1:entered.set();release.wait(2);return old
            return new
        dialog=TrackInfoDialog({'id':'selected'},artwork=resolve,parent=self.parent)
        original=dialog._paint_artwork
        def paint():
            painted.append(dialog._image.pixelColor(0,0).name());original()
        dialog._paint_artwork=paint
        dialog._load_artwork();self.assertTrue(entered.wait(1))
        self.parent._refresh_info_artwork(['other'])
        self.assertFalse(dialog._art_dirty)
        for _ in range(4):self.parent._refresh_info_artwork(['selected'])
        release.set();self.settle(dialog)
        self.assertEqual(len(calls),2)
        self.assertEqual(painted,['#0000ff'])
        dialog.reject();self.parent._refresh_info_artwork(['selected'])
        self.assertEqual(dialog._owner.pending,0)

    def test_refresh_keeps_existing_cover_visible_until_the_new_one_is_ready(self):
        old=self.image('old.png','red');new=self.image('new.png','blue');release=Event()
        dialog=TrackInfoDialog({'id':'selected'},artwork=lambda:old,parent=self.parent)
        dialog.prepare_artwork()
        dialog._artwork=lambda:(release.wait(2) and new)
        self.parent._refresh_info_artwork(['selected'])
        self.assertEqual(dialog._covers[0].pixmap().toImage().pixelColor(0,0).name(),'#ff0000')
        release.set();self.settle(dialog)
        self.assertEqual(dialog._covers[0].pixmap().toImage().pixelColor(0,0).name(),'#0000ff')

    def test_summary_metadata_is_literal_even_when_it_looks_like_markup(self):
        title='<b>Title & artist</b>'
        dialog=TrackInfoDialog({'title':title,'location':'/Music/<i>album</i>/song.wav'},parent=self.parent)
        labels={label.text():label for label in dialog.findChildren(QLabel)}
        self.assertEqual(labels[title].textFormat(),Qt.PlainText)
        self.assertEqual(labels['/Music/<i>album</i>/song.wav'].textFormat(),Qt.PlainText)

    def test_closed_modal_releases_parented_dialog_only_after_decode_retires(self):
        parent = QWidget()
        release = Event()
        events = []
        def resolve():
            release.wait(2)
            events.append('decode-ended')
        dialog = TrackInfoDialog({'title': 'Fixture'}, artwork=resolve, parent=parent)
        dialog.destroyed.connect(lambda: events.append('dialog-destroyed'))
        QTimer.singleShot(20, dialog.reject)
        QTimer.singleShot(60, release.set)
        _exec_info(dialog)
        self.assertEqual(dialog._owner.pending, 0)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(shiboken6.isValid(dialog))
        self.assertEqual(events, ['decode-ended', 'dialog-destroyed'])
        self.assertEqual(parent.findChildren(TrackInfoDialog), [])
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
