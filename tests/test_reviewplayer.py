"""Review transport fakes: no audio/device/library/network reads."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from desktop.reviewplayer import ReviewPlayer

_app = None


class Deck(QObject):
    playback_error = Signal(str)

    def __init__(self):
        super().__init__()
        self.state = dict(track_id="other",position=0,duration=120000,requested=True,loading=False,available=True)
        self.calls=[]

    def review_transport_state(self):
        return dict(self.state)

    def pause(self):
        self.calls.append("pause")
        self.state["requested"]=False

    def resume(self):
        self.calls.append("resume")
        self.state["requested"]=True

    def seek_ms(self, ms):
        self.calls.append(("seek",ms))
        self.state["position"]=ms


class ReviewTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _app
        _app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.deck=Deck()
        def start(tid):
            self.deck.calls.append(("load",tid))
            self.deck.state.update(track_id=tid,position=0,requested=True)
            return True
        self.widget=ReviewPlayer(player=self.deck,start=start)
        self.addCleanup(self.widget.deleteLater)
        self.addCleanup(self.widget.shutdown)
        self.widget.set_track("review")

    def test_selection_does_not_load_then_play_pause_resume_and_seek_share_deck(self):
        self.assertEqual(self.deck.calls,[])
        self.widget._toggle()
        self.widget._toggle()
        self.widget._toggle()
        self.widget.scrub.setValue(45000)
        self.assertEqual(self.deck.calls,[("load","review"),"pause","resume",("seek",45000)])
        self.assertEqual(self.widget.time.text(),"0:45 / 2:00")
        self.widget._sync()
        self.assertEqual(len(self.deck.calls),4)  # refresh never issues a seek

    def test_change_and_close_cancel_owned_audio_once_without_auto_loading(self):
        self.widget._toggle()
        self.widget.set_track("next")
        self.assertEqual(self.deck.calls,[("load","review"),"pause"])
        self.widget._toggle()
        self.widget.shutdown()
        self.widget.shutdown()
        self.assertEqual(self.deck.calls[-2:],[("load","next"),"pause"])
        self.assertFalse(self.widget._timer.isActive())
        self.widget._toggle()
        self.assertEqual(self.deck.calls[-1],"pause")

    def test_clicking_scrubber_jumps_to_clicked_time_once(self):
        self.widget.resize(650,45)
        self.widget.show()
        _app.processEvents()
        self.widget._toggle()
        scrub=self.widget.scrub
        QTest.mouseClick(scrub,Qt.LeftButton,pos=QPoint(round(scrub.width()*.75),scrub.height()//2))
        seeks=[call for call in self.deck.calls if isinstance(call,tuple) and call[0]=="seek"]
        self.assertEqual(len(seeks),1)
        self.assertGreater(seeks[0][1],84000)
        self.assertLess(seeks[0][1],96000)

    def test_pending_source_change_can_be_canceled_and_failed_load_does_not_pause_other_audio(self):
        self.widget.start=lambda _: True  # source-change fade has not published its ID yet
        self.widget._toggle()
        self.assertEqual(self.widget.time.text(),"Loading…")
        self.widget._toggle()
        self.assertEqual(self.deck.calls,["pause"])
        self.widget.start=lambda _: False
        self.widget._toggle()
        self.assertEqual(self.deck.calls,["pause"])
        self.assertEqual(self.widget.time.text(),"Audio unavailable")

    def test_loading_and_missing_backend_disable_scrubbing_and_report_errors(self):
        self.widget._toggle()
        self.deck.state["loading"]=True
        self.widget._sync()
        self.assertFalse(self.widget.scrub.isEnabled())
        self.widget._seek()
        self.assertEqual(self.deck.calls,[("load","review")])
        self.deck.playback_error.emit("Cloud file is unavailable")
        self.assertEqual(self.widget.time.text(),"Audio unavailable")
        self.assertEqual(self.widget.time.toolTip(),"Cloud file is unavailable")
        self.deck.state["available"]=False
        self.widget._sync()
        self.assertFalse(self.widget.play.isEnabled())
