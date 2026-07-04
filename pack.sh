#!/bin/sh

set -e

ROOT_DIR=$(pwd)

export PATH="$ROOT_DIR:$ROOT_DIR/bin:$PATH"

mkdir -p build build/DATA1/font

if [ ! -d raw/iso ]; then
    echo "Extracting ISO..."
    7z x "raw/One Side Summer + (Japan) (v1.01).iso" -oraw/iso
    # cp -r raw/iso build/iso
fi

if [ ! -d raw/DATA1 ]; then
    echo "Extracting DATA1.CPK..."
    CriPakTools.exe extract -i "raw/iso/PSP_GAME/USRDIR/data/DATA1.CPK" -o raw/DATA1
fi

python gen_glyph_table.py -i script.csv,eboot.csv -f raw/DATA1/font/font_16_a.txt -o build/glyph_table.csv

python patch_tool.py -b assets/EBOOT.BIN -c eboot.csv -e cp932 -g build/glyph_table.csv -o build/iso/PSP_GAME/SYSDIR/EBOOT.BIN

python gen_font_index.py -i build/glyph_table.csv -o build/DATA1/font/font_16_a.txt

python script_tool.py write -i raw/DATA1/script -o build/DATA1/script -c script.csv -r build/glyph_table.csv

python gen_glyph_images.py -i build/glyph_table.csv -f C:/Windows/Fonts/msyh.ttc -o build/font_16_a0.png
pngquant.exe --ext .png --force --verbose 16 build/font_16_a0.png
python ext_tool.py encode -i build/font_16_a0.png -o build/DATA1/font/font_16_a0.ext

python gen_glyph_images.py -i build/glyph_table.csv -f C:/Windows/Fonts/msyh.ttc -o build/font_16_a1.png
pngquant.exe --ext .png --force --verbose 16 build/font_16_a1.png
python ext_tool.py encode -i build/font_16_a1.png -o build/DATA1/font/font_16_a1.ext

python gen_glyph_images.py -i build/glyph_table.csv -f C:/Windows/Fonts/msyh.ttc -o build/font_16_a2.png
pngquant.exe --ext .png --force --verbose 16 build/font_16_a2.png
python ext_tool.py encode -i build/font_16_a2.png -o build/DATA1/font/font_16_a2.ext

CriPakTools.exe replace -i "raw/iso/PSP_GAME/USRDIR/data/DATA1.CPK" -d build/DATA1 -o build/iso/PSP_GAME/USRDIR/data/DATA1.CPK

echo "done"
