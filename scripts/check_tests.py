"""Route every discovered test once, with no skipped/expected-failure release passes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor,as_completed
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TimedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.case_timings=[]

    def addDuration(self,test,elapsed):
        super().addDuration(test,elapsed)
        self.case_timings.append(dict(id=test.id(),seconds=elapsed))


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def partition(ids,workers,weights):
    """Keep module fixtures/order intact; scheduling hints never omit a case."""
    modules={}
    for identity in ids:modules.setdefault(identity.split('.')[0],[]).append(identity)
    groups=[[] for _ in range(min(workers,len(modules)))];loads=[0.0]*len(groups)
    for module,cases in sorted(modules.items(),key=lambda pair:(-weights.get(pair[0],1),pair[0])):
        index=min(range(len(groups)),key=lambda i:(loads[i],i))
        groups[index].extend(cases);loads[index]+=weights.get(module,1)
    # Discovery order also controls setUpModule/tearDownModule transitions.
    result=[]
    for group in groups:
        assigned=set(group)
        result.append([identity for identity in ids if identity in assigned])
    return result


def parallel(args,inventory,ids,discovery_seconds):
    from scripts.verify_test_reports import verify_workers
    weights=json.loads((ROOT/'scripts/test-module-costs.json').read_text())
    groups=partition(ids,args.workers,weights)
    directory=args.report.parent/(args.report.stem+'-workers');directory.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    def run(index,selected):
        report=directory/f'{index}.json';plan=directory/f'{index}-plan.json';log=directory/f'{index}.log'
        report.unlink(missing_ok=True)
        plan.write_text(json.dumps(dict(inventory=inventory,selected=selected)))
        try:
            with log.open('w') as output:
                completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--phase',args.phase,
                    '--report',str(report),'--partition',str(plan),'--workers','1'],stdout=output,stderr=subprocess.STDOUT)
            value=json.loads(report.read_text())
            if value['selected']!=selected:raise ValueError('Worker did not report its assigned partition')
            value['exit_code']=completed.returncode
        except (OSError,KeyError,TypeError,ValueError) as exc:
            with log.open('a') as output:output.write(f'\nWorker report failed: {exc}\n')
            value=dict(phase=args.phase,inventory=inventory,selected=selected,
                       ran=0,skipped=0,expected_failures=0,passed=False,exit_code=1)
        return index,value,log
    workers=[]
    print(f'Running {len(groups)} isolated offscreen processes; Cocoa follows after all finish.',flush=True)
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        futures=[pool.submit(run,i,selected) for i,selected in enumerate(groups)]
        for future in as_completed(futures):
            index,value,log=future.result();workers.append(value)
            print(f'Worker {index+1}: {value["ran"]}/{len(value["selected"])} tests, '
                  f'{value.get("elapsed_seconds",0):.1f}s; '+('PASS' if value['passed'] and value['exit_code']==0 else 'FAIL'),flush=True)
            if not value['passed'] or value['exit_code']:
                print(log.read_text()[-18000:],file=sys.stderr)
    result=dict(phase=args.phase,inventory=inventory,selected=ids,
        ran=sum(w['ran'] for w in workers),skipped=sum(w['skipped'] for w in workers),
        expected_failures=sum(w['expected_failures'] for w in workers),passed=True,
        discovery_seconds=discovery_seconds,elapsed_seconds=time.monotonic()-started,
        case_timings=[case for w in workers for case in w.get('case_timings',[])],workers=workers)
    try:verify_workers(result)
    except ValueError as exc:result['passed']=False;print(str(exc),file=sys.stderr)
    args.report.write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('offscreen', 'native'), required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--partition',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    expected_platform = 'cocoa' if args.phase == 'native' else 'offscreen'
    if os.environ.get('QT_QPA_PLATFORM') != expected_platform:
        parser.error(f'{args.phase} requires QT_QPA_PLATFORM={expected_platform}')
    if args.workers<1 or args.workers>3:parser.error('Use one to three isolated workers')
    # A failed discovery/process must never leave an older green aggregate at
    # this run's report path. Worker reports have the same rule below.
    args.report.unlink(missing_ok=True)
    discovery_started=time.monotonic()
    cases = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'tests'))))
    discovery_seconds=time.monotonic()-discovery_started
    native = lambda case: case.__class__.__module__.split('.')[-1] == 'test_native_deck'
    selected = [case for case in cases if native(case) == (args.phase == 'native')]
    all_ids, ids = [c.id() for c in cases], [c.id() for c in selected]
    if len(set(all_ids)) != len(all_ids) or not ids:
        raise RuntimeError('Duplicate tests or empty test phase')
    if args.partition:
        plan=json.loads(args.partition.read_text())
        if plan['inventory']!=all_ids or not plan['selected'] or len(set(plan['selected']))!=len(plan['selected']):
            raise RuntimeError('Worker inventory changed or invalid partition')
        allowed=set(plan['selected'])
        if not allowed<=set(ids):raise RuntimeError('Partition crosses Qt phase')
        selected=[case for case in selected if case.id() in allowed];ids=[c.id() for c in selected]
        if ids!=plan['selected']:raise RuntimeError('Worker routing changed')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    if args.phase=='offscreen' and args.workers>1 and not args.partition:
        return parallel(args,all_ids,ids,discovery_seconds)
    print(f'{args.phase}: running {len(ids)} of {len(all_ids)} discovered tests; '
          'remaining tests belong to the other required process.', flush=True)
    started=time.monotonic()
    result = unittest.TextTestRunner(verbosity=1, buffer=True, durations=15,resultclass=TimedResult).run(
        unittest.TestSuite(selected))
    elapsed=time.monotonic()-started
    ok = (result.wasSuccessful() and not result.skipped and not result.expectedFailures
          and result.testsRun == len(ids))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'phase': args.phase, 'inventory': all_ids,
        'selected': ids, 'ran': result.testsRun, 'skipped': len(result.skipped),
        'expected_failures': len(result.expectedFailures), 'passed': ok,
        'discovery_seconds':discovery_seconds,'elapsed_seconds':elapsed,
        # Individual case durations already include setUp/tearDown/cleanups.
        'outside_case_seconds':max(0,elapsed-sum(t['seconds'] for t in result.case_timings)),
        'case_timings':result.case_timings}, indent=2)+'\n')
    if not ok:
        print('RELEASE TEST GATE FAILED: every selected test must run and pass.', file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    # Each process owns separate disposable Qt state, including modules which
    # use QStandardPaths directly. Never redirect HOME or share real AppData.
    from PySide6.QtCore import QStandardPaths
    QStandardPaths.setTestModeEnabled(True)
    original=QStandardPaths.writableLocation
    with tempfile.TemporaryDirectory(prefix='soundbox-test-process-') as area:
        private={kind:str(Path(area)/name) for kind,name in (
            (QStandardPaths.AppDataLocation,'data'),(QStandardPaths.AppLocalDataLocation,'data'),
            (QStandardPaths.CacheLocation,'cache'),(QStandardPaths.AppConfigLocation,'config'))}
        with mock.patch.object(QStandardPaths,'writableLocation',side_effect=lambda kind:private.get(kind) or original(kind)):
            raise SystemExit(main())
