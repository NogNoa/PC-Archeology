import sys

from PIL import Image

BG = (0, 0, 0xAA)
GLOBAL_INTENSITY = 0x55

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
        for byte in call[:0x10]:
            pix_quad = byte >> 6, byte >> 4, byte >> 2, byte
            pix_quad = (p & 3 for p in pix_quad)
            for pl, p in enumerate(pix_quad):
                pixels[x + pl, y] = CGA_mode4_pallete_1(p)
            x += 4
        call = call[0x10:]
        y += 1


scroll_nom = sys.argv[1]
with open(scroll_nom, "rb") as file:
    scroll = file.read()

image = Image.new("RGB", (0x40, len(scroll) // 0x10))
pixels = image.load()

draw_low_middle(scroll)

image.save(f"{scroll_nom}.png")
