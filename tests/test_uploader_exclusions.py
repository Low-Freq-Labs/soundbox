"""Local blocked-uploader manager against generated metadata only."""
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from soundbox.artwork import ArtCache
from desktop.uploader_exclusions import UploaderExclusionsDialog

_app = None


class UploaderManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _app
        _app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache=ArtCache(Path(self.temp.name)/"art")

    def dialog(self, **kwargs):
        dialog=UploaderExclusionsDialog(self.cache,**kwargs)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_add_list_reopen_and_remove_are_persistent_without_touching_covers(self):
        dialog=self.dialog(selected_url="https://soundcloud.com/some-account/song?tracking=1")
        self.assertEqual(dialog.input.text(),"https://soundcloud.com/some-account")
        dialog._add()
        self.assertEqual(self.cache.source_exclusions(),("some-account",))
        dialog.input.setText("@Another_Account")
        dialog._add()
        reopened=self.dialog()
        self.assertEqual(reopened.accounts.count(),2)
        reopened.accounts.setCurrentRow(0)
        self.assertTrue(reopened.remove.isEnabled())
        reopened._remove()
        self.assertEqual(self.cache.source_exclusions(),("some-account",))
        self.assertEqual(self.cache.index,{})

    def test_known_automatic_sources_remain_listed_and_cannot_be_unblocked(self):
        recovery=self.cache.root/"source-recovery"
        recovery.mkdir()
        (recovery/"1.json").write_text(json.dumps(dict(before=dict(src="https://soundcloud.com/plate-selling/song"))))
        dialog=self.dialog()
        self.assertEqual(dialog.accounts.count(),1)
        self.assertIn("plate-selling",dialog.accounts.item(0).text())
        dialog.accounts.setCurrentRow(0)
        self.assertFalse(dialog.remove.isEnabled())
        dialog._remove()
        self.assertEqual(dialog.accounts.count(),1)
        self.assertEqual(self.cache.source_exclusions(),())

    def test_invalid_profile_input_does_not_save_a_block(self):
        dialog=self.dialog()
        for value in ("https://example.com/uploader","a name with spaces","https://soundcloud.com/search"):
            dialog.input.setText(value)
            dialog._add()
            self.assertEqual(self.cache.source_exclusions(),())
            self.assertIn("Enter a SoundCloud",dialog.status.text())
