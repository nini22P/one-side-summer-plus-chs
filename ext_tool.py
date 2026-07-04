import struct, argparse, os
from PIL import Image

HEADER_SIZE = 0x150
TILE_H = 16


def decode(ext_path: str, png_path: str) -> None:
    with open(ext_path, 'rb') as f:
        data = f.read()

    width = struct.unpack('<H', data[4:6])[0] or 32
    pixel_data = data[HEADER_SIZE:]

    # Palette: 16 × RGBA
    palette: list[tuple[int, int, int, int]] = []
    for i in range(16):
        off = 0x10 + i * 4
        palette.append((
            data[off], data[off + 1], data[off + 2],
            data[off + 3] if off + 3 < len(data) else 255
        ))

    img_height = len(pixel_data) * 2 // width
    img = Image.new('RGBA', (width, img_height))
    pix = img.load()

    row_blocks = max(width // 32, 1)

    for y in range(img_height):
        for x in range(width):
            bx = x // 32
            by = y // 8
            addr = ((by * row_blocks) + bx) * 128 + (y % 8) * 16 + (x % 32) // 2
            if addr >= len(pixel_data):
                continue
            byte_val = pixel_data[addr]
            idx = (byte_val >> 4) & 0x0F if x % 2 else byte_val & 0x0F
            pix[x, y] = palette[idx] if idx < len(palette) else (0, 0, 0, 0)

    pal_img = Image.new('P', (width, img_height))
    flat_rgb = [c for rgba in palette for c in rgba[:3]]
    pal_img.putpalette(flat_rgb[:48])  # 16 × 3
    alpha_vals = bytes(a for _, _, _, a in palette)
    pal_img.info['transparency'] = alpha_vals

    for y in range(img_height):
        for x in range(width):
            rgba = pix[x, y]
            for idx, entry in enumerate(palette):
                if rgba == entry:
                    pal_img.putpixel((x, y), idx)
                    break

    pal_img.save(png_path)

    tiles = len(pixel_data) // 128
    print(f'[OK] {png_path} ({width}×{img_height}, {tiles} tiles, {len(palette)}-color palette)')


def encode(png_path: str, ext_path: str) -> None:
    img = Image.open(png_path)

    if img.mode == 'P':
        palette_rgb = img.getpalette()
        if not palette_rgb:
            raise ValueError('PNG has no palette')
        colors = list(zip(palette_rgb[0::3], palette_rgb[1::3], palette_rgb[2::3]))
        if 'transparency' in img.info:
            trans = img.info['transparency']
            if isinstance(trans, int):
                alpha = [255] * len(colors)
                alpha[trans] = 0
            else:
                alpha = list(trans) if len(trans) == len(colors) else [255] * len(colors)
        else:
            alpha = [255] * len(colors)
        palette: list[tuple[int, int, int, int]] = [
            (r, g, b, a) for (r, g, b), a in zip(colors, alpha)
        ]
    else:
        img = img.convert('RGBA')
        pixels = list(img.getdata())
        seen: dict[tuple[int, int, int, int], int] = {}
        for px in pixels:
            if px not in seen:
                seen[px] = len(seen)
        if len(seen) > 16:
            raise ValueError(f'{len(seen)} unique colors, max 16. Run pngquant first.')
        palette = list(seen.keys())
    while len(palette) < 16:
        palette.append((0, 0, 0, 0))

    width, img_height = img.size
    row_blocks = max(width // 32, 1)

    px_to_idx: dict[tuple[int, int, int, int], int] = {}
    for i, entry in enumerate(palette):
        px_to_idx[entry] = i

    if img.mode == 'P':
        get_idx = lambda x, y: img.getpixel((x, y))
    else:
        get_idx = lambda x, y: px_to_idx.get(img.getpixel((x, y)), 0)

    pixel_data_size = (width // 2) * img_height
    pixel_data = bytearray(pixel_data_size)

    for y in range(img_height):
        for x in range(0, width, 2):
            left = get_idx(x, y)
            right = get_idx(x + 1, y) if x + 1 < width else 0
            byte_val = (right << 4) | left

            bx = x // 32
            by = y // 8
            addr = ((by * row_blocks) + bx) * 128 + (y % 8) * 16 + (x % 32) // 2
            if addr < len(pixel_data):
                pixel_data[addr] = byte_val

    tile_rows = img_height // TILE_H
    virtual_height = (tile_rows + 1) * TILE_H

    header = bytearray(HEADER_SIZE)
    struct.pack_into('<H', header, 0x00, 1)
    struct.pack_into('<H', header, 0x02, 770)
    struct.pack_into('<H', header, 0x04, width)
    struct.pack_into('<H', header, 0x06, virtual_height)
    struct.pack_into('<H', header, 0x08, TILE_H)
    struct.pack_into('<H', header, 0x0C, 80)

    for i, (r, g, b, a) in enumerate(palette):
        off = 0x10 + i * 4
        header[off] = r
        header[off + 1] = g
        header[off + 2] = b
        header[off + 3] = a

    with open(ext_path, 'wb') as f:
        f.write(header)
        f.write(pixel_data)

    tiles = len(pixel_data) // 128
    print(f'[OK] {ext_path} ({width}×{img_height}, {tiles} tiles, {len(palette)}-color palette)')


def main() -> None:
    parser = argparse.ArgumentParser(description='Ext font texture tool')
    sub = parser.add_subparsers(dest='mode', required=True)

    p_d = sub.add_parser('decode', help='ext → PNG')
    p_d.add_argument('-i', required=True)
    p_d.add_argument('-o', required=True)

    p_e = sub.add_parser('encode', help='PNG → ext')
    p_e.add_argument('-i', required=True)
    p_e.add_argument('-o', required=True)

    args = parser.parse_args()

    if args.mode == 'decode':
        decode(args.i, args.o)
    else:
        encode(args.i, args.o)


if __name__ == '__main__':
    main()
