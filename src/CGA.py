import sys

from PIL import Image

BG = (0, 0, 0xAA)
GLOBAL_INTENSITY = 0x55
FIELD_SZ = 0x2000
LINE_PIX = 320
LINE_SZ = LINE_PIX  // 4
MESG_LINE_SZ = 0x20

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


def draw_message(call: bytes) -> Image.Image:
    image = Image.new("RGB", (4 * MESG_LINE_SZ, len(scroll) // MESG_LINE_SZ + 1))
    pixels = image.load()
    y = 0
    while call:
        x = 0
        for byte in call[:MESG_LINE_SZ]:
            pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
            pix_quad = (p & 3 for p in pix_quad)
            for pl, p in enumerate(pix_quad):
                pixels[x + pl, y] = CGA_mode4_pallete_1(p)
            x += 4
        call = call[MESG_LINE_SZ:]
        y += 1
    return image


def draw_CG(call: bytes) -> Image.Image:
    image = Image.new("RGB", (LINE_PIX, len(scroll) // LINE_SZ + 2))
    pixels = image.load()
    for byte_i, byte in enumerate(call):
        field_i = byte_i // FIELD_SZ
        byte_i %= FIELD_SZ
        x = 4 * (byte_i % LINE_SZ)
        y = 2 * (byte_i // LINE_SZ) + field_i
        pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
        pix_quad = (p & 3 for p in pix_quad)
        for pixel_i, p in enumerate(pix_quad):
            try:
                pixels[x + pixel_i, y] = CGA_mode4_pallete_1(p)
            except IndexError:
                print(f"Error: draw to [{x+pixel_i}, {y}]")
    return image


def draw_2bit_font(call: bytes) -> Image.Image:
    image = Image.new("L", (8, len(scroll) // 2))
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
    image = Image.new("1", (0x100, len(scroll) // 0x20))
    pixels = image.load()
    for byte_i, byte in enumerate(call):
        letter_i = byte_i // 8
        y = byte_i % 8
        col = letter_i % 0x20
        row = letter_i // 0x20
        for x in range(8):
            try:
                pixels[8 * col + x, 8 * row + y] = byte >> (7 - x) & 1
            except IndexError:
                print(f"Error: draw to [{x}, {y}]")
    return image


scroll_nom = sys.argv[1]
with open(scroll_nom, "rb") as file:
    scroll = file.read()

if sys.argv[2] == "lm":
    image = draw_message(scroll)
elif sys.argv[2] == "cg":
    image = draw_CG(scroll)
elif sys.argv[2] == "ft":
    image = draw_1bit_font(scroll)
else:
    raise ValueError

image.save(f"{scroll_nom}.png")
