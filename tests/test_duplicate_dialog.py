"""Duplicate confirmation stays compact, literal and safe by default."""
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from desktop.duplicate_dialog import DuplicateAddDialog
from desktop import theme


class DuplicateDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.previous_style=cls.app.styleSheet()
        cls.app.setStyleSheet(theme.QSS)

    @classmethod
    def tearDownClass(cls):
        cls.app.setStyleSheet(cls.previous_style)

    def dialog(self,*args):
        dialog=DuplicateAddDialog(*args)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_playlist_and_titles_remain_literal_including_markup(self):
        dialog=self.dialog('<b>ALL & LOCAL</b>',['<b>A & B</b> <img src="never-read">'],1)
        self.assertFalse(dialog.playlist.acceptRichText())
        self.assertEqual(dialog.playlist.toPlainText(),'“<b>ALL & LOCAL</b>”')
        self.assertIn('&lt;b&gt;',dialog.playlist.toHtml())
        self.assertEqual(dialog.tracks.toPlainText(),'<b>A & B</b> <img src="never-read">')
        self.assertIn('&lt;b&gt;',dialog.tracks.toHtml())
        self.assertEqual(dialog.question.text(),'Add this track again?')

    def test_skip_is_default_and_enter_escape_and_close_reject(self):
        for action in (lambda d:QTest.keyClick(d,Qt.Key_Return),
                       lambda d:QTest.keyClick(d,Qt.Key_Escape),lambda d:d.close()):
            dialog=self.dialog('ALL LOCAL',['Song'],1)
            self.assertTrue(dialog.skip.isDefault())
            self.assertFalse(dialog.add.isDefault())
            self.assertEqual(dialog.buttons.buttonRole(dialog.skip),QDialogButtonBox.RejectRole)
            QTimer.singleShot(0,lambda d=dialog,a=action:a(d))
            self.assertEqual(dialog.exec(),QDialog.Rejected)

    def test_add_anyway_accepts_and_multiple_tracks_use_plural_summary(self):
        dialog=self.dialog('ALL LOCAL',[f'Song {i}' for i in range(100)],100)
        self.assertEqual(dialog.tracks.toPlainText().splitlines(),[f'Song {i}' for i in range(6)])
        self.assertEqual(dialog.more.text(),'…and 94 more')
        self.assertEqual(dialog.question.text(),'Add these tracks again?')
        self.assertEqual(dialog.buttons.buttonRole(dialog.add),QDialogButtonBox.AcceptRole)
        QTimer.singleShot(0,lambda:QTest.mouseClick(dialog.add,Qt.LeftButton))
        self.assertEqual(dialog.exec(),QDialog.Accepted)

    def test_long_names_wrap_inside_a_bounded_dialog(self):
        dialog=self.dialog('A long playlist & name ' * 3,['Long title ' * 40,'UnbrokenTitle' * 60],2)
        dialog.show();self.app.processEvents()
        self.assertLessEqual(dialog.width(),480)
        self.assertLessEqual(dialog.tracks.height(),200)
        self.assertGreater(dialog.tracks.document().firstBlock().layout().lineCount(),1)
        self.assertEqual(dialog.tracks.horizontalScrollBar().maximum(),0)
        self.assertLess(dialog.height(),600)
        self.assertEqual(dialog.skip.height(),dialog.add.height())
        unbroken=self.dialog('PLAYLIST'*30,['A readable title'],1)
        unbroken.show();self.app.processEvents()
        self.assertGreater(unbroken.playlist.document().firstBlock().layout().lineCount(),1)
        self.assertEqual(unbroken.playlist.horizontalScrollBar().maximum(),0)
        self.assertLessEqual(unbroken.playlist.height(),80)

    def test_window_adapter_reads_only_six_effective_titles_and_returns_exact_dialog_choice(self):
        from desktop.app import MainWindow
        host=SimpleNamespace(_track_label=mock.Mock(side_effect=lambda tid:'Title '+tid),
                             _shutting_down=False,_state_maintenance=False)
        duplicates=[str(i) for i in range(100)]
        with mock.patch('desktop.duplicate_dialog.DuplicateAddDialog') as factory:
            factory.DialogCode=QDialog.DialogCode
            box=factory.return_value;box.exec.return_value=QDialog.Accepted
            self.assertTrue(MainWindow._confirm_duplicate_add(host,'ALL LOCAL',duplicates))
            factory.assert_called_once_with('ALL LOCAL',['Title '+str(i) for i in range(6)],100,host)
            self.assertEqual(host._track_label.call_count,6)
            box.exec.return_value=QDialog.Rejected
            self.assertFalse(MainWindow._confirm_duplicate_add(host,'ALL LOCAL',['0']))


if __name__=='__main__':unittest.main()
