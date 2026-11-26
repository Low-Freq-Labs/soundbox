"""Worker-only routing for retained stock media; never writer/media authority.

Metadata, artwork, order and names still pass the fresh catalog planner. This
check avoids reacquiring unchanged selected media only; any uncertain baseline
returns to the ordinary full planner, including reference-counted pruning.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path,PurePosixPath
import stat

from .library import Library,Playlist
from . import manifest
from .operation_control import checkpoint,OperationCancelled
from .source_read_retry import is_cloud_storage_source
from .stock_ipod_catalog import STOCK_AUDIO_SUFFIXES
from .stock_ipod_catalog_state import StockCatalogWitness
from .stock_ipod_info import StockIpodSnapshot
from .stock_ipod_playlist_binding import playlist_binding_from_dict,source_playlist_identity,PlaylistBinding
from .stock_ipod_shadow import StockIpodShadow
from .stock_plan_observations import NegativeSourceOmissions,STOCK_SOURCE_PROFILE,source_epoch


def _digest(value):
    return type(value)is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _integer(value):
    return type(value)is int and value>=0


def _regular_source(path,check):
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.resolve(strict=True)!=path:return False
    if not check(before):return False
    return source_epoch(path.lstat())==source_epoch(before) and path.resolve(strict=True)==path


def _retained_source(path,row,native,dbid):
    if (not isinstance(row,dict) or path.suffix.lower() not in STOCK_AUDIO_SUFFIXES
            or row.get('stock_media_profile')!=STOCK_SOURCE_PROFILE
            or type(row.get('dbid'))is not int or row['dbid']!=dbid
            or not _digest(row.get('sha256')) or not _digest(row.get('source_sha256'))
            or not _integer(row.get('size')) or not _integer(row.get('source_size'))
            or not _integer(row.get('mtime_ns'))):return False
    epoch=row.get('source_epoch');technical=row.get('stock_technical')
    if (not isinstance(epoch,(list,tuple)) or len(epoch)!=5 or not all(map(_integer,epoch))
            or not isinstance(technical,(list,tuple)) or len(technical)!=3
            or not all(map(_integer,technical))):return False
    device_path=row.get('device_path')
    if type(device_path)is not str or '\0' in device_path:return False
    relative=PurePosixPath(device_path)
    if (relative.is_absolute() or '..' in relative.parts or str(relative)!=device_path
            or relative.parts[:2]!=('iPod_Control','Music') or len(relative.parts)<3
            or native.location!=':'+':'.join(relative.parts)
            or type(native.size)is not int or native.size!=row['size']):return False
    def matches(observed):
        current=(observed.st_dev,observed.st_ino,observed.st_size,observed.st_mtime_ns,observed.st_ctime_ns)
        return (tuple(epoch[:4])==current[:4] and row['source_size']==current[2]
                and row['mtime_ns']==current[3]
                and (tuple(epoch)==current or is_cloud_storage_source(path)))
    return _regular_source(path,matches)


def stock_shadow_selection_current(library: Library,selected: list[Playlist],
                                   shadow: StockIpodShadow | None) -> bool:
    """Catalog no-ops cannot accept a stale full-source playlist baseline.

    A prior witness-only completion can already describe omitted new members
    while the committed shadow still predates them. Only the full planner can
    validate those sources and renew that shadow at explicit acceptance.
    """
    if not isinstance(shadow,StockIpodShadow) or not selected:return False
    keys=set()
    for playlist in selected:
        checkpoint()
        key=manifest.playlist_key(playlist)
        if key in keys:return False
        keys.add(key)
        current=manifest.entry_for(playlist,getattr(library,'hidden_ids',frozenset()),
                                   getattr(library,'removed_by_playlist',None))
        saved=shadow.playlists.get(key)
        if not isinstance(saved,dict) or any(saved.get(field)!=value for field,value in current.items()):
            return False
    return True


def stock_selection_reusable(library: Library,selected: list[Playlist],snapshot: StockIpodSnapshot,
                             witness: StockCatalogWitness | None,shadow: StockIpodShadow | None,
                             omissions: NegativeSourceOmissions | None) -> bool:
    """Choose catalog routing only from unchanged selected occurrence/media facts.

    Changed source order/name/display/art is allowed for catalog comparison,
    but the device catalog and artwork must still match the saved baseline.
    Source membership changes, unknown omissions or stale source/native bindings
    require the full planner. No source, device audio or DB is opened here.
    """
    try:
        checkpoint()
        if (not selected or not isinstance(witness,StockCatalogWitness)
                or not isinstance(shadow,StockIpodShadow) or snapshot.identity_conflict
                or witness.source!=(str(library.source),library.source_kind,library.source_library_id)
                or witness.device_key!=snapshot.identity_key or shadow.identity_key!=snapshot.identity_key
                or witness.database_sha256!=snapshot.database_sha256
                or witness.artwork_database_sha256!=snapshot.artwork_database_sha256
                or shadow.database_sha256!=snapshot.database_sha256
                or shadow.artwork_database_sha256!=snapshot.artwork_database_sha256
                or shadow.model and snapshot.model and shadow.model!=snapshot.model
                or shadow.serial_number and snapshot.serial_number and shadow.serial_number!=snapshot.serial_number):return False
        sources=[source_playlist_identity(library,playlist) for playlist in selected]
        if (any(source.identity_error for source in sources)
                or len({source.key for source in sources})!=len(sources)):
            return False
        native_ids=[source.native_id for source in sources if source.native_id]
        if len(set(native_ids))!=len(native_ids):return False
        binding_keys=Counter(binding.key for binding in witness.bindings)
        device_owners=Counter(binding.device_playlist_id for binding in witness.bindings)
        bindings={binding.key:binding for binding in witness.bindings}
        native_counts=Counter(row.dbid for row in snapshot.tracks)
        native_rows={row.dbid:row for row in snapshot.tracks}
        playlists=Counter(row.playlist_id for row in snapshot.playlists if row.kind=='Playlist')
        checked: dict[Path,tuple[str,bool,int | None]] = {}
        exported_owners: dict[int,str] = {}
        exported_links: dict[str,int] = {}
        for source in sources:
            checkpoint()
            if binding_keys[source.key]!=1:return False
            binding=bindings[source.key]
            binding=playlist_binding_from_dict({key:getattr(binding,key) for key in PlaylistBinding.__dataclass_fields__})
            if ((binding.native_id,binding.uuid)!=(source.native_id,source.uuid)
                    or device_owners[binding.device_playlist_id]!=1 or playlists[binding.device_playlist_id]!=1
                    or Counter(source.identities)!=Counter(binding.source_members)):return False
            remaining=Counter(local for local,_dbid in binding.members)
            links=dict(binding.members)
            for identity in source.identities:
                checkpoint()
                local=identity[0];path=Path(identity[4])
                if not path.is_absolute():return False
                exported=remaining[local]>0
                if exported:remaining[local]-=1
                dbid=links[local] if exported else None
                if dbid is not None:
                    if local in exported_links and exported_links[local]!=dbid:return False
                    exported_links[local]=dbid
                role=(local,exported,dbid)
                if path in checked:
                    if checked[path]!=role:return False
                    continue
                if dbid is not None:
                    if (native_counts[dbid]!=1 or dbid in exported_owners and exported_owners[dbid]!=local
                            or not _retained_source(path,shadow.sources.get(str(path)),native_rows[dbid],dbid)):
                        return False
                    exported_owners[dbid]=local
                elif path.suffix.lower() not in STOCK_AUDIO_SUFFIXES:
                    if not _regular_source(path,lambda _observed:True):return False
                elif (not isinstance(omissions,NegativeSourceOmissions) or omissions.profile!=STOCK_SOURCE_PROFILE
                        or not omissions.current(path)):
                    return False
                checked[path]=role
        return True
    except OperationCancelled:
        raise
    except (OSError,ValueError,TypeError,AttributeError,KeyError,RuntimeError):
        return False
