"""Strict bare-import replay against explicit frozen native captures.

Only native-generated identity/clock seeds are taken from the expected row; all
other fields are constructed. Every reference-only difference stays in the report.
A mismatch exits nonzero. No source publication or discovery exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from soundbox.rekordbox_write import _cell, _connect, _rows, _state
from research.cdj_export.playlist_reference import compare_rows
from research.cdj_export.playlist_transaction import _sha
from .registration_workspace import OfflineRegistrationWorkspace


def replay(before, after, source, output):
    before, after = (Path(p).resolve(strict=True) for p in (before, after))
    source = Path(source).absolute()
    output = Path(output).absolute()
    if output.exists() or output.is_relative_to(before) or output.is_relative_to(after) or output == source:
        raise ValueError('Report must be a new path outside the protected inputs')
    protected = {path: _sha(path)
                 for directory in (before, after) for path in directory.iterdir() if path.is_file()}
    workspace = OfflineRegistrationWorkspace.capture(before)
    finished = False
    try:
        with tempfile.TemporaryDirectory(prefix='soundbox-native-registration-audit-') as area:
            root = Path(area).resolve()
            shutil.copyfile(after/'master.db', root/'native.db')
            conn = _connect(root/'native.db', readonly=True)
            left = _connect(workspace.database, readonly=True)
            try:
                native = _state(conn)
                if native != json.loads((after/'state.json').read_bytes()):
                    raise ValueError('Native after capture does not match its recorded inventory')
                old = {r['ID']: r for r in _rows(left, 'djmdContent')}
                added = [r for r in _rows(conn, 'djmdContent') if r['ID'] not in old]
                if len(added) != 1 or added[0]['FolderPath'] != str(source):
                    raise ValueError('Expected one exact-path native import')
                target = added[0]
                registry_before = [{k: _cell(v) for k, v in r.items()} for r in _rows(left, 'agentRegistry')]
                registry_native = [{k: _cell(v) for k, v in r.items()} for r in _rows(conn, 'agentRegistry')]
            finally:
                left.close(); conn.close()
            audio = workspace.inspect_wave(source)
            plan = workspace.plan(audio, identity=target['ID'], uuid=target['UUID'],
                                  timestamp=target['created_at'], day=target['StockDate'])
            if plan['candidate']['row'] != target:
                differences = [k for k in plan['candidate']['row'].keys() | target.keys()
                               if plan['candidate']['row'].get(k) != target.get(k)]
                raise ValueError('Constructed registration differs from native fields: '+','.join(sorted(differences)))
            outcome = workspace.apply(plan)
            conn = _connect(workspace.database, readonly=True)
            try:
                actual = _state(conn)
                registry_actual = [{k: _cell(v) for k, v in r.items()} for r in _rows(conn, 'agentRegistry')]
            finally:
                conn.close()
            complete = actual == native
            report = dict(complete_semantic_match=complete, constructor_exact_native_row=True,
                encrypted_copy_commit=outcome, idempotent_replay=workspace.apply(plan),
                mismatched_reference_tables=sorted(t for t in native['tables'].keys() | actual['tables'].keys()
                    if native['tables'].get(t) != actual['tables'].get(t)),
                schema_match=actual['schema']==native['schema'], metadata_match=actual['metadata']==native['metadata'],
                registry_reference_delta=compare_rows({'agentRegistry':registry_before},{'agentRegistry':registry_native}),
                registry_remaining_delta=compare_rows({'agentRegistry':registry_actual},{'agentRegistry':registry_native}),
                xml_preserved=(workspace.root/'masterPlaylists6.xml').read_bytes()==(before/'masterPlaylists6.xml').read_bytes(),
                xml_reference_match=(workspace.root/'masterPlaylists6.xml').read_bytes()==(after/'masterPlaylists6.xml').read_bytes(),
                source_sha256=audio.sha256, source_write_authority=False,
                plan=plan, transaction_receipt=json.loads((workspace.root/'last-result.json').read_bytes()),
                scope='One bare integral untagged PCM16 stereo44.1kHz WAV; no analysis/metadata publication')
            audio.revalidate()
            report['protected_capture_bytes_unchanged'] = all(
                _sha(path)==digest for path,digest in protected.items())
            if not report['protected_capture_bytes_unchanged']:
                raise ValueError('Frozen inputs changed during replay')
            output.parent.mkdir(parents=True,exist_ok=True)
            # Exclusive creation prevents accidental replacement of old evidence.
            with output.open('x') as stream:
                json.dump(report,stream,indent=2)
            finished = True
            return report
    except BaseException as exc:
        exc.add_note(f'Incomplete registration replay retained at {workspace.root}')
        raise
    finally:
        if finished:
            # Successful copy transaction has a retained receipt/report. Strict
            # reference mismatch is reported separately and still exits nonzero.
            shutil.rmtree(workspace.root)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path); parser.add_argument('after',type=Path)
    parser.add_argument('source',type=Path); parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    report=replay(args.before,args.after,args.source,args.report)
    print(json.dumps({k:v for k,v in report.items() if not k.startswith('registry_')
                     and k not in ('plan','transaction_receipt')},indent=2))
    return 0 if report['complete_semantic_match'] and report['xml_reference_match'] else 1


if __name__=='__main__':
    raise SystemExit(main())
