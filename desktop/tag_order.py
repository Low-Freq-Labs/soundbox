"""Native tag order admission; non-native sources retain a display preference."""
import json

KEY = 'my_tag_order'


def _scope(context):
    return json.dumps(list(context), ensure_ascii=True, separators=(',', ':'))


def ordered_tags(tags, settings, context):
    if context is not None and context[1] == 'rekordbox-db':return list(tags)
    saved = settings.get(KEY, {})
    rows = saved.get(_scope(context), ()) if isinstance(saved, dict) and context is not None else ()
    by_key = {(tag.tag_id, tag.source_uuid): tag for tag in tags}
    if not isinstance(rows, (tuple, list)) or len(rows) > 16_384:
        return list(tags)
    keys = [tuple(row) for row in rows if isinstance(row, (tuple, list)) and len(row) == 2
            and all(type(value) is str for value in row)]
    known = dict.fromkeys(key for key in keys if key in by_key)
    return [*(by_key[key] for key in known), *(tag for tag in tags if (tag.tag_id, tag.source_uuid) not in known)]


def save_order(window, order, binding):
    button = window.my_tags
    lib = window.lib
    context = (str(lib.source), lib.source_kind, lib.source_library_id) if lib else None
    if (window._shutting_down or window._state_maintenance or not lib
            or binding != button.order_binding() or binding[0] != context):
        button.restore_order(binding)
        return
    ids = tuple(order)
    by_id = {tag.tag_id: tag for tag in lib.source_tags}
    if len(by_id) != len(lib.source_tags) or len(ids) != len(by_id) or set(ids) != set(by_id):
        button.restore_order(binding)
        return
    if lib.source_kind == 'rekordbox-db':
        from soundbox.rekordbox_tag_order import request_for
        controller=window.tag_definition_controller
        reason=controller.order_reason()
        try:
            if reason:raise ValueError(reason)
            request=request_for(lib.source_library_id,lib.source_tags,ids)
            if not controller.enqueue_order(request):
                raise ValueError('The native tag order could not be admitted; retry after pending changes finish')
        except (OSError,ValueError,RuntimeError) as exc:
            button.restore_order(binding)
            window._flash_status(str(exc),15)
            return
        window._sync_tags()
        return
    before = window.settings.get(KEY)
    value = {**(before if isinstance(before, dict) else {}),
             _scope(context): [[tid, by_id[tid].source_uuid] for tid in ids]}
    window.settings[KEY] = value
    window._schedule_settings_save(KEY)
    if not window._flush_settings_preferences():
        if before is None:window.settings.pop(KEY, None)
        else:window.settings[KEY] = before
        button.restore_order(binding)
        return
    window._sync_tags()
