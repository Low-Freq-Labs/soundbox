"""Independent bounded DeviceSQL page/index validation; no filesystem access."""
import hashlib
import struct


def audit(data: bytes) -> dict:
    """Inspect bounded table chains and row indexes, retaining diagnostic errors."""
    result: dict = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
              "issues": [], "tables": []}
    issues = result["issues"]
    if len(data) < 28:
        issues.append("Truncated file header")
        return result
    _, size, count, next_unused, marker, sequence, _ = struct.unpack_from("<7I", data)
    result.update(page_size=size, table_count=count, next_unused=next_unused,
                  header_marker=marker, sequence=sequence)
    if size < 40 or size > 65536 or len(data) % size or len(data) < size:
        issues.append("Invalid page size or truncated page")
        return result
    if 28 + 16 * count > size:
        issues.append("Table directory extends beyond header page")
        return result
    result["physical_pages"] = len(data) // size
    for t in range(count):
        kind, candidate, first, last = struct.unpack_from("<4I", data, 28 + t * 16)
        table: dict = {"type": kind, "empty_candidate": candidate, "first": first,
                 "last": last, "pages": [], "present_rows": 0}
        result["tables"].append(table)
        # Empty candidates legitimately name pages beyond EOF. Do not follow them.
        seen = set()
        index = first
        while True:
            label = f"Table {kind}, page {index}"
            if index in seen or index < 1 or index >= result["physical_pages"]:
                issues.append(f"{label}: cycle or page outside file")
                break
            seen.add(index)
            page = data[index * size:(index + 1) * size]
            stored_index, stored_type, following = struct.unpack_from("<3I", page, 4)
            flags = page[27]
            packed = int.from_bytes(page[24:27], "little")
            slots, declared = packed & 0x1FFF, packed >> 13
            report: dict = {"index": index, "flags": flags,
                      "count_bytes": page[24:27].hex(" "), "slots": slots,
                      "declared_present": declared,
                      "last_write": list(struct.unpack_from("<2H", page, 32))}
            table["pages"].append(report)
            if (stored_index, stored_type) != (index, kind):
                issues.append(f"{label}: page identity/type disagrees with directory")
            if index == first and not flags & 0x40:
                issues.append(f"{label}: table starts with a data page")
            if not flags & 0x40:
                used = struct.unpack_from("<H", page, 30)[0]
                groups = (slots + 15) // 16
                heap_end, index_start = 40 + used, size - groups * 36
                if heap_end > index_start or index_start < 40:
                    issues.append(f"{label}: row heap/index overlap (slots={slots})")
                else:
                    present = 0
                    for slot in range(slots):
                        group, within = divmod(slot, 16)
                        base = size - group * 36
                        mask = struct.unpack_from("<H", page, base - 4)[0]
                        if mask & (1 << within):
                            present += 1
                            offset = struct.unpack_from("<H", page, base - 6 - within * 2)[0]
                            if offset >= used:
                                issues.append(f"{label}: live row {slot} outside heap")
                            elif offset + 4 <= used:
                                subtype, shift = struct.unpack_from("<2H", page, 40 + offset)
                                typed = ((kind == 0 and subtype == 0x24)
                                         or (kind == 2 and subtype in (0x60, 0x64))
                                         or (kind == 3 and subtype in (0x80, 0x680))
                                         or (kind == 19 and subtype == 0x280))
                                if typed and shift != slot * 32:
                                    issues.append(f"{label}: typed row {slot} has wrong page-slot identity")
                    report["observed_present"] = present
                    table["present_rows"] += present
                    if present != declared:
                        issues.append(f"{label}: header declares {declared}, masks contain {present}")
            if index == last:
                break
            if following == 0 or following == index:
                issues.append(f"{label}: chain ended before declared last page {last}")
                break
            index = following
    return result
