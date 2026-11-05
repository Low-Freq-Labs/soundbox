"""CLI success reflects the guarded exporter, not free space after copying."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from soundbox import cli
from soundbox.library import Library, Playlist
from soundbox.report import SyncReport


class CliSpaceReporting(unittest.TestCase):
    def test_completed_export_is_not_reported_as_aborted_when_remaining_space_is_low(self):
        with tempfile.TemporaryDirectory() as area:
            target=Path(area)
            args=SimpleNamespace(dry_run=False,xml='generated.xml',playlists=None,
                target=str(target),layout='tree',derive_artists=False,clean=False)
            library=Library({},[Playlist('Generated',[])],'generated.xml')
            report=SyncReport(dry_run=False,bytes_to_copy=100)
            completed=[]
            def export(*args,**kwargs):
                (target/'export-finished').write_text('complete')
                completed.append(True)
                return report
            with mock.patch.object(cli.rekordbox_xml,'load',return_value=library), \
                    mock.patch.object(cli.sync_mod,'run',side_effect=export), \
                    mock.patch('shutil.disk_usage',return_value=SimpleNamespace(free=50)), \
                    redirect_stdout(io.StringIO()) as output:
                result=cli._cmd_sync(args)
            self.assertEqual(result,0)
            self.assertEqual(completed,[True])
            self.assertTrue((target/'export-finished').exists())
            self.assertNotIn('Aborting before copy',output.getvalue())
            self.assertIn('Total data copied',output.getvalue())
