"""Synthetic transport boundaries and physical holds; no real audio/library."""
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop.player import PlayerBar


class BeatJumpHold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.pb=PlayerBar();self.pb._src_path='/private/synthetic.wav';self.pb._track_id='synthetic'
        self.pb._dur=120000;self.pb._pos=60000;self.pb._bpm=120;self.pb._playback_available=True
        self.moves=[]
        def move(position):self.moves.append(position);self.pb._pos=position
        self.pb._move_clean=move;self.pb._update_transport()
        self.pb.show();self.pb.activateWindow();self.app.processEvents()

    def tearDown(self):
        for button in list(self.pb._jump_holds):self.pb._release_jump_button(button)
        self.pb.shutdown();self.pb.close();self.pb.deleteLater();self.app.processEvents()

    def wait_steps(self,count):
        for _ in range(120):
            if len(self.moves)>=count:return
            QTest.qWait(10)
        self.fail('Held jump did not repeat')

    def test_oversized_jump_uses_remaining_audio_with_and_without_a_grid(self):
        self.pb._jump_beats=128
        for grid in ([],[i*500 for i in range(241)]):
            self.pb._beat_grid=grid;self.pb._pos=16000
            self.assertEqual(self.pb._beat_jump_target(-1),0)
            self.pb._pos=110000
            self.assertEqual(self.pb._beat_jump_target(1),120000)
            self.pb._pos=0;self.assertIsNone(self.pb._beat_jump_target(-1))
            self.pb._pos=120000;self.assertIsNone(self.pb._beat_jump_target(1))

    def test_mouse_hold_repeats_and_release_stops_without_an_extra_click_step(self):
        QTest.mousePress(self.pb.beat_back_btn,Qt.LeftButton)
        self.wait_steps(3);QTest.mouseRelease(self.pb.beat_back_btn,Qt.LeftButton)
        position=self.pb._pos;count=len(self.moves);QTest.qWait(220)
        self.assertEqual(self.moves[:3],[52000,44000,36000])
        self.assertEqual(self.pb._pos,position);self.assertEqual(len(self.moves),count)

    def test_shortcut_hold_stops_on_release_track_change_and_focus_loss(self):
        release=self.pb.begin_beat_jump(1);self.wait_steps(2);release()
        count=len(self.moves);QTest.qWait(180);self.assertEqual(len(self.moves),count)
        release=self.pb.begin_beat_jump(-1);self.pb._track_id='a different track'
        count=len(self.moves);QTest.qWait(350);self.assertEqual(len(self.moves),count);release()
        release=self.pb.begin_beat_jump(-1)
        with mock.patch.object(self.pb,'isActiveWindow',return_value=False):
            count=len(self.moves);QTest.qWait(350);self.assertEqual(len(self.moves),count)
        release()
