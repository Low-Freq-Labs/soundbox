"""Explicit Cocoa gate: skipped native tests cannot count as successful validation."""
import os
import sys
import unittest


def main():
    if os.environ.get('QT_QPA_PLATFORM') != 'cocoa':
        print('Native gate requires QT_QPA_PLATFORM=cocoa', file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_native_deck')
    result = unittest.TextTestRunner(verbosity=2, buffer=True).run(suite)
    if result.skipped or result.expectedFailures:
        print('Native gate incomplete: skips/expected failures are not accepted.', file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
