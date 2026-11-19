"""Owned synthetic helpers only: dedicated FDs, failure and actual joined exit."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from soundbox.worker_process import WorkerProcess
from soundbox.worker_protocol import ProtocolError

_ACTOR = '''
import os,sys
from soundbox.worker_protocol import PipeChannel
mode=sys.argv[-4]
channel=PipeChannel(int(sys.argv[-2]),int(sys.argv[-1]))
print('This diagnostic is not a protocol frame',flush=True)
channel.send(('ready',mode,channel.read_fd,channel.write_fd))
while True:
    message,_=channel.receive()
    if message==('stop',):
        channel.send(('stopped',mode));break
    channel.send(('echo',message))
channel.close()
'''


class OwnedWorkerProcess(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def worker(self, actor=_ACTOR):
        worker = WorkerProcess('cache', self.root, command=(sys.executable, '-c', actor))
        self.addCleanup(worker.close)
        return worker

    def test_prewarm_is_once_protocol_uses_dedicated_fds_and_close_joins_owned_actor(self):
        worker = self.worker()
        ready = worker.start()
        self.assertEqual(ready[:2], ('ready', 'cache'))
        self.assertGreaterEqual(min(ready[2:]), 3)
        self.assertNotEqual(*ready[2:])
        process = worker.process
        self.assertIs(worker.start(), ready)
        self.assertIs(worker.process, process)
        worker.channel.send(('literal', '$value', '\n', None))
        self.assertEqual(worker.channel.receive()[0], ('echo', ('literal', '$value', '\n', None)))
        worker.close()
        self.assertEqual(process.returncode, 0)
        self.assertFalse(worker.available)
        with self.assertRaises(ProtocolError):worker.channel
        with self.assertRaises(ProtocolError):worker.start()

    def test_invalid_startup_closes_admission_but_preserves_process_ownership_until_join(self):
        actor = '''
import sys
from soundbox.worker_protocol import PipeChannel,ProtocolError
channel=PipeChannel(int(sys.argv[-2]),int(sys.argv[-1]))
channel.send(('ready','wrong-mode'))
try:channel.receive()
except ProtocolError:pass
channel.close()
'''
        worker = self.worker(actor)
        with self.assertRaisesRegex(ProtocolError, 'did not become ready'):worker.start()
        process = worker.process
        self.assertIsNotNone(process)
        self.assertFalse(worker.available)
        with self.assertRaises(ProtocolError):worker.start()
        worker.close()
        self.assertEqual(process.returncode, 0)

    def test_invalidation_delivers_pipe_eof_and_retains_handle_for_graceful_join(self):
        actor = _ACTOR.replace('message,_=channel.receive()',
            "try:message,_=channel.receive()\n    except RuntimeError:break")
        worker = self.worker(actor)
        worker.start();process = worker.process
        worker.invalidate(ProtocolError('Synthetic wrong nonce'))
        self.assertFalse(worker.available)
        worker.close()
        self.assertEqual(process.returncode, 0)

    def test_retry_joins_failed_actor_before_replacement_and_explicit_close_never_respawns(self):
        actor = _ACTOR.replace('message,_=channel.receive()',
            "try:message,_=channel.receive()\n    except RuntimeError:break")
        worker = self.worker(actor)
        worker.start();old = worker.process
        worker.invalidate(ProtocolError('Synthetic interrupted read'))
        worker.start();new = worker.process
        self.assertIsNot(new, old)
        self.assertEqual(old.returncode, 0)
        self.assertTrue(worker.available)
        worker.channel.send(('fresh-read',))
        self.assertEqual(worker.channel.receive()[0], ('echo', ('fresh-read',)))
        worker.close()
        with self.assertRaises(ProtocolError):worker.start()
        self.assertIs(worker.process, new)
        self.assertEqual(new.returncode, 0)

    def test_failed_launch_closes_every_owned_pipe_without_an_actor(self):
        from soundbox import worker_process
        descriptors = []
        original = worker_process._pipe
        def capture():
            pair = original();descriptors.extend(pair);return pair
        worker = self.worker()
        with mock.patch.object(worker_process, '_pipe', side_effect=capture), mock.patch.object(
                worker_process.subprocess, 'Popen', side_effect=OSError('Synthetic launch failure')):
            with self.assertRaisesRegex(ProtocolError, 'Could not launch'):worker.start()
        self.assertIsNone(worker.process)
        for fd in descriptors:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_closed_standard_descriptors_are_never_reused_for_protocol(self):
        # The probe is its own Python actor. It neither closes this test
        # runner's descriptors nor invokes the application entrypoint.
        result = self.root / 'result'
        actor = f'''
import os
from pathlib import Path
from soundbox.worker_process import WorkerProcess
for fd in (0,1,2):
    try:os.close(fd)
    except OSError:pass
w=WorkerProcess('cache',Path({str(self.root)!r}),command=({sys.executable!r},'-c',{_ACTOR!r}))
ready=w.start()
assert min(ready[2:])>=3 and ready[2]!=ready[3]
w.close()
assert w.process.returncode==0
Path({str(result)!r}).write_text('joined')
'''
        subprocess.run([sys.executable, '-c', actor], check=True, timeout=10)
        self.assertEqual(result.read_text(), 'joined')
