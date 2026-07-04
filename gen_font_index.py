import struct, csv, sys, argparse

FONT_INDEX_SIZE = 10531
A_BASE = 0x8140
B_BASE = 0xC182
A_LEAD = range(0x81, 0xA0)
B_LEAD = range(0xE0, 0xF0)


def get_font_index(sjis: int) -> int | None:
    b1 = (sjis >> 8) & 0xFF
    if b1 in A_LEAD:
        return sjis - A_BASE
    if b1 in B_LEAD:
        return sjis - B_BASE
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate font_16_a.txt')
    parser.add_argument('-i', required=True, help='input glyph_table.csv')
    parser.add_argument('-o', required=True, help='output font_16_a.txt')
    args = parser.parse_args()

    entries = [0] * FONT_INDEX_SIZE
    written = 0
    skipped = 0

    with open(args.i, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            idx_str = (row.get('index') or '').strip()
            sjis_str = (row.get('sjis') or '').strip()
            note = (row.get('note') or '').strip()

            if note == 'hardcoded':
                skipped += 1
                continue
            if not idx_str or not sjis_str.startswith('0x'):
                skipped += 1
                continue

            slot = int(idx_str)
            sjis = int(sjis_str, 16)
            font_index = get_font_index(sjis)

            if font_index is None or not (0 <= font_index < FONT_INDEX_SIZE):
                skipped += 1
                continue

            entries[font_index] = slot
            written += 1

    with open(args.o, 'wb') as f:
        f.write(struct.pack(f'<{FONT_INDEX_SIZE}H', *entries))

    print(f'[OK] {args.o} written')
    print(f'  {written} entries written, {skipped} skipped')
    print(f'  Non-zero entries: {sum(1 for v in entries if v != 0)}')


if __name__ == '__main__':
    main()
