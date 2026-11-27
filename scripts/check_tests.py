"""Run isolated focused checks or the complete, zero-skip release inventory."""
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
from scripts.test_suites import LEVELS, load_catalog, normalize_selector, suite_plan


_COCOA_TEST_ARGUMENTS = ('-ApplePersistenceIgnoreState','YES',
                         '-NSIgnorePersistentState','YES',
                         '-NSQuitAlwaysKeepsWindows','NO')


def qt_test_python_command(code,*arguments,platform=None):
    """Launch an owned Python probe with its payload argv and Cocoa policy.

    AppKit reads the original OS argv. Strip the private suffix before executing
    the payload so paths and ordinary probe arguments retain their exact meaning.
    Offscreen children need no bootstrap and retain their original command.
    """
    command=[sys.executable,'-c',code,*map(str,arguments)]
    if sys.platform!='darwin' or (platform or os.environ.get('QT_QPA_PLATFORM'))!='cocoa':
        return command
    bootstrap=('import sys; del sys.argv[-'+str(len(_COCOA_TEST_ARGUMENTS))+':]; '
               'exec(compile('+repr(code)+',"<owned-cocoa-probe>","exec"))')
    return [sys.executable,'-c',bootstrap,*map(str,arguments),*_COCOA_TEST_ARGUMENTS]


def _bootstrap_cocoa_test_process():
    """Ignore restoration only in this owned macOS Cocoa test process.

    AppKit reads the original process arguments, even when a fixture creates
    QApplication([]). Relaunch before importing Qt or creating temporary state;
    only the exact internal suffix is removed from the Python test CLI.
    """
    arguments=sys.argv[1:]
    if sys.platform!='darwin' or any(option in arguments for option in
            ('--suite','--list-suites','--plan','--help','-h')):
        return
    native=('--cocoa' in arguments or '--phase=native' in arguments
            or any(arguments[index:index+2]==['--phase','native']
                   for index in range(len(arguments))))
    if not native:return
    size=len(_COCOA_TEST_ARGUMENTS)
    if tuple(arguments[-size:])==_COCOA_TEST_ARGUMENTS:
        del sys.argv[-size:]
        return
    os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve()),
                            *arguments,*_COCOA_TEST_ARGUMENTS])


class TimedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.case_timings=[]

    def stopTest(self,test):
        super().stopTest(test)
        # Test cleanups have stopped their workers and queued widget deletion.
        # Dispatch that deletion while the case still retains its Python model
        # and window wrappers, before TestSuite releases the completed case.
        from PySide6.QtCore import QCoreApplication,QEvent
        if QCoreApplication.instance() is not None:
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def addDuration(self,test,elapsed):
        super().addDuration(test,elapsed)
        self.case_timings.append(dict(id=test.id(),seconds=elapsed))


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def focused_cases(selectors):
    """Import only named test modules; retain module/class fixture grouping."""
    names=[normalize_selector(selector,root=ROOT) for selector in selectors]
    sys.path.insert(0,str(ROOT/'tests'))
    loader=unittest.TestLoader();unique={}
    for name in names:
        cases=list(flatten(loader.loadTestsFromName(name)))
        if loader.errors:raise ValueError('\n'.join(loader.errors))
        if len({case.id() for case in cases})!=len(cases):
            raise ValueError(f'Duplicate test identities inside {name}')
        for case in cases:unique.setdefault(case.id(),case)
    if not unique:raise ValueError('Focused selection contains no tests')
    return [unique[identity] for identity in sorted(unique)]


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
            value=dict(scope='full',phase=args.phase,inventory=inventory,selected=selected,
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
    result=dict(scope='full',phase=args.phase,inventory=inventory,selected=ids,
        ran=sum(w['ran'] for w in workers),skipped=sum(w['skipped'] for w in workers),
        expected_failures=sum(w['expected_failures'] for w in workers),passed=True,
        discovery_seconds=discovery_seconds,elapsed_seconds=time.monotonic()-started,
        case_timings=[case for w in workers for case in w.get('case_timings',[])],workers=workers)
    try:verify_workers(result)
    except ValueError as exc:result['passed']=False;print(str(exc),file=sys.stderr)
    args.report.write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['passed'] else 1


def focused_report_path(path):
    if path.resolve() in {(ROOT/'build/check'/f'{phase}.json').resolve() for phase in ('offscreen','native')}:
        raise ValueError('Focused reports cannot overwrite complete release reports')
    return path


def run_suites(args,parser):
    """Run planned areas in fresh offscreen then Cocoa processes, once per ID."""
    try:report_path=focused_report_path(args.report or ROOT/'build/check/focused/suites.json')
    except ValueError as exc:parser.error(str(exc))
    report_path.unlink(missing_ok=True)
    phase_directory=report_path.parent/(report_path.stem+'-phases')
    for phase in ('offscreen','native'):
        (phase_directory/f'{phase}.json').unlink(missing_ok=True)
    try:plan=suite_plan(args.suite,level=args.level or 'quick',selectors=args.focus or (),cocoa=args.cocoa,root=ROOT)
    except ValueError as exc:parser.error(str(exc))
    phase_directory.mkdir(parents=True,exist_ok=True)
    record=dict(scope='focused',selection='suites',**{key:value for key,value in plan.items() if key!='phases'},
                planned_phases=plan['phases'],inventory=[],selected=[],ran=0,
                skipped=0,expected_failures=0,passed=False,status='running',phases=[])
    def save():report_path.write_text(json.dumps(record,indent=2)+'\n')
    save();started=time.monotonic();seen=set();passed=True
    print(f'SUITES {", ".join(plan["suites"])} / {plan["level"]}; focused coverage, full suite not run.',flush=True)
    for phase,selectors in plan['phases'].items():
        if not selectors:continue
        output=phase_directory/f'{phase}.json';log=phase_directory/f'{phase}.log'
        command=[sys.executable,str(Path(__file__).resolve()),'--focus',*selectors,'--report',str(output)]
        if phase=='native':command.append('--cocoa')
        print(f'{phase}: {len(selectors)} planned selectors; log: {log}',flush=True)
        try:
            with log.open('w') as handle:
                completed=subprocess.run(command,stdout=handle,stderr=subprocess.STDOUT)
            child=json.loads(output.read_text());ids=child['selected']
            platform='cocoa' if phase=='native' else 'offscreen'
            if (child.get('scope')!='focused' or child.get('phase')!=phase or child.get('platform')!=platform
                    or child.get('selectors')!=selectors or not isinstance(ids,list) or not ids
                    or any(not isinstance(identity,str) for identity in ids)
                    or len(ids)!=len(set(ids)) or seen.intersection(ids) or child.get('inventory')!=ids):
                raise ValueError('Phase returned different identities, selectors or scope')
            valid=(completed.returncode==0 and child['passed'] and child['ran']==len(ids)
                   and not child['skipped'] and not child['expected_failures'])
            seen.update(ids);record['selected'].extend(ids)
            for key in ('ran','skipped','expected_failures'):record[key]+=child[key]
            receipt=dict(phase=phase,exit_code=completed.returncode,passed=bool(valid),report=child,log=str(log))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            valid=False;receipt=dict(phase=phase,passed=False,error=str(exc),log=str(log))
        record['phases'].append(receipt);passed=passed and valid
        print(f'{phase}: '+('PASS' if valid else 'FAIL'),flush=True)
        if not valid and log.exists():print(log.read_text()[-18000:],file=sys.stderr)
        save()
    record.update(inventory=list(record['selected']),passed=bool(passed and record['phases']),
                  status='complete',elapsed_seconds=time.monotonic()-started)
    save()
    print('FOCUSED SUITES '+('PASSED' if record['passed'] else 'FAILED')+
          f': {record["ran"]} cases; not a full release result.')
    return 0 if record['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('offscreen', 'native'))
    parser.add_argument('--report', type=Path)
    parser.add_argument('--focus',nargs='+',metavar='SELECTOR',help='Only these modules/classes/methods; not a release gate')
    parser.add_argument('--suite',nargs='+',action='extend',metavar='AREA',help='Combine named test areas')
    parser.add_argument('--level',choices=LEVELS,help='Named-suite depth; default quick')
    parser.add_argument('--list-suites',action='store_true',help='List available areas without importing tests')
    parser.add_argument('--plan',action='store_true',help='Preview selectors/platforms without running tests')
    parser.add_argument('--cocoa',action='store_true',help='Run focused UI/native checks on macOS Cocoa')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--partition',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list_suites:
        if args.suite or args.focus or args.level or args.plan or args.phase or args.partition:
            parser.error('--list-suites lists the catalog; select areas separately')
        try:catalog=load_catalog(root=ROOT)
        except ValueError as exc:parser.error(str(exc))
        for name,entry in catalog.items():
            counts=[];selectors=set()
            for level in LEVELS:
                selectors.update(entry[level]);counts.append(f'{level}: {len(selectors)} selectors')
            print(f'{name:12} {entry["description"]}\n             '+', '.join(counts))
        print('Levels are cumulative. Use --suite AREA... --level LEVEL; --plan previews exact coverage.')
        return 0
    if args.level and not args.suite:parser.error('--level requires --suite')
    if args.suite or args.plan:
        if args.phase or args.partition:parser.error('Named suites choose their own Qt phases')
        if args.plan:
            try:plan=suite_plan(args.suite or (),level=args.level or 'quick',selectors=args.focus or (),cocoa=args.cocoa,root=ROOT)
            except ValueError as exc:parser.error(str(exc))
            print(json.dumps(dict(plan_only=True,tests_run=False,**plan),indent=2));return 0
        return run_suites(args,parser)
    if args.focus:
        if args.partition or args.phase:parser.error('--focus chooses its own phase; use --cocoa for native UI')
        args.phase='native' if args.cocoa else 'offscreen'
        args.report=args.report or ROOT/'build/check/focused'/f'{args.phase}.json'
        try:focused_report_path(args.report)
        except ValueError as exc:parser.error(str(exc))
        os.environ['QT_QPA_PLATFORM']='cocoa' if args.cocoa else 'offscreen'
    elif not args.phase or args.report is None or args.cocoa:
        parser.error('Full checks require --phase and --report; --cocoa requires --focus')
    expected_platform = 'cocoa' if args.phase == 'native' else 'offscreen'
    if os.environ.get('QT_QPA_PLATFORM') != expected_platform:
        parser.error(f'{args.phase} requires QT_QPA_PLATFORM={expected_platform}')
    if args.workers<1 or args.workers>3:parser.error('Use one to three isolated workers')
    # A failed discovery/process must never leave an older green aggregate at
    # this run's report path. Worker reports have the same rule below.
    args.report.unlink(missing_ok=True)
    discovery_started=time.monotonic()
    cases = (focused_cases(args.focus) if args.focus else
             list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'tests')))))
    discovery_seconds=time.monotonic()-discovery_started
    native = lambda case: case.__class__.__module__.split('.')[-1] == 'test_native_deck'
    if args.focus and args.phase=='offscreen' and any(native(case) for case in cases):
        parser.error('Native deck tests require --cocoa')
    selected = cases if args.focus else [case for case in cases if native(case) == (args.phase == 'native')]
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
    if args.phase=='offscreen' and args.workers>1 and not args.partition and not args.focus:
        return parallel(args,all_ids,ids,discovery_seconds)
    if args.focus:
        print(f'FOCUSED {expected_platform}: running {len(ids)} selected tests; full suite not run.',flush=True)
    else:
        print(f'{args.phase}: running {len(ids)} of {len(all_ids)} discovered tests; '
              'remaining tests belong to the other required process.', flush=True)
    started=time.monotonic()
    suite = unittest.TestSuite(selected)
    # IDs above own the report inventory. TestSuite must be the only owner of
    # case objects so its ordinary per-case release can retire Qt resources.
    del cases, selected
    result = unittest.TextTestRunner(verbosity=1, buffer=True, durations=15,resultclass=TimedResult).run(suite)
    elapsed=time.monotonic()-started
    ok = (result.wasSuccessful() and not result.skipped and not result.expectedFailures
          and result.testsRun == len(ids))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'scope':'focused' if args.focus else 'full',
        'selectors':args.focus, 'platform':expected_platform, 'phase': args.phase, 'inventory': all_ids,
        'selected': ids, 'ran': result.testsRun, 'skipped': len(result.skipped),
        'expected_failures': len(result.expectedFailures), 'passed': ok,
        'discovery_seconds':discovery_seconds,'elapsed_seconds':elapsed,
        # Individual case durations already include setUp/tearDown/cleanups.
        'outside_case_seconds':max(0,elapsed-sum(t['seconds'] for t in result.case_timings)),
        'case_timings':result.case_timings}, indent=2)+'\n')
    if not ok:
        print(('FOCUSED CHECK FAILED' if args.focus else 'RELEASE TEST GATE FAILED')+
              ': every selected test must run and pass.', file=sys.stderr)
    elif args.focus:
        print(f'FOCUSED CHECK PASSED: {len(ids)} tests; not a full release result.')
    return 0 if ok else 1


if __name__ == '__main__':
    _bootstrap_cocoa_test_process()
    if any(option in sys.argv for option in ('--suite','--list-suites','--plan','--help','-h')):
        raise SystemExit(main())
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
