"""Fresh native process queries, including failure boundaries and a private child."""
import ctypes
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from uuid import uuid4

from soundbox import mac_process

ROOT = Path(__file__).resolve().parents[1]


def bind(library):
    function = library.sb_process_names_running
    function.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t]
    function.restype = ctypes.c_int
    return function


class NativeProcessGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = tempfile.TemporaryDirectory(prefix='soundbox-process-proof-')
        cls.addClassCleanup(cls.area.cleanup)
        folder = Path(cls.area.name)
        source = folder / 'kernel_fixture.c'
        source.write_text(r'''
#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/user.h>
#include <libproc.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
static int mode, calls;
void test_mode(int value) { mode = value; calls = 0; }
int test_calls(void) { return calls; }
static int fake_sysctl(int *query, u_int count, void *out, size_t *size, void *in, size_t input) {
    (void)query; (void)count; (void)in; (void)input;
    ++calls;
    if (mode == 6) { errno = EPERM; return -1; }
    if (!out) {
        *size = mode == 8 ? 0 : mode == 9 ? 64 * 1024 * 1024 : 2 * sizeof(struct kinfo_proc);
        return 0;
    }
    if ((mode == 7 && calls == 2) || mode == 10) { errno = ENOMEM; return -1; }
    if (mode == 11) { ++*size; return 0; }
    struct kinfo_proc *rows = out;
    memset(rows, 0, 2 * sizeof(*rows));
    rows[0].kp_proc.p_pid = getpid();
    char self_name[2 * MAXCOMLEN + 1] = {0};
    proc_name(getpid(), self_name, sizeof(self_name));
    strlcpy(rows[0].kp_proc.p_comm, self_name, sizeof(rows[0].kp_proc.p_comm));
    rows[1].kp_proc.p_pid = getpid() + 100000;
    strcpy(rows[1].kp_proc.p_comm, (mode == 1 || mode == 7) ? "rekordbox" : "other");
    if (mode == 3) rows[0].kp_proc.p_pid = -1;
    if (mode == 4) memset(rows[1].kp_proc.p_comm, 'x', sizeof(rows[1].kp_proc.p_comm));
    if (mode == 13) strcpy(rows[0].kp_proc.p_comm, "wrong-abi");
    *size = mode == 12 ? 0 : 2 * sizeof(*rows) - (mode == 5 ? 1 : 0);
    return 0;
}
#define sysctl fake_sysctl
#include "ProcessGuard.c"
''')
        library = folder / 'kernel_fixture.dylib'
        subprocess.run(['/usr/bin/clang', '-dynamiclib', '-O2', '-std=c11', '-I', str(ROOT / 'native'),
                        str(source), '-o', str(library)], check=True, capture_output=True)
        cls.library = ctypes.CDLL(str(library))
        cls.fixture = bind(cls.library)
        cls.library.test_mode.argtypes = [ctypes.c_int]
        cls.library.test_calls.restype = ctypes.c_int

    def query(self, mode):
        self.library.test_mode(mode)
        names = (ctypes.c_char_p * 2)(*mac_process.REKORDBOX_NAMES)
        return self.fixture(names, 2)

    def test_complete_kernel_snapshot_proves_only_exact_requested_names(self):
        self.assertEqual(self.query(0), 0)
        self.assertEqual(self.query(1), 1)
        names = (ctypes.c_char_p * 1)(b'rekordboxAgent')
        self.assertEqual(self.fixture(names, 1), 0)

    def test_unknown_or_incomplete_kernel_results_never_prove_absence(self):
        for mode in (3, 4, 5, 6, 8, 9, 10, 11, 12, 13):
            with self.subTest(mode=mode):
                self.assertEqual(self.query(mode), -1)
        self.assertEqual(self.query(7), 1)
        self.assertEqual(self.library.test_calls(), 4)
        self.query(10)
        self.assertEqual(self.library.test_calls(), 12)

    def test_native_bad_query_arguments_fail_closed(self):
        self.assertEqual(self.fixture(None, 1), -1)
        names = (ctypes.c_char_p * 1)(b'')
        self.assertEqual(self.fixture(names, 0), -1)
        self.assertEqual(self.fixture(names, 17), -1)
        self.assertEqual(self.fixture(names, 1), -1)
        names[0] = b'x' * 17
        self.assertEqual(self.fixture(names, 1), -1)

    def test_python_queries_are_fresh_and_unknowns_remain_unknown(self):
        probe = mock.Mock(side_effect=[0, 1, -1, 2, False, OSError('denied')])
        with mock.patch.object(mac_process, '_native_function', return_value=probe):
            self.assertEqual([mac_process.rekordbox_running() for _ in range(6)],
                             [False, True, None, None, None, None])
        self.assertEqual(probe.call_count, 6)
        with mock.patch.object(mac_process, '_native_function', return_value=None):
            self.assertIsNone(mac_process.rekordbox_running())

    def test_bad_python_names_never_reach_native_query(self):
        with mock.patch.object(mac_process, '_native_function') as loader:
            for names in ([], (), (b'',), (b'x' * 17,), (b'a\0b',), ('text',), (b'x',) * 17):
                self.assertIsNone(mac_process.process_names_running(names))
            loader.assert_not_called()

    def test_actual_kernel_query_detects_private_child_without_real_app_names(self):
        self.assertIsNotNone(mac_process._native_function(), 'Build the required native process helper')
        name = 'sbp' + uuid4().hex[:9]
        names = (name.encode(),)
        self.assertIs(mac_process.process_names_running(names), False)
        with tempfile.TemporaryDirectory(prefix='soundbox-private-process-') as area:
            binary = Path(area) / name
            shutil.copyfile('/bin/sleep', binary)
            binary.chmod(0o700)
            child = subprocess.Popen([str(binary), '30'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 2
                while mac_process.process_names_running(names) is not True and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertIs(mac_process.process_names_running(names), True)
            finally:
                child.terminate(); child.wait(timeout=5)
        self.assertIs(mac_process.process_names_running(names), False)

    def test_unavailable_native_interface_retains_fallback_eligibility(self):
        with mock.patch.object(mac_process.ctypes, 'CDLL', side_effect=OSError('missing helper')):
            self.assertIsNone(mac_process._native_function.__wrapped__())
        for location in (None, 'relative', 3):
            with mock.patch.object(mac_process.sys, 'frozen', True, create=True), \
                    mock.patch.object(mac_process.sys, '_MEIPASS', location, create=True):
                self.assertIsNone(mac_process._native_function.__wrapped__())
