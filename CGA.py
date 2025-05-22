import sys

from PIL import Image

image = Image.new("RGB", (0x20, 0x19))
pixels = image.load()


def CGA_pallete(index: int) -> tuple[int, int, int]:
    index = index % 0x10
    intensity = 0x55 * index // 8
    return (0xAA * bool(index & 4) + intensity,
            0xAA * bool(index & 2) + intensity,
            0xAA * bool(index & 1) + intensity,
            )


def draw_low_middle(call: bytes):
    pointer = 0x1424
    y = 0
    while y < 0x18:
        x = 0
        for byte in call[:0x10]:
            pix_couple = byte & 0xF, (byte & 0xF0) >> 4
            pixels[x, y], pixels[x + 1, y] = (CGA_pallete(p) for p in pix_couple)
            x += 2
        pointer = pointer + 0x10
        call = call[0x10:]
        x = 0
        for byte in call[:0x10]:
            pix_couple = byte & 0xF, byte & 0xF0 >> 4
            pixels[x, y + 1], pixels[x + 1, y + 1] = pix_couple
            x += 2
        y += 2


scroll_nom = sys.argv[1]
with open(scroll_nom, "rb") as file:
    scroll = file.read()
draw_low_middle(scroll)

image.save(f"{scroll_nom}.png")
