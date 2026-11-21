"""Require a complete, nonduplicated passing inventory across both Qt processes."""
from __future__ import annotations

import json
from pathlib import Path


def verify_workers(report):
    if report.get('scope','full')!='full':raise ValueError('Focused checks are not complete release evidence')
    workers=report.get('workers')
    if not isinstance(workers,list) or not workers:raise ValueError('Missing worker reports')
    seen=set()
    for worker in workers:
        selected=worker['selected']
        if (worker.get('scope','full')!='full' or worker['phase']!=report['phase'] or worker['inventory']!=report['inventory']
                or not selected or len(set(selected))!=len(selected) or seen.intersection(selected)
                or worker.get('workers') or worker.get('exit_code')!=0
                or not worker['passed'] or worker['skipped'] or worker['expected_failures']
                or worker['ran']!=len(selected)):
            raise ValueError('Every isolated worker must run its unique complete partition and pass')
        seen.update(selected)
    if seen!=set(report['selected']) or report['ran']!=sum(w['ran'] for w in workers):
        raise ValueError('Worker partitions do not cover the phase exactly')


def verify(reports: list[dict]) -> int:
    if any(report.get('scope','full')!='full' for report in reports):
        raise ValueError('Focused checks are not complete release evidence')
    if len(reports) != 2 or {r['phase'] for r in reports} != {'offscreen', 'native'}:
        raise ValueError('Both required Qt phases must report')
    inventory = reports[0]['inventory']
    if not inventory or len(set(inventory)) != len(inventory):
        raise ValueError('Empty or duplicate discovered inventory')
    seen = set()
    for report in reports:
        if report.get('scope','full')!='full':raise ValueError('Focused checks are not complete release evidence')
        if 'workers' in report:verify_workers(report)
        selected = report['selected']
        if report['inventory'] != inventory:
            raise ValueError('Test inventory changed between processes')
        if len(set(selected)) != len(selected) or seen.intersection(selected):
            raise ValueError('Test routed more than once')
        if (not report['passed'] or report['skipped'] or report['expected_failures']
                or report['ran'] != len(selected)):
            raise ValueError('Every selected test must run and pass')
        seen.update(selected)
    if seen != set(inventory):
        raise ValueError('Discovered test was not run')
    return len(inventory)


if __name__ == '__main__':
    reports = [json.loads(Path(f'build/check/{phase}.json').read_text())
               for phase in ('offscreen', 'native')]
    count = verify(reports)
    print(f'ALL {count} TESTS PASSED: zero skips, zero expected failures; both Qt processes verified.')
