from PIL import Image

image = Image.new("RGB", (0x20, 0x19))
pixels = image.load()

CGA = {
    0: (0,0,0),
    1: (0,0,0xAA),
    1: (0,0,0xAA)
}

def draw_low_middle(call: bytes):
    pointer = 0x1424
    x = 0
    y = 0
    for byte in call[:0x10]:
        pix_couple = byte & 0xF, byte & 0xF0
        pixels[x, y], pixels[x + 1, y] = (CGA[p] for p in pix_couple)
        x += 2
    pointer = pointer + 0x10
    call = call[0x10:]
    x = 0
    for byte in call[:0x10]:
        pix_couple = byte & 0xF, byte & 0xF0
        pixels[x, y + 1], pixels[x + 1, y + 1] = pix_couple
        x += 2
    y += 2
