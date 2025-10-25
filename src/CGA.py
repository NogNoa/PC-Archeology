import argparse
import math
import sys

from PIL import Image

BG = (0, 0, 0xAA)
GLOBAL_INTENSITY = 0x55
FIELD_SZ = 0x2000
LINE_PIX = 320
LINE_SZ = LINE_PIX  // 4
MESG_WIDTH = 0x80
ROW_LETTERS = 0x10
LETTER_WIDTH = 8
LETTER_HIGHT = 8
ROW_WIDTH = ROW_LETTERS * LETTER_WIDTH
ROW_BYTES = ROW_LETTERS * LETTER_HIGHT

# CG hight is 205 lines


def CGA_pallete(index: int) -> tuple[int, int, int]:
    index = index % 0x10
    intensity = 0x55 * index // 8
    return (0xAA * bool(index & 4) + intensity,
            0xAA * bool(index & 2) + intensity,
            0xAA * bool(index & 1) + intensity,
            )


def CGA_mode4_pallete_1(index: int) -> tuple[int, int, int]:
    index = index % 4
    if index == 0:
        return BG
    return (0xAA * bool(index & 2) + GLOBAL_INTENSITY,
            0xAA * bool(index & 1) + GLOBAL_INTENSITY,
            0xAA + GLOBAL_INTENSITY)


def MDA_pallete(index: int) -> int:
    index = index % 4
    return 0xAA * bool(index & 2) + 0x55 * bool(index & 1)


def draw_CG(call: bytes, width_pix, interlaced=True) -> Image.Image:
    line_sz = math.ceil(width_pix / 4)
    hight = math.ceil(len(call) / line_sz) + 1
    image = Image.new("RGB", (width_pix, hight))
    pixels = image.load()
    # An interlaced file is made of lines which are devided into two fields.
    # The fields go sequentialy in both the file and the CGA screen
    # buffer, but are interleaved on the monitor.
    if interlaced:
        field_sz = len(call) // 2
        fields = call[:field_sz], call[field_sz:]
    else:
        fields = (call,)
    del call
    for field_i, field in enumerate(fields):
        lines = (field[y*line_sz:(y+1)*line_sz] for y in range(hight))
        for y, line in enumerate(lines):
            if interlaced:
                y = 2 * y + field_i
            for byte_i, byte in enumerate(line):
                # each byte represents 4 pixels
                pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
                pix_quad = (p & 3 for p in pix_quad)
                for pixel_i, p in enumerate(pix_quad):
                    x = 4 * byte_i + pixel_i
                    try:
                        pixels[x, y] = CGA_mode4_pallete_1(p)
                    except IndexError:
                        print(f"Error: draw to [{x}, {y}]")
    return image


def draw_2bit_font(call: bytes) -> Image.Image:
    image = Image.new("L", (8, math.ceil(len(call) / 2)))
    pixels = image.load()
    for byte_i, byte in enumerate(call):
        x = 4 * (byte_i % 2)
        y = 2 * (byte_i // 2)
        for pixel_i in range(4):
            pixel = (byte >> 2 * pixel_i) & 3
            try:
                pixels[x + pixel_i, y] = MDA_pallete(pixel)
            except IndexError:
                print(f"Error: draw to [{x+pixel_i}, {y}]")
    return image


def draw_1bit_font(call: bytes) -> Image.Image:
    image = Image.new("1", (ROW_WIDTH, math.ceil(len(call) / ROW_LETTERS)))
    pixels = image.load()
    rows = (call[i * ROW_BYTES:(i+1) * ROW_BYTES] for i in range(len(call)))
    for row_i, row in enumerate(rows):
        letters = (row[i * LETTER_HIGHT: (i+1) * LETTER_HIGHT] for i in range(ROW_LETTERS))
        for letter_i, letter in enumerate(letters):
            for byte_i, byte in enumerate(letter):
                y = LETTER_HIGHT * row_i + byte_i
                for pix_i in range(LETTER_WIDTH):
                    x = LETTER_WIDTH * letter_i + pix_i
                    try:
                        pixels[x, y] = byte >> (7 - pix_i) & 1
                    except IndexError:
                        print(f"Error: draw to [{x}, {y}]")
    return image


def get_1bit_font(call: bytes) -> list[Image.Image]:
    pixels = image.load()
    rows = (call[i * ROW_BYTES:(i + 1) * ROW_BYTES] for i in range(len(call)))
    for row_i, row in enumerate(rows):
        letters = (row[i * LETTER_HIGHT: (i + 1) * LETTER_HIGHT] for i in range(ROW_LETTERS))
        for letter_i, letter in enumerate(letters):
            for byte_i, byte in enumerate(letter):
                y = LETTER_HIGHT * row_i + byte_i
                for pix_i in range(LETTER_WIDTH):
                    x = LETTER_WIDTH * letter_i + pix_i
                    try:
                        pixels[x, y] = byte >> (7 - pix_i) & 1
                    except IndexError:
                        print(f"Error: draw to [{x}, {y}]")
    return image


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scroll")
    parser.add_argument("action")
    parser.add_argument("line_length", type=int, default=LINE_PIX, nargs="?")
    parser.add_argument("-p", "--progrssive", action="store_true")
    parser.add_argument("-o", "--offset", type=int, default=0, nargs="?")
    args = parser.parse_args()

    def main():
        scroll_nom = args.scroll
        with open(scroll_nom, "rb") as file:
            file.seek(args.offset)
            scroll = file.read()

        if args.action == "cg":
            image = draw_CG(scroll, args.line_length, not args.progrssive)
        elif args.action == "lm":
            image = draw_CG(scroll, MESG_WIDTH, False)
        elif sys.argv[2] == "ft":
            image = draw_1bit_font(scroll)
        else:
            raise ValueError

        image.save(f"{scroll_nom}.png")
