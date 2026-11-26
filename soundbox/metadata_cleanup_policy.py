"""Exact-source automatic cleanup choices, independent of native authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

KEY = 'metadata_cleanup_policy'
FORMAT = 'soundbox-metadata-cleanup-policy-v1'
MODES = frozenset(('clean', 'original'))


def source_scope(library):
    scope = dict(source=str(library.source), kind=library.source_kind,
                 library_id=library.source_library_id)
    _validate_scope(scope)
    return scope


def _validate_scope(scope):
    if (type(scope) is not dict or set(scope) != {'source', 'kind', 'library_id'}
            or any(type(value) is not str or '\0' in value or len(value) > 4096
                   for value in scope.values())
            or not Path(scope['source']).is_absolute() or not scope['kind']
            or scope['kind'] == 'rekordbox-db' and not scope['library_id']):
        raise ValueError('A verified library identity is required for the cleanup choice')


def scope_key(scope):
    _validate_scope(scope)
    raw = json.dumps(scope, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def validate(value):
    if (type(value) is not dict or set(value) != {'format', 'choices'}
            or value['format'] != FORMAT or type(value['choices']) is not dict
            or len(value['choices']) > 128):
        raise ValueError('Invalid library cleanup choices')
    for identity, row in value['choices'].items():
        if (type(row) is not dict or set(row) != {'scope', 'mode'}
                or type(row['mode']) is not str or row['mode'] not in MODES
                or identity != scope_key(row['scope'])):
            raise ValueError('Invalid source-bound cleanup choice')
    return value


def choice_for(settings, library):
    """Missing legacy policy preserves behavior; invalid policy cannot opt in.

    First-run UI must hold publication until its choice has been durably saved.
    This function grants neither write permission nor onboarding completion.
    """
    if KEY not in settings:
        return 'clean'
    try:
        value = validate(settings[KEY])
        row = value['choices'].get(scope_key(source_scope(library)))
        return row['mode'] if row is not None else 'original'
    except (ValueError, TypeError, AttributeError, KeyError):
        return 'original'


def with_choice(settings, library, mode):
    """Return detached settings for the caller's ordinary durable state save."""
    if type(mode) is not str or mode not in MODES:
        raise ValueError('Choose Clean titles/artists or Keep originals')
    current = settings.get(KEY, {'format': FORMAT, 'choices': {}})
    validate(current)
    scope = source_scope(library)
    choices = deepcopy(current['choices'])
    choices[scope_key(scope)] = dict(scope=scope, mode=mode)
    result = {**settings, KEY: dict(format=FORMAT, choices=choices)}
    validate(result[KEY])
    return result


def original_values(local, title, artist, *, titles, artists, aliases, info):
    """Raw native values plus explicit curation; no inferred parsing or casing."""
    selected_title = titles.get(local)
    if selected_title and selected_title.strip():
        title = selected_title.strip()
    if local in artists and artists[local].strip():
        artist = artists[local].strip()
    seen = set()
    while artist in aliases and artist not in seen:
        seen.add(artist)
        artist = aliases[artist]
    return info.get('title', title), info.get('artist', artist)
