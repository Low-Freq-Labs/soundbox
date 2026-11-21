"""Refresh scheduling hints from a complete passing offscreen report only."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.verify_test_reports import verify_workers


def measured_costs(report):
    if (report.get('scope','full')!='full' or report.get('phase')!='offscreen'
            or not report.get('passed') or report.get('skipped') or report.get('expected_failures')):
        raise ValueError('Use a complete passing offscreen report')
    verify_workers(report)
    selected=report['selected'];timings=report['case_timings']
    identities=[row['id'] for row in timings]
    if len(identities)!=len(set(identities)) or set(identities)!=set(selected):
        raise ValueError('Every selected case needs one measured duration')
    costs=defaultdict(float)
    for row in timings:
        duration=row['seconds']
        if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<0:
            raise ValueError('Invalid case duration')
        costs[row['id'].split('.')[0]]+=duration
    return {module:round(seconds,3) for module,seconds in sorted(costs.items())}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args=parser.parse_args()
    costs=measured_costs(json.loads(args.report.read_text()))
    (ROOT/'scripts/test-module-costs.json').write_text(json.dumps(costs,indent=2)+'\n')
    print(f'Refreshed {len(costs)} module hints; scheduling only, no tests excluded.')


if __name__=='__main__':main()
