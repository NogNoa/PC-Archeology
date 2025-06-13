import sys

from PIL import Image

BG = (0, 0, 0xAA)
GLOBAL_INTENSITY = 0x55
FIELD_SZ = 0x2000
LINE_PIX = 320
LINE_SZ = LINE_PIX  // 4

# CG hight is 205

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


def draw_low_middle(call: bytes):
    y = 0
    while call:
        x = 0
        for byte in call[:0x20]:
            pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
            pix_quad = (p & 3 for p in pix_quad)
            for pl, p in enumerate(pix_quad):
                pixels[x + pl, y] = CGA_mode4_pallete_1(p)
            x += 4
        call = call[0x20:]
        y += 1


def draw_CG(call: bytes):
    for pli, byte in enumerate(call):
        x = pli % LINE_SZ
        y = pli % FIELD_SZ // LINE_SZ
        field_i = pli // FIELD_SZ
        pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
        pix_quad = (p & 3 for p in pix_quad)
        for plj, p in enumerate(pix_quad):
            try:
                pixels[4 * x + plj, 2 * y + field_i] = CGA_mode4_pallete_1(p)
            except IndexError:
                print("actual x:", 4 * x + plj, "actual y:", 2 * y + field_i)


scroll_nom = sys.argv[1]
with open(scroll_nom, "rb") as file:
    scroll = file.read()

if sys.argv[2] == "lm":
    image = Image.new("RGB", (0x80, len(scroll) // 0x20 + 1))
    pixels = image.load()
    draw_low_middle(scroll)
elif sys.argv[2] == "cg":
    image = Image.new("RGB", (LINE_PIX, len(scroll) // LINE_SZ + 1))
    pixels = image.load()
    draw_CG(scroll)
else:
    raise ValueError

image.save(f"{scroll_nom}.png")
