import os, csv, re, glob, argparse, sys
from collections import OrderedDict

NAME_PATTERN  = re.compile(r'<11\s(.+?)>')
TEXT_PATTERN  = re.compile(r'<12\s(.+?)>')
CHAPTER_PATTERN  = re.compile(r'<95\s(.+?)>')
SELECT_PATTERN   = re.compile(r'<78\s*,([^,>]*)')
TAG_PATTERN   = re.compile(r'<(\d+)')
NONASCII      = re.compile(r'[^\x00-\x7F]')

HANDLED_TAGS  = {'11', '12', '78', '95'}

SKIP_TAGS = {
    '10',              # 暂停
    '13', '14', '15',  # 文字颜色 / 样式
    '16', '17', '18',  # 设置
    '30', '31',        # 图层 / 刷新
    '32', '33', '34',  # 过渡 / 特效
    '35',              # 特效
    '36', '37', '38',  # 设置
    '50', '51',        # 音效 / 延迟
    '52', '53', '56',  # 条件流控制
    '54', '55',        # 表情 / 图层控制
    '57',              # 语音（名字由 name_map 全局替换）
    '70', '72', '73',  # 流程控制 / 条件判断
    '74', '75', '79',  # 流程控制 / else / end
    '76', '77',        # 脚本包含 / 标签 / 跳转
    '90', '91',        # 等待 / 操作
    '92', '93', '94',  # 特效 / 设置
    '96',              # 设置
}

CSV_FIELDS    = ['source', 'line', 'type', 'context', 'text', 'translation']
FILE_ENCODING = 'shift_jis'


class ScriptTool:
    def get_files(self, directory: str) -> list:
        return sorted(glob.glob(os.path.join(directory, '*.txt')))

    def _die(self, file_name: str, lineno: int, tag: str, line: str) -> None:
        sys.stderr.write(
            f"\nUNHANDLED TAG <{tag}> with non-ASCII content:\n"
            f"  File: {file_name}:{lineno}\n"
            f"  Line: {line.rstrip()}\n"
            f"\n  → add <{tag}> to HANDLED_TAGS (extract) or SKIP_TAGS (ignore)\n"
        )
        sys.exit(1)

    def extract(self, input_dir: str, output_csv: str) -> None:
        files = self.get_files(input_dir)
        if not files:
            print(f"ERROR: no .txt files found in '{input_dir}'")
            return

        print(f"Extracting from '{input_dir}' ({len(files)} files)...")
        rows = []
        unique_names = set()

        for file_path in files:
            file_name = os.path.basename(file_path)
            try:
                with open(file_path, 'r', encoding=FILE_ENCODING) as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"  SKIP {file_name}: {e}")
                continue

            for i, line in enumerate(lines):
                lineno = i + 1

                # <11 名字>
                nm = NAME_PATTERN.search(line)
                if nm:
                    unique_names.add(nm.group(1).strip())
                    continue

                # <12 台词>
                tx = TEXT_PATTERN.search(line)
                if tx:
                    raw = tx.group(1).strip()
                    if raw:
                        ctx = ""
                        if i > 0:
                            prev_nm = NAME_PATTERN.search(lines[i - 1])
                            if prev_nm:
                                ctx = prev_nm.group(1).strip()
                        rows.append(OrderedDict([
                            ('source', file_name),
                            ('line', lineno),
                            ('type', 'TEXT'),
                            ('context', ctx),
                            ('text', raw),
                            ('translation', ''),
                        ]))
                    continue

                # <95 章节>
                ch = CHAPTER_PATTERN.search(line)
                if ch:
                    raw = ch.group(1).strip()
                    if raw:
                        rows.append(OrderedDict([
                            ('source', file_name),
                            ('line', lineno),
                            ('type', 'CHAPTER'),
                            ('context', ''),
                            ('text', raw),
                            ('translation', ''),
                        ]))
                    continue

                # <78 选择项>
                sel = SELECT_PATTERN.search(line)
                if sel:
                    raw = sel.group(1).strip()
                    if raw:
                        rows.append(OrderedDict([
                            ('source', file_name),
                            ('line', lineno),
                            ('type', 'SELECT'),
                            ('context', ''),
                            ('text', raw),
                            ('translation', ''),
                        ]))
                    continue

                if not NONASCII.search(line):
                    continue
                tag_m = TAG_PATTERN.search(line)
                if not tag_m:
                    continue
                tag = tag_m.group(1)
                if tag in SKIP_TAGS:
                    continue
                self._die(file_name, lineno, tag, line)

        name_rows = []
        for n in sorted(unique_names):
            name_rows.append(OrderedDict([
                ('source', ''), ('line', ''), ('type', 'NAME'),
                ('context', ''), ('text', n), ('translation', ''),
            ]))

        final = name_rows + rows
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(final)

        print(f"Saved: {output_csv}")


    def write(self, input_dir: str, output_dir: str, csv_path: str,
              replace_map_path: str | None = None) -> None:
        if not os.path.exists(csv_path):
            print(f"ERROR: CSV not found '{csv_path}'")
            return

        print(f"Loading translations from '{csv_path}'...")

        replace_trans = None
        if replace_map_path:
            if not os.path.isfile(replace_map_path):
                print(f"ERROR: glyph table not found '{replace_map_path}'")
                return
            raw: dict[str, str] = {}
            with open(replace_map_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    repl = (row.get('replace') or '').strip()
                    carrier = (row.get('char') or '').strip()
                    if repl and carrier:
                        raw[repl] = carrier
            replace_trans = str.maketrans(raw)
            print(f"  Loaded replacement map: {len(raw)} entries")

        name_map = {}
        text_map = {}   # (source, line) → translation
        chap_map = {}
        sel_map  = {}

        with open(csv_path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                tran = (row.get('translation') or '').strip()
                if not tran:
                    continue
                typ = row.get('type')
                src = row.get('source', '')
                ln  = int(row.get('line') or 0)
                if typ == 'NAME':
                    name_map[row['text']] = tran
                elif typ == 'TEXT':
                    text_map[(src, ln)] = tran
                elif typ == 'CHAPTER':
                    chap_map[(src, ln)] = tran
                elif typ == 'SELECT':
                    sel_map[(src, ln)] = tran

        os.makedirs(output_dir, exist_ok=True)

        files = self.get_files(input_dir)
        print(f"Processing {len(files)} files...")

        for file_path in files:
            file_name = os.path.basename(file_path)
            dest_path = os.path.join(output_dir, file_name)

            try:
                with open(file_path, 'r', encoding=FILE_ENCODING) as f:
                    lines = f.readlines()
            except Exception as e:
                print(f"  SKIP {file_name}: {e}")
                continue

            new_lines = list(lines)

            # <11 名字>
            for i, line in enumerate(new_lines):
                for orig_name, tran_name in name_map.items():
                    if orig_name not in line:
                        continue
                    line = re.sub(rf'(<11\s){re.escape(orig_name)}(>)',
                                  rf'\g<1>{tran_name}\g<2>', line)
                new_lines[i] = line

            # <12 台词>, <95 章节>, <78 选择项>
            for i, line in enumerate(new_lines):
                lineno = i + 1

                tx = TEXT_PATTERN.search(line)
                if tx and (file_name, lineno) in text_map:
                    new_lines[i] = TEXT_PATTERN.sub(
                        f'<12 {text_map[(file_name, lineno)]}>', line)

                ch = CHAPTER_PATTERN.search(line)
                if ch and (file_name, lineno) in chap_map:
                    new_lines[i] = CHAPTER_PATTERN.sub(
                        f'<95 {chap_map[(file_name, lineno)]}>', line)

                sl = SELECT_PATTERN.search(line)
                if sl and (file_name, lineno) in sel_map:
                    new_lines[i] = re.sub(
                        r'(<78\s*,)([^,>]*)([^>]*>)',
                        rf'\g<1>{sel_map[(file_name, lineno)]}\g<3>', line)

            if replace_trans:
                for i, line in enumerate(new_lines):
                    new_lines[i] = line.translate(replace_trans)  # type: ignore

            with open(dest_path, 'w', encoding=FILE_ENCODING) as f:
                f.writelines(new_lines)

        print(f"Done: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description='One Side Summer Script Tool')
    sub = parser.add_subparsers(dest='mode', required=True)

    p_ext = sub.add_parser('extract', help='extract text to CSV')
    p_ext.add_argument('-i', '--input',  required=True)
    p_ext.add_argument('-o', '--output', required=True)

    p_wr = sub.add_parser('write', help='write translations to scripts')
    p_wr.add_argument('-i', '--input',  required=True, help='original scripts dir')
    p_wr.add_argument('-o', '--output', required=True, help='output scripts dir')
    p_wr.add_argument('-c', '--csv',    required=True, help='translation CSV')
    p_wr.add_argument('-g', '--glyph-table', help='glyph_table.csv')

    args = parser.parse_args()
    tool = ScriptTool()

    if args.mode == 'extract':
        tool.extract(args.input, args.output)
    else:
        tool.write(args.input, args.output, args.csv, args.glyph_table)


if __name__ == '__main__':
    main()
