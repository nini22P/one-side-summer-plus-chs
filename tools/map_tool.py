#!/usr/bin/env python3
"""Generate patch/map.tsv (PSP .txt line <-> PC .ks line) and fill empty PC translations at build time."""
from __future__ import annotations

import argparse
import codecs
import csv
from pathlib import Path
import re
import struct
import zlib

XP3_MAGIC = b"XP3\r\n \n\x1a\x8b\x67\x01"
SCENARIO_PREFIX = "scenario/"

NAME_RE = re.compile(r"^【([^】]+)】(.*)$")
SELADD_RE = re.compile(r'^(?:\[|@)seladd\b[^\]]*?\btext=(?:"([^"]*)"|\'([^\']*)\'|([^\s\]]+))')
VOICE_CMD_RE = re.compile(r'^[^\s【「（]+?\s*voice="[^"]*"')
RUBY_RE = re.compile(r"\[([^\[\]\s'=]+)'([^\[\]]+)\]")
NEWLINE_TAG_RE = re.compile(r"\[r\]", re.IGNORECASE)
TAG_RE = re.compile(r"\[[^\]]*\]")
PROTECTED_TOKEN_RE = re.compile(r"(\[[^|\]]+\|[^\]]+\]|#n)")
QUOTE_TRANS = str.maketrans({"“": "『", "”": "』", "‘": "『", "’": "』"})
NOTE_RE = re.compile(r"[(（]\s*(翻译君注|译者注|译注|[ｓs]|注)\s*[：:]([^)）]*)[)）]?(」)?$")
STRAIGHT_QUOTE_RE = re.compile(r'"([^"]*)"')

RUBY_OPEN = "\x01"
RUBY_CLOSE = "\x02"


def normalize_text(text: str) -> str:
    """Normalize half-width ASCII alnum (outside [ruby|base] and #n), quotes, and translator notes."""
    def _conv(s: str) -> str:
        s = s.translate(QUOTE_TRANS)
        return "".join(
            chr(ord(ch) + 0xFEE0)
            if (0x30 <= ord(ch) <= 0x39 or 0x41 <= ord(ch) <= 0x5A or 0x61 <= ord(ch) <= 0x7A)
            else ch
            for ch in s
        )

    parts = PROTECTED_TOKEN_RE.split(text)
    normalized = "".join(p if PROTECTED_TOKEN_RE.fullmatch(p) else _conv(p) for p in parts)
    return NOTE_RE.sub(
        lambda m: f"{m.group(3) or ''}（{m.group(1)}：{STRAIGHT_QUOTE_RE.sub(r'『\1』', m.group(2))}）",
        normalized,
    )


def decode_text_auto(raw: bytes, label: str = "<memory>") -> str:
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    for enc in ("utf-8", "cp932", "gb18030", "utf-16-le", "utf-16-be"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("auto", raw, 0, len(raw), f"无法识别文件编码: {label}")


def locate_xp3_bytes(source: Path) -> bytes:
    data = source.read_bytes()
    if data.startswith(XP3_MAGIC):
        return data
    offset = 0
    while True:
        idx = data.find(XP3_MAGIC, offset)
        if idx == -1:
            break
        if idx + 19 <= len(data):
            rel_idx_off = struct.unpack_from("<Q", data, idx + 11)[0]
            abs_idx_off = idx + rel_idx_off
            if idx + 19 <= abs_idx_off < len(data) and data[abs_idx_off] in (0, 1):
                return data[idx:]
        offset = idx + 1
    raise ValueError(f"未在文件中找到有效的 XP3 封包: {source}")


def load_cn_scripts(cn_source: Path) -> dict[str, tuple[str, str]]:
    if cn_source.is_dir():
        scripts: dict[str, tuple[str, str]] = {}
        for p in sorted(cn_source.rglob("*.ks")):
            rel_name = p.relative_to(cn_source).as_posix()
            scripts[rel_name.lower()] = (rel_name, decode_text_auto(p.read_bytes(), str(p)))
        if not scripts:
            raise ValueError(f"目录中未找到 .ks 脚本文件: {cn_source}")
        return scripts

    if not cn_source.is_file():
        raise FileNotFoundError(f"汉化输入路径不存在: {cn_source}")

    xp3_bytes = locate_xp3_bytes(cn_source)
    idx_off = struct.unpack_from("<Q", xp3_bytes, 11)[0]
    flag = xp3_bytes[idx_off]
    if flag == 1:
        comp_size, _ = struct.unpack_from("<QQ", xp3_bytes, idx_off + 1)
        idx_data = zlib.decompress(xp3_bytes[idx_off + 17 : idx_off + 17 + comp_size])
    elif flag == 0:
        orig_size = struct.unpack_from("<Q", xp3_bytes, idx_off + 1)[0]
        idx_data = xp3_bytes[idx_off + 9 : idx_off + 9 + orig_size]
    else:
        raise ValueError(f"不支持的 XP3 索引标志: {flag}")

    scripts = {}
    pos = 0
    while pos < len(idx_data):
        tag = idx_data[pos : pos + 4]
        chunk_size = struct.unpack_from("<Q", idx_data, pos + 4)[0]
        if tag == b"File":
            sub = idx_data[pos + 12 : pos + 12 + chunk_size]
            sp = 0
            fname: str | None = None
            segs: list[tuple[int, int, int]] = []
            while sp < len(sub):
                stag = sub[sp : sp + 4]
                ssize = struct.unpack_from("<Q", sub, sp + 4)[0]
                if stag == b"info":
                    _, _, _, namelen = struct.unpack_from("<IQQH", sub, sp + 12)
                    fname = sub[sp + 34 : sp + 34 + namelen * 2].decode("utf-16le", errors="replace")
                elif stag == b"segm":
                    for off_s in range(0, ssize, 28):
                        is_comp, seg_off, _, seg_asize = struct.unpack_from("<IQQQ", sub, sp + 12 + off_s)
                        segs.append((is_comp, seg_off, seg_asize))
                sp += 12 + ssize

            if fname:
                norm = fname.replace("\\", "/")
                if norm.lower().startswith(SCENARIO_PREFIX) and norm.lower().endswith(".ks"):
                    rel_name = norm[len(SCENARIO_PREFIX) :]
                    buf = bytearray()
                    for is_comp, seg_off, seg_asize in segs:
                        chunk = xp3_bytes[seg_off : seg_off + seg_asize]
                        if is_comp:
                            chunk = zlib.decompress(chunk)
                        buf.extend(chunk)
                    scripts[rel_name.lower()] = (rel_name, decode_text_auto(bytes(buf), norm))
        pos += 12 + chunk_size

    if not scripts:
        raise ValueError("XP3 中未找到 scenario/*.ks 脚本文件")
    return scripts


def has_unclosed_quote(text: str) -> bool:
    return text.count("「") > text.count("」") or text.count("『") > text.count("』")


def clean_line_text(line: str) -> str:
    cleaned = RUBY_RE.sub(rf"{RUBY_OPEN}\2|\1{RUBY_CLOSE}", line)
    cleaned = NEWLINE_TAG_RE.sub("\n", cleaned)
    cleaned = TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace(RUBY_OPEN, "[").replace(RUBY_CLOSE, "]")
    return cleaned.strip()


def tokenize_ks_with_lines(content: str) -> list[tuple[int, str]]:
    """Extract (1-based start line in .ks, cleaned text) pairs."""
    tokens: list[tuple[int, str]] = []
    in_script = False
    can_continue_quote = False

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        s = raw_line.strip()
        if not s:
            can_continue_quote = False
            continue
        if s.startswith(";"):
            continue
        lower = s.lower()
        if lower.startswith(("[iscript]", "@iscript")):
            in_script = True
            can_continue_quote = False
            continue
        if lower.startswith(("[endscript]", "@endscript")):
            in_script = False
            can_continue_quote = False
            continue
        if in_script:
            continue
        if s.startswith("*"):
            can_continue_quote = False
            continue

        m_sel = SELADD_RE.match(s)
        if m_sel:
            can_continue_quote = False
            txt = m_sel.group(1) or m_sel.group(2) or m_sel.group(3) or ""
            tokens.append((lineno, txt))
            continue

        if s.startswith("@") or VOICE_CMD_RE.match(s):
            can_continue_quote = False
            continue

        cleaned = clean_line_text(s)
        if not cleaned:
            can_continue_quote = False
            continue

        m_name = NAME_RE.match(cleaned)
        if m_name:
            txt = m_name.group(2).strip()
            tokens.append((lineno, txt))
            can_continue_quote = has_unclosed_quote(txt)
        elif can_continue_quote and tokens:
            prev_ln, prev_txt = tokens[-1]
            merged = f"{prev_txt}\n{cleaned}"
            tokens[-1] = (prev_ln, merged)
            can_continue_quote = has_unclosed_quote(merged)
        else:
            tokens.append((lineno, cleaned))
            can_continue_quote = False

    return tokens


def weighted_lis_align(psp_texts: list[str], ks_texts: list[str]) -> list[tuple[int, int]]:
    """Find maximum-weight strictly monotonic alignment between PSP and KS lines."""
    ks_pos: dict[str, list[int]] = {}
    for j, t in enumerate(ks_texts):
        ks_pos.setdefault(t, []).append(j)

    n_ks = len(ks_texts)
    bit: list[tuple[int, int]] = [(0, -1)] * (n_ks + 1)
    nodes: list[tuple[int, int, int]] = []

    def query(idx: int) -> tuple[int, int]:
        best = (0, -1)
        while idx > 0:
            if bit[idx][0] > best[0]:
                best = bit[idx]
            idx -= idx & -idx
        return best

    def update(idx: int, val: tuple[int, int]) -> None:
        while idx <= n_ks:
            if val[0] > bit[idx][0]:
                bit[idx] = val
            idx += idx & -idx

    for i, t in enumerate(psp_texts):
        js = ks_pos.get(t)
        if not js:
            continue
        w = 100 + len(t)
        updates: list[tuple[int, tuple[int, int]]] = []
        for j in reversed(js):
            prev_w, prev_id = query(j)
            node_id = len(nodes)
            nodes.append((i, j, prev_id))
            updates.append((j + 1, (prev_w + w, node_id)))
        for pos, val in updates:
            update(pos, val)

    _, cur = query(n_ks)
    res: list[tuple[int, int]] = []
    while cur != -1:
        i, j, prev_id = nodes[cur]
        res.append((i, j))
        cur = prev_id
    res.reverse()
    return res


def cmd_make_map(csv_path: Path, exe_path: Path, map_path: Path, strip_out: Path | None) -> None:
    scripts = load_cn_scripts(exe_path)
    ks_by_stem: dict[str, tuple[str, list[tuple[int, str]]]] = {}
    for _, (rel, content) in scripts.items():
        ks_by_stem[Path(rel).stem.lower()] = (rel, tokenize_ks_with_lines(content))

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    by_stem: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for idx, row in enumerate(rows):
        if row.get("type") in ("TEXT", "SELECT") and (row.get("translation") or "").strip():
            st = Path(row.get("source", "")).stem.lower()
            by_stem.setdefault(st, []).append((idx, row))

    mappings: list[tuple[int, str, str, str, int]] = []
    for st, items in by_stem.items():
        if st not in ks_by_stem:
            continue
        rel, toks = ks_by_stem[st]
        psp_texts = [row["translation"] for _, row in items]
        ks_texts = [normalize_text(txt) for _, txt in toks]
        for a_idx, b_idx in weighted_lis_align(psp_texts, ks_texts):
            row_idx, row = items[a_idx]
            ks_line = toks[b_idx][0]
            mappings.append((row_idx, row["source"], row["line"], rel, ks_line))

    mappings.sort(key=lambda x: x[0])
    map_path.parent.mkdir(parents=True, exist_ok=True)
    with map_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["psp_file", "psp_line", "ks_file", "ks_line"])
        for _, psp_file, psp_line, ks_file, ks_line in mappings:
            w.writerow([psp_file, psp_line, ks_file, ks_line])

    print(f"Generated {len(mappings)} mappings -> {map_path}")

    if strip_out is not None:
        mapped_indices = {m[0] for m in mappings}
        for idx in mapped_indices:
            rows[idx]["translation"] = ""
        strip_out.parent.mkdir(parents=True, exist_ok=True)
        with strip_out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote stripped script CSV (cleared {len(mapped_indices)} PC rows) -> {strip_out}")


def cmd_fill(csv_path: Path, map_path: Path, exe_path: Path, out_csv: Path) -> None:
    scripts = load_cn_scripts(exe_path)
    ks_lookup: dict[tuple[str, int], str] = {}
    for _, (rel, content) in scripts.items():
        for ks_line, txt in tokenize_ks_with_lines(content):
            ks_lookup[(rel, ks_line)] = normalize_text(txt)

    psp_to_ks: dict[tuple[str, str], tuple[str, int]] = {}
    with map_path.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            psp_to_ks[(r["psp_file"], r["psp_line"])] = (r["ks_file"], int(r["ks_line"]))

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    filled = 0
    for row in rows:
        if (row.get("translation") or "").strip():
            continue
        key = (row.get("source", ""), row.get("line", ""))
        target = psp_to_ks.get(key)
        if target is None:
            continue
        txt = ks_lookup.get(target)
        if txt is None:
            raise KeyError(f"PC 汉化安装程序中未找到映射位置 {target} (对应 PSP {key})")
        row["translation"] = txt
        filled += 1

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"Filled {filled} PC translations into {out_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PSP <-> PC line map & fill tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_make = sub.add_parser("make-map", help="Generate patch/map.tsv from aligned script.csv + PC exe")
    p_make.add_argument("-c", "--csv", required=True, type=Path)
    p_make.add_argument("-e", "--exe", required=True, type=Path)
    p_make.add_argument("-m", "--map", required=True, type=Path)
    p_make.add_argument("--strip-out", type=Path, default=None, help="Optional output CSV with mapped PC rows cleared")

    p_fill = sub.add_parser("fill", help="Fill empty PC translations in script.csv using map.tsv + PC exe")
    p_fill.add_argument("-c", "--csv", required=True, type=Path)
    p_fill.add_argument("-m", "--map", required=True, type=Path)
    p_fill.add_argument("-e", "--exe", required=True, type=Path)
    p_fill.add_argument("-o", "--output", required=True, type=Path)

    args = parser.parse_args()
    if args.cmd == "make-map":
        cmd_make_map(args.csv, args.exe, args.map, args.strip_out)
    else:
        cmd_fill(args.csv, args.map, args.exe, args.output)


if __name__ == "__main__":
    main()
