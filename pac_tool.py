from __future__ import annotations

import struct, argparse, os
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
import pngquant_py


CLUT_SIZE = 1024
PAC_HEADER_SIZE = 0x44
SUB_HEADER_SIZE = 16
SUB_CLUT_OFF = 0x10
SUB_DATA_OFF = 0x410
SUB_HEADER_MAGIC = 0x03030001


def parse_header(data: bytes) -> dict:
    if data[0] != 0x00 or data[1] not in (0x01, 0x02, 0x03):
        raise ValueError('Not a PAC file')

    layout = data[1]
    sub_count_field = struct.unpack_from('<H', data, 0x02)[0]
    buf_w = struct.unpack_from('<H', data, 0x08)[0]
    buf_h = struct.unpack_from('<H', data, 0x0A)[0]
    disp_w = struct.unpack_from('<H', data, 0x0C)[0]
    disp_h = struct.unpack_from('<H', data, 0x0E)[0]
    flag = struct.unpack_from('<H', data, 0x10)[0]
    frame_info = struct.unpack_from('<H', data, 0x14)[0]
    right_edge = struct.unpack_from('<H', data, 0x18)[0]
    bottom_edge = struct.unpack_from('<H', data, 0x1C)[0]
    file_size = struct.unpack_from('<I', data, 0x20)[0]

    if file_size != len(data):
        print(f'WARN: header file_size={file_size} != actual {len(data)}')

    return dict(
        layout=layout,
        sub_count_field=sub_count_field,
        buf_w=buf_w,
        buf_h=buf_h,
        disp_w=disp_w,
        disp_h=disp_h,
        flag=flag,
        frame_info=frame_info,
        right_edge=right_edge,
        bottom_edge=bottom_edge,
        file_size=file_size,
    )


def deswizzle_16x8(src: bytes | bytearray, w: int, h: int) -> bytearray:
    tiles_x = (w + 15) // 16
    tiles_y = (h + 7) // 8
    dst = bytearray(w * h)
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            tile_idx = ty * tiles_x + tx
            base_src = tile_idx * 128
            for py in range(8):
                sy = ty * 8 + py
                if sy >= h:
                    break
                row = src[base_src + py * 16: base_src + py * 16 + 16]
                dst_start = sy * w + tx * 16
                dst[dst_start: dst_start + len(row)] = row
    return dst


def swizzle_16x8(src: bytes | bytearray, w: int, h: int) -> bytearray:
    tiles_x = (w + 15) // 16
    tiles_y = (h + 7) // 8
    total = tiles_x * tiles_y * 128
    dst = bytearray(total)
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            tile_idx = ty * tiles_x + tx
            base_dst = tile_idx * 128
            for py in range(8):
                sy = ty * 8 + py
                if sy >= h:
                    break
                sx_start = tx * 16
                row_len = min(16, w - sx_start)
                if row_len <= 0:
                    break
                src_row = src[sy * w + sx_start: sy * w + sx_start + row_len]
                dst[base_dst + py * 16: base_dst + py * 16 + row_len] = src_row
    return dst


def iter_subtextures(data: bytes) -> list[dict]:
    subs = []
    off = PAC_HEADER_SIZE
    while off + SUB_HEADER_SIZE <= len(data):
        magic = struct.unpack_from('<I', data, off)[0]
        if magic != SUB_HEADER_MAGIC:
            break
        sw = struct.unpack_from('<H', data, off + 4)[0]
        sh = struct.unpack_from('<H', data, off + 6)[0]
        clut_off_rel = struct.unpack_from('<I', data, off + 8)[0]
        data_off_rel = struct.unpack_from('<I', data, off + 12)[0]
        sub_clut = off + clut_off_rel
        sub_data = off + data_off_rel
        tiles_x = (sw + 15) // 16
        tiles_y = (sh + 7) // 8
        pixel_bytes = tiles_x * tiles_y * 128
        raw = data[sub_data: sub_data + pixel_bytes]
        if len(raw) < pixel_bytes:
            raw = raw + b'\x00' * (pixel_bytes - len(raw))
        subs.append(dict(sw=sw, sh=sh, clut_off=sub_clut, data_off=sub_data, raw=raw))
        off = sub_data + pixel_bytes
    return subs


def _collect_layout(subs: list[dict], layout: int, buf_w: int, buf_h: int) -> tuple[list[int], list[int]]:
    if layout in (2, 3) and len(subs) > 1:
        cols = []
        acc = 0
        for i, s in enumerate(subs):
            if s['sh'] == subs[0]['sh']:
                acc += s['sw']
                cols.append(s['sw'])
                if acc >= buf_w:
                    break
            else:
                break
        if not cols:
            cols = [subs[0]['sw']]
        rows = []
        for i in range(0, len(subs), len(cols)):
            rows.append(subs[i]['sh'])
        return cols, rows
    else:
        rows = [s['sh'] for s in subs]
        cols = [subs[0]['sw']] if subs else [buf_w]
        return cols, rows


def decode_image(data: bytes, meta: dict, crop: bool) -> Image.Image:
    w, h = meta['buf_w'], meta['buf_h']
    subs = iter_subtextures(data)
    if not subs:
        return Image.new('RGBA', (w, h))

    s0 = subs[0]
    clut = data[s0['clut_off']: s0['clut_off'] + CLUT_SIZE]

    cols, rows = _collect_layout(subs, meta['layout'], w, h)
    col_off = []
    acc = 0
    for cw in cols:
        col_off.append(acc)
        acc += cw
    row_off = []
    acc = 0
    for rh in rows:
        row_off.append(acc)
        acc += rh

    flat = bytearray(w * h)
    for i, s in enumerate(subs):
        ci = i % len(cols)
        ri = i // len(cols)
        chunk = deswizzle_16x8(s['raw'], s['sw'], s['sh'])
        for sy in range(s['sh']):
            src_start = sy * s['sw']
            dst_start = (row_off[ri] + sy) * w + col_off[ci]
            flat[dst_start: dst_start + s['sw']] = chunk[src_start: src_start + s['sw']]

    rgba = bytearray(w * h * 4)
    for i in range(w * h):
        idx = flat[i]
        po = i * 4
        rgba[po: po + 4] = clut[idx * 4: idx * 4 + 4]
    img = Image.frombytes('RGBA', (w, h), bytes(rgba))

    if crop:
        cx, cy, cw, ch = _crop_region(meta)
        img = img.crop((cx, cy, cx + cw, cy + ch))

    return img


def _build_clut(quantized: Image.Image) -> bytearray:
    pal = quantized.getpalette()
    pal_rgb = [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)] if pal else [(0, 0, 0)] * 256
    n_colors = len(pal_rgb)
    while len(pal_rgb) < 256:
        pal_rgb.append((0, 0, 0))

    alpha_vals = [255] * 256
    if 'transparency' in quantized.info:
        trans = quantized.info['transparency']
        if isinstance(trans, int):
            if trans < 256:
                alpha_vals[trans] = 0
        elif isinstance(trans, (bytes, bytearray, list)):
            for i, a in enumerate(trans):
                if i < 256:
                    alpha_vals[i] = a

    clut = bytearray(CLUT_SIZE)
    for i in range(256):
        r, g, b = pal_rgb[i]
        a = alpha_vals[i] if i < n_colors else 255
        clut[i * 4: i * 4 + 4] = (r, g, b, a)
    return clut


def _crop_region(meta: dict) -> tuple[int, int, int, int]:
    bw, bh = meta['buf_w'], meta['buf_h']
    cw = meta['right_edge'] + 1
    ch = meta['bottom_edge'] + 1
    if cw > bw:
        cw = meta['disp_w']
        if cw > bw:
            cw = bw
    if ch > bh:
        ch = meta['disp_h']
        if ch > bh:
            ch = bh
    cx = (bw - cw) // 2
    cy = (bh - ch + 1) // 2
    return cx, cy, cw, ch


def _place_into_buf(img: Image.Image, meta: dict) -> tuple[bytearray, int, int]:
    w, h = meta['buf_w'], meta['buf_h']
    if img.size == (w, h):
        return bytearray(img.tobytes()), w, h
    else:
        cx, cy, cw, ch = _crop_region(meta)
        if img.size == (cw, ch):
            canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            canvas.paste(img, (cx, cy))
            return bytearray(canvas.tobytes()), w, h
        else:
            raise ValueError(
                f'PNG size {img.size[0]}\u00d7{img.size[1]} must match either '
                f'buffer size {w}\u00d7{h} (decode with --no-crop) '
                f'or crop size {cw}\u00d7{ch} (decode without --no-crop)')


def encode_image(img: Image.Image, meta: dict, orig_data: bytes) -> bytearray:
    orig_subs = iter_subtextures(orig_data)
    if not orig_subs:
        raise ValueError('original pac has no sub-textures')

    cols, rows = _collect_layout(orig_subs, meta['layout'], meta['buf_w'], meta['buf_h'])
    sub_count = len(orig_subs)
    if sub_count != len(cols) * len(rows):
        raise ValueError(f'sub_count mismatch: {sub_count} vs {len(cols)}x{len(rows)}')

    buf_w = sum(cols)
    buf_h = sum(rows)

    pix_bytes, w, h = _place_into_buf(img, meta)

    buf = BytesIO()
    Image.frombytes('RGBA', (w, h), bytes(pix_bytes)).save(buf, format='PNG')
    quantized_bytes = pngquant_py.quantize(buf.getvalue(), speed=1)
    with Image.open(BytesIO(quantized_bytes)) as quantized:
        if quantized.mode != 'P':
            quantized = quantized.convert('P')
        clut = _build_clut(quantized)
        pix_data = quantized.tobytes()

    indices = bytearray(w * h)
    n = min(len(pix_data), w * h)
    indices[:n] = pix_data[:n]

    blocks = []
    y_off = 0
    for rh in rows:
        x_off = 0
        for cw in cols:
            chunk = bytearray(cw * rh)
            for sy in range(rh):
                src_start = (y_off + sy) * w + x_off
                dst_start = sy * cw
                chunk[dst_start: dst_start + cw] = indices[src_start: src_start + cw]
            blocks.append(swizzle_16x8(chunk, cw, rh))
            x_off += cw
        y_off += rh

    total_pixels = sum(len(b) for b in blocks)
    file_size = PAC_HEADER_SIZE + SUB_HEADER_SIZE + CLUT_SIZE + len(blocks[0])
    for b in blocks[1:]:
        file_size += SUB_HEADER_SIZE + CLUT_SIZE + len(b)

    FIRST_SUB_TOTAL = PAC_HEADER_SIZE + SUB_HEADER_SIZE + CLUT_SIZE
    header = bytearray(FIRST_SUB_TOTAL)
    header[:PAC_HEADER_SIZE] = orig_data[:PAC_HEADER_SIZE]
    struct.pack_into('<H', header, 0x08, buf_w)
    struct.pack_into('<H', header, 0x0A, buf_h)
    struct.pack_into('<I', header, 0x20, file_size)

    s0_cw = cols[0]
    s0_rh = rows[0]
    struct.pack_into('<I', header, PAC_HEADER_SIZE, SUB_HEADER_MAGIC)
    struct.pack_into('<H', header, PAC_HEADER_SIZE + 4, s0_cw)
    struct.pack_into('<H', header, PAC_HEADER_SIZE + 6, s0_rh)
    struct.pack_into('<I', header, PAC_HEADER_SIZE + 8, SUB_CLUT_OFF)
    struct.pack_into('<I', header, PAC_HEADER_SIZE + 12, SUB_DATA_OFF)
    header[PAC_HEADER_SIZE + SUB_CLUT_OFF: PAC_HEADER_SIZE + SUB_CLUT_OFF + CLUT_SIZE] = clut

    result = bytearray(header)
    result.extend(blocks[0])

    for bi in range(1, sub_count):
        ci = bi % len(cols)
        ri = bi // len(cols)
        cw = cols[ci]
        rh = rows[ri]
        sub_hdr = bytearray(SUB_HEADER_SIZE)
        struct.pack_into('<I', sub_hdr, 0, SUB_HEADER_MAGIC)
        struct.pack_into('<H', sub_hdr, 4, cw)
        struct.pack_into('<H', sub_hdr, 6, rh)
        struct.pack_into('<I', sub_hdr, 8, SUB_CLUT_OFF)
        struct.pack_into('<I', sub_hdr, 12, SUB_DATA_OFF)
        result.extend(sub_hdr)
        result.extend(clut)
        result.extend(blocks[bi])

    if meta['file_size'] != 0 and meta['file_size'] != len(result):
        print(f'WARN: rebuilt file_size={len(result)} != orig {meta["file_size"]}')

    return result


def decode(input_path: str, output_path: str, crop: bool) -> None:
    with open(input_path, 'rb') as f:
        data = f.read()
    meta = parse_header(data)
    img = decode_image(data, meta, crop)
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    img.save(output_path)
    print(f'[OK] {os.path.abspath(input_path)} -> {os.path.abspath(output_path)} ({img.width}\u00d7{img.height})')


def encode(input_path: str, output_path: str, orig_path: str) -> None:
    with open(orig_path, 'rb') as f:
        orig_data = f.read()
    meta = parse_header(orig_data)
    with Image.open(input_path) as img:
        img = img.convert('RGBA')
        result = encode_image(img, meta, orig_data)
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(result)
    print(f'[OK] {os.path.abspath(input_path)} -> {os.path.abspath(output_path)} ({meta["buf_w"]}\u00d7{meta["buf_h"]})')


def decode_path(input_path: str, output_path: str, crop: bool) -> None:
    abs_input = os.path.abspath(input_path)

    if os.path.isfile(abs_input):
        out = output_path
        name = os.path.splitext(os.path.basename(abs_input))[0]
        if not output_path.lower().endswith('.png'):
            out = os.path.join(output_path, name + '.png')
        decode(abs_input, out, crop)
        return

    if not os.path.isdir(abs_input):
        raise FileNotFoundError(abs_input)

    output_dir = output_path
    print(f'output dir: {output_dir}')

    tasks = []
    for root, _, files in os.walk(abs_input):
        for file in files:
            if file.lower().endswith('.pac'):
                src = os.path.join(root, file)
                name = os.path.splitext(file)[0]
                rel = os.path.relpath(root, abs_input)
                if rel == '.':
                    dst = os.path.join(output_dir, name + '.png')
                else:
                    dst = os.path.join(output_dir, rel, name + '.png')
                tasks.append((src, dst))

    _run_parallel(decode, [(src, dst, crop) for src, dst in tasks], lambda args: f'args[0]')


def _run_parallel(fn, tasks, key_fn) -> None:
    count = 0
    if len(tasks) > 1:
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            fut_to_key = {executor.submit(fn, *t): key_fn(t) for t in tasks}
            for fut in as_completed(fut_to_key):
                try:
                    fut.result()
                    count += 1
                except Exception as e:
                    print(f'ERROR: {fut_to_key[fut]}: {e}')
    else:
        for t in tasks:
            try:
                fn(*t)
                count += 1
            except Exception as e:
                print(f'ERROR: {key_fn(t)}: {e}')
    print(f'processed: {count} file(s)')


def _find_orig(orig_path: str, input_path: str) -> str | None:
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    for root, _, files in os.walk(orig_path):
        for f in files:
            if f.lower().endswith('.pac'):
                if os.path.splitext(f)[0] == base_name:
                    return os.path.join(root, f)
    return None


def encode_path(input_path: str, output_path: str, orig_path: str) -> None:
    abs_input = os.path.abspath(input_path)

    if os.path.isfile(abs_input):
        out = output_path
        name = os.path.splitext(os.path.basename(abs_input))[0]
        if not output_path.lower().endswith('.pac'):
            out = os.path.join(output_path, name + '.pac')
        orig = orig_path if os.path.isfile(orig_path) else _find_orig(orig_path, abs_input)
        if not orig:
            print(f'ERROR: orig not found for {abs_input}')
            return
        encode(abs_input, out, orig)
        return

    if not os.path.isdir(abs_input):
        raise FileNotFoundError(abs_input)

    output_dir = output_path
    print(f'output dir: {output_dir}')

    tasks = []
    for root, _, files in os.walk(abs_input):
        for file in files:
            if file.lower().endswith('.png'):
                src = os.path.join(root, file)
                name = os.path.splitext(file)[0]
                rel = os.path.relpath(root, abs_input)
                if rel == '.':
                    dst = os.path.join(output_dir, name + '.pac')
                else:
                    dst = os.path.join(output_dir, rel, name + '.pac')
                orig = orig_path if os.path.isfile(orig_path) else _find_orig(orig_path, src)
                if not orig:
                    print(f'SKIP: orig not found for {src}')
                    continue
                tasks.append((src, dst, orig))

    _run_parallel(encode, tasks, lambda args: args[0])


def main() -> None:
    parser = argparse.ArgumentParser(description='PAC texture tool')
    sub = parser.add_subparsers(dest='mode', required=True)

    p_d = sub.add_parser('decode', help='PAC to PNG')
    p_d.add_argument('-i', '--input', required=True, help='Input .pac file or directory')
    p_d.add_argument('-o', '--output', required=True, help='Output .png file or directory')
    p_d.add_argument('--no-crop', action='store_true', help='Keep full buffer size (default: apply crop)')

    p_e = sub.add_parser('encode', help='PNG to PAC')
    p_e.add_argument('-i', '--input', required=True, help='Input .png file or directory')
    p_e.add_argument('-o', '--output', required=True, help='Output .pac file or directory')
    p_e.add_argument('--orig', required=True, metavar='FILE_OR_DIR', help='Original PAC (file or dir) to reuse header')

    args = parser.parse_args()

    if args.mode == 'decode':
        decode_path(args.input, args.output, not args.no_crop)
    elif args.mode == 'encode':
        encode_path(args.input, args.output, args.orig)


if __name__ == '__main__':
    main()
