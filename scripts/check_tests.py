"""Route every discovered test once, with no skipped/expected-failure release passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('offscreen', 'native'), required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    expected_platform = 'cocoa' if args.phase == 'native' else 'offscreen'
    if os.environ.get('QT_QPA_PLATFORM') != expected_platform:
        parser.error(f'{args.phase} requires QT_QPA_PLATFORM={expected_platform}')
    cases = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
    native = lambda case: case.__class__.__module__.split('.')[-1] == 'test_native_deck'
    selected = [case for case in cases if native(case) == (args.phase == 'native')]
    all_ids, ids = [c.id() for c in cases], [c.id() for c in selected]
    if len(set(all_ids)) != len(all_ids) or not ids:
        raise RuntimeError('Duplicate tests or empty test phase')
    print(f'{args.phase}: running {len(ids)} of {len(all_ids)} discovered tests; '
          'remaining tests belong to the other required process.', flush=True)
    result = unittest.TextTestRunner(verbosity=1, buffer=True, durations=15).run(
        unittest.TestSuite(selected))
    ok = (result.wasSuccessful() and not result.skipped and not result.expectedFailures
          and result.testsRun == len(ids))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'phase': args.phase, 'inventory': all_ids,
        'selected': ids, 'ran': result.testsRun, 'skipped': len(result.skipped),
        'expected_failures': len(result.expectedFailures), 'passed': ok}, indent=2)+'\n')
    if not ok:
        print('RELEASE TEST GATE FAILED: every selected test must run and pass.', file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
