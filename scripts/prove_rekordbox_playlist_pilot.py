#!/usr/bin/env python3
"""Prepare/show one playlist pilot; publication requires a separate approval file.

No default source, automatic approval, production authority provisioning, audio
access, native UI control or device writes. Keep the reviewed plan and approval
for explicit recovery. This historical pilot never grants ordinary source-write
authority; production playlist and initial-analysis stores have separate gates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from research.cdj_export.playlist_pilot import PlaylistPilot  # noqa: E402
from research.cdj_export.playlist_pilot_plan import prepare, load_plan  # noqa: E402
from research.cdj_export.playlist_transaction import _sha  # noqa: E402
from soundbox.state_io import atomic_bytes  # noqa: E402


def preview(folder, record):
    counter = [c for c in record['changes'] if c['table']=='agentRegistry']
    return dict(plan=str(folder/'plan.json'),plan_sha256=_sha(folder/'plan.json'),
        operation_id=record['operation_id'],source=record['source'],
        library_id=record['library_id'],application_version=record['application_version'],
        recovery_root=record['recovery_root'],playlist_id=record['playlist_id'],
        playlist_name=record['playlist_name'],ordered_tracks=record['content_refs'],
        local_counter_before=counter[0]['before']['int_1'],local_counter_after=counter[-1]['after']['int_1'],
        changed_tables=['agentRegistry','djmdPlaylist','djmdSongPlaylist'],
        new_playlists=1,new_memberships=2,existing_collection_rows_changed=0,
        audio_analysis_cues_changed=False,encrypted_before_after_backups=True,
        plan_confers_source_authority=False,native_acceptance=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    make = commands.add_parser('prepare')
    make.add_argument('--source',type=Path,required=True)
    make.add_argument('--data-root',type=Path,required=True)
    make.add_argument('--output',type=Path,required=True)
    make.add_argument('--track-id',action='append',required=True)
    show = commands.add_parser('show');show.add_argument('--plan',type=Path,required=True)
    for operation in ('publish','recover'):
        command = commands.add_parser(operation)
        command.add_argument('--plan',type=Path,required=True)
        command.add_argument('--approval',type=Path,required=True)
    args = parser.parse_args()
    if args.command=='prepare':
        record = prepare(args.source,args.output,args.track_id,data_root=args.data_root)
        report = preview(args.output,record)
        atomic_bytes(args.output/'preview.json',json.dumps(report,indent=2).encode()+b'\n')
    elif args.command=='show':report = preview(args.plan,load_plan(args.plan))
    else:
        store = PlaylistPilot(args.plan,args.approval)
        outcome = store.apply() if args.command=='publish' else store.recover()
        report = dict(command=args.command,outcome=outcome,playlist_id=store.plan['playlist_id'],
            retained_backups=str(store.archive),native_acceptance=False,production_authority_enabled=False)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
