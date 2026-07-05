import argparse, csv, struct, os

def sjis_to_index(sjis: int) -> int | None:
    lead = (sjis >> 8) & 0xFF
    trail = sjis & 0xFF

    if 0x81 <= lead <= 0x9F:
        iVar6 = lead * 2 - 0x101  # row = 2 * (lead - 0x81) + 1
        ext = 0
    elif 0xE0 <= lead <= 0xEF:
        iVar6 = lead * 2 - 0x181
        ext = 0
    elif 0xF0 <= lead <= 0xFC:
        iVar6 = lead * 2 - 0x1D9
        ext = 1
    else:
        return None

    if 0x40 <= trail <= 0x7E:
        cell = trail - 0x3F  # cell 1-63
    elif 0x80 <= trail <= 0x9E:
        cell = trail - 0x40  # cell 64-94
    elif 0x9F <= trail <= 0xFC:
        cell = trail - 0x9E  # cell 1-94, next row
        iVar6 += 1
    else:
        return None

    index = (ext << 15) | ((iVar6 + 0x20) << 8) | (cell + 0x20)
    return index & 0xFFFF


def main():
    ap = argparse.ArgumentParser(description='Patch jis2ucs.bin')
    ap.add_argument('-i', '--input', required=True, help='Input jis2ucs.bin')
    ap.add_argument('-o', '--output', required=True, help='Output jis2ucs.bin')
    ap.add_argument('-g', '--glyph-table', required=True, help='glyph_table.csv')
    args = ap.parse_args()

    with open(args.input, 'rb') as f:
        data = bytearray(f.read())

    count = len(data) // 2
    print(f'jis2ucs.bin: {len(data)} bytes, {count} entries')

    rows = []
    with open(args.glyph_table, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            rows.append(row)

    carrier_map: list[tuple[int, int, str, str]] = []
    for row in rows:
        carrier_char = (row.get('char') or '').strip()
        replace_char = (row.get('replace') or '').strip()
        sjis_str = (row.get('sjis') or '').strip()
        if not carrier_char or not replace_char or not sjis_str:
            continue
        sjis_code = int(sjis_str, 16)
        idx = sjis_to_index(sjis_code)
        if idx is None:
            print(f'  WARN: cannot compute index for SJIS 0x{sjis_code:04X} ({carrier_char})')
            continue
        if idx >= count:
            print(f'  WARN: index 0x{idx:04X} out of range for SJIS 0x{sjis_code:04X}')
            continue
        carrier_ucs = ord(carrier_char)
        replace_ucs = ord(replace_char)
        current = struct.unpack('<H', data[idx*2:idx*2+2])[0]
        if current != carrier_ucs:
            print(f'  NOTE: SJIS 0x{sjis_code:04X} idx={idx:04X}: expected U+{carrier_ucs:04X} but table has U+{current:04X}')
        carrier_map.append((idx, sjis_code, current, replace_ucs))

    print(f'\nCarriers to patch: {len(carrier_map)}')

    patched = 0
    for idx, sjis_code, old_ucs, new_ucs in carrier_map:
        struct.pack_into('<H', data, idx * 2, new_ucs)
        patched += 1
        if patched <= 5:
            print(f'  U+{old_ucs:04X} -> U+{new_ucs:04X} [SJIS 0x{sjis_code:04X}]')

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'wb') as f:
        f.write(data)
    print(f'\nWritten to: {args.output}')
    print(f'Modified entries: {patched}')


if __name__ == '__main__':
    main()
