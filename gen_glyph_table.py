import csv, struct, os, sys, argparse
from collections import OrderedDict

FONT_INDEX_MAX = 10531
A_BASE = 0x8140
B_BASE = 0xC182
A_LEAD = range(0x81, 0xA0)
B_LEAD = range(0xE0, 0xF0)
TRAIL1 = range(0x40, 0x7F)
TRAIL2 = range(0x80, 0xFD)


def valid_trail(b2: int) -> bool:
    return b2 in TRAIL1 or b2 in TRAIL2


def decode_sjis(code: int) -> str | None:
    try:
        return bytes([(code >> 8) & 0xFF, code & 0xFF]).decode('shift_jis')
    except:
        return None


def read_font_index(path: str) -> list[int]:
    with open(path, 'rb') as f:
        raw = f.read()
    return list(struct.unpack(f'<{len(raw)//2}H', raw))


def iter_sjis(lead: range, base: int):
    for b1 in lead:
        for b2 in range(0x40, 0xFD):
            if not valid_trail(b2):
                continue
            code = (b1 << 8) | b2
            yield code - base, code


def build_existing(font_indexs: list[int]) -> list[OrderedDict]:
    entries: list[tuple[int, int, int, str, str]] = []

    for font_index, code in iter_sjis(A_LEAD, A_BASE):
        if font_index >= FONT_INDEX_MAX or font_indexs[font_index] == 0:
            continue
        ch = decode_sjis(code)
        if not ch:
            continue
        entries.append((font_indexs[font_index], font_index, code, ch, ''))

    for font_index, code in iter_sjis(B_LEAD, B_BASE):
        if font_index >= FONT_INDEX_MAX or font_indexs[font_index] == 0:
            continue
        ch = decode_sjis(code)
        if not ch:
            continue
        entries.append((font_indexs[font_index], font_index, code, ch, ''))

    # tex_slot 0 = hardcoded full-width dot
    entries.append((0, 5, 0x8145, '・', 'hardcoded'))
    entries.sort(key=lambda r: r[0])

    rows: list[OrderedDict] = []
    for slot, font_index, code, ch, note in entries:
        rows.append(OrderedDict([
            ('index', str(slot)),
            ('sjis', '0x%04X' % code),
            ('ucs', 'U+%04X' % ord(ch)),
            ('char', ch),
            ('replace', ''),
            ('note', note),
        ]))
    return rows


def get_font_index(sjis: int) -> int | None:
    b1 = (sjis >> 8) & 0xFF
    if b1 in A_LEAD:
        return sjis - A_BASE
    if b1 in B_LEAD:
        return sjis - B_BASE
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description='Build glyph table')
    parser.add_argument('-i', required=True, nargs='+',
                        help='translated CSV(s), comma-separated')
    parser.add_argument('-f', '--font-index', default='DATA1/font/font_16_a.txt')
    parser.add_argument('-o', default='glyph_table.csv',
                        help='output CSV path')
    args = parser.parse_args()

    font_indexs = read_font_index(args.font_index)

    existing = build_existing(font_indexs)
    char_map = {r['char']: r for r in existing}
    max_slot = max(int(r['index']) for r in existing) if existing else -1

    needed: set[str] = set()
    csv_files = []
    for arg in args.i:
        csv_files.extend(arg.split(','))
    for csv_path in csv_files:
        csv_path = csv_path.strip()
        if not csv_path:
            continue
        with open(csv_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                val = (row.get('translation') or '').strip()
                for ch in val:
                    if ch in ('\n', '\r', '\t'):
                        continue
                    if ch not in char_map:
                        needed.add(ch)

    to_add = sorted(ch for ch in needed if not can_render(ch, char_map, font_indexs))

    if not to_add:
        _write_csv(args.o, existing)
        print(args.o)
        return

    used_font_indexs: set[int] = {r['sjis'] for r in existing}
    next_slot = max_slot + 1
    new_rows: list[OrderedDict] = []

    sjis_native_rows: list[tuple[str, int, OrderedDict]] = []
    carrier_chars: list[str] = []

    for ch in to_add:
        native_code = None
        try:
            b = ch.encode('shift_jis')
            if len(b) == 2:
                code = (b[0] << 8) | b[1]
                font_index = get_font_index(code)
                if font_index is not None and 0 <= font_index < FONT_INDEX_MAX and font_indexs[font_index] == 0:
                    native_code = code
        except UnicodeEncodeError:
            pass

        if native_code is not None and native_code not in used_font_indexs:
            used_font_indexs.add(native_code)
            sjis_native_rows.append((ch, native_code, OrderedDict([
                ('index', str(next_slot)),
                ('sjis', '0x%04X' % native_code),
                ('ucs', 'U+%04X' % ord(ch)),
                ('char', ch),
                ('replace', ''),
                ('note', ''),
            ])))
            next_slot += 1
        else:
            carrier_chars.append(ch)

    MIN_CARRIER_INDEX = 1410
    unused_carriers: list[tuple[int, int, str]] = []
    for font_index, code in iter_sjis(A_LEAD, A_BASE):
        if font_index < MIN_CARRIER_INDEX or font_index >= FONT_INDEX_MAX or font_indexs[font_index] != 0:
            continue
        if code in used_font_indexs:
            continue
        carrier = decode_sjis(code)
        if carrier:
            unused_carriers.append((font_index, code, carrier))

    if len(unused_carriers) < len(carrier_chars):
        print(f"ERROR: need {len(carrier_chars)} carriers, only {len(unused_carriers)} available")
        sys.exit(1)

    carrier_iter = iter(unused_carriers)
    for ch in carrier_chars:
        font_index, code, carrier = next(carrier_iter)
        used_font_indexs.add(code)
        new_rows.append(OrderedDict([
            ('index', str(next_slot)),
            ('sjis', '0x%04X' % code),
            ('ucs', 'U+%04X' % ord(ch)),
            ('char', carrier),
            ('replace', ch),
            ('note', ''),
        ]))
        next_slot += 1

    for row in sjis_native_rows:
        new_rows.append(row[2])

    new_rows.sort(key=lambda r: int(r['index']))
    all_rows = existing + new_rows
    _write_csv(args.o, all_rows)
    print(f"Saved -> {args.o}")


def can_render(ch: str, char_map: dict, font_indexs: list[int]) -> bool:
    if ch in char_map:
        return True
    try:
        b = ch.encode('shift_jis')
        if len(b) == 1 and (0x20 <= b[0] <= 0x7E or 0xA1 <= b[0] <= 0xDF):
            return True
        if len(b) != 2:
            return False
        font_index = get_font_index((b[0] << 8) | b[1])
        return font_index is not None and 0 <= font_index < FONT_INDEX_MAX and font_indexs[font_index] != 0
    except:
        return False


def _write_csv(path, rows):
    fieldnames = ['index', 'sjis', 'ucs', 'char', 'replace', 'note']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
