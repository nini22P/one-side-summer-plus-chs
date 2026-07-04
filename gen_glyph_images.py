import csv, argparse, math
from PIL import Image, ImageDraw, ImageFont


def load_rows(csv_path: str) -> list[dict]:
    rows: list[dict] = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            idx_str = (row.get('index') or '').strip()
            if not idx_str:
                continue
            ch = (row.get('replace') or '').strip() or (row.get('char') or '').strip()
            rows.append({'slot': int(idx_str), 'char': ch})
    rows.sort(key=lambda r: r['slot'])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate glyph texture image')
    parser.add_argument('-i', required=True, help='glyph_table.csv')
    parser.add_argument('-f', required=True, help='font file (TTF/OTF)')
    parser.add_argument('-o', default='glyphs.png', help='output PNG')
    parser.add_argument('--font-size', type=int, default=14,
                        help='font size in pixels (default 14)')
    args = parser.parse_args()

    rows = load_rows(args.i)
    if not rows:
        print('ERROR: no rows in glyph table')
        return

    font = ImageFont.truetype(args.f, args.font_size)
    tile_w, tile_h = 16, 16
    cols = 2
    total = len(rows)
    img_w = tile_w * cols
    img_h = tile_h * math.ceil(total / cols)

    img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for i, row in enumerate(rows):
        ch = row['char']
        if not ch:
            continue
        x = (i % cols) * tile_w
        y = (i // cols) * tile_h

        draw.text((x + tile_w / 2, y + tile_h / 2), ch, font=font, fill='white', anchor='mm')

    img.save(args.o)
    print(f'[OK] {args.o} ({total} glyphs, {img_w}×{img_h})')


if __name__ == '__main__':
    main()
