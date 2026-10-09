"""Decode admission errors/retries without touching a live cloud provider."""
from pathlib import Path
import subprocess
import tempfile
import unittest


class NativeCloudReadiness(unittest.TestCase):
    def test_bounded_auth_retries_cancellation_and_error_identity(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
#include "CloudReadiness.h"
#include <cassert>
int main() {
    const std::string cloud = "/Users/test/Library/CloudStorage/Provider/a.m4a";
    int reads = 0, waits = 0;
    auto waiting = [&] { ++waits; return true; };
    auto failed = [&] { ++reads; return ENEEDAUTH; };
    assert(sbCloudReadiness(cloud, failed, waiting) == ENEEDAUTH);
    assert(reads == 3 && waits == 2);
    reads = waits = 0;
    assert(sbCloudReadiness(cloud, [&] { return ++reads < 3 ? ENEEDAUTH : 0; }, waiting) == 0);
    assert(reads == 3 && waits == 2);
    reads = waits = 0;
    assert(sbCloudReadiness(cloud, failed, [&] { ++waits; return false; }) == ECANCELED);
    assert(reads == 1 && waits == 1);
    for (int error : {EACCES, ENOENT, EIO}) {
        reads = waits = 0;
        assert(sbCloudReadiness(cloud, [&] { ++reads; return error; }, waiting) == error);
        assert(reads == 1 && waits == 0);
    }
    reads = waits = 0;
    assert(sbCloudReadiness("/tmp/local.m4a", failed, waiting) == 0);
    assert(reads == 0 && waits == 0);
    assert(sbReadSourceByte("missing-fixture.m4a") == ENOENT);
}
'''
        with tempfile.TemporaryDirectory(prefix="sb-cloud-admission-") as area:
            source = Path(area) / "probe.cpp"
            binary = Path(area) / "probe"
            source.write_text(program)
            subprocess.run(["clang++", "-std=c++17", "-I", str(root / "native"),
                            str(source), "-o", str(binary)], check=True,
                           capture_output=True)
            subprocess.run([str(binary)], cwd=area, check=True)
