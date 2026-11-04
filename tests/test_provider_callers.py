"""Worker provider scopes are exercised directly, without a QApplication/UI."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop.workers import ArtFillWorker, _OneShotArt
from desktop.artpicker import _CandidateSearch
from soundbox.library import Library,Track
from soundbox_personal import operations


class ProviderCallers(unittest.TestCase):
    def test_picker_cancel_during_search_does_not_start_previews_or_emit_done(self):
        provider=SimpleNamespace(operation_scope=operations.operation_scope,preview_image=Mock())
        worker=_CandidateSearch('Fixture','Title',provider)
        provider.search_candidates=lambda *_args:(worker.stop() or [dict(title='Candidate')])
        done=[];worker.done.connect(done.append)
        worker.run()
        provider.preview_image.assert_not_called()
        self.assertEqual(done,[])

    def test_one_shot_cancel_after_fetch_is_an_exception_not_a_miss_or_result(self):
        provider=SimpleNamespace(operation_scope=operations.operation_scope)
        worker=_OneShotArt('fixture',lambda:(worker.stop() or object()),provider=provider)
        results=[];worker.done.connect(lambda _tid,result:results.append(result))
        worker.run()
        self.assertEqual(len(results),1)
        self.assertIsInstance(results[0],operations.ProviderCancelled)

    def test_fill_deadline_does_not_record_false_miss_and_remains_retryable(self):
        now=[0]
        with tempfile.TemporaryDirectory() as area:
            path=Path(area)/'fixture.wav';path.write_bytes(b'fixture, never decoded')
            track=Track('fixture','Title','Artist',path.as_uri())
            library=Library({'fixture':track},[],'synthetic')
            cache=SimpleNamespace(index={},status=lambda _tid:None,source_excluded=lambda _tid:False,
                store_auto_if_unfetched=Mock(),rebalance_default_photos=Mock())
            def fetch(*_args):now[0]=61;return None
            provider=SimpleNamespace(operation_scope=operations.operation_scope,fetch_best=fetch)
            worker=ArtFillWorker(library,cache,{}, {},provider=provider);worker.DELAY=0
            with patch.object(operations.time,'monotonic',side_effect=lambda:now[0]):
                worker.run()
            cache.store_auto_if_unfetched.assert_not_called()
            cache.rebalance_default_photos.assert_called_once()
