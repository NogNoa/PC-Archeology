sector_sz = 0x200
track_sz = 0x1000

def game_factory(scroll_nom:str):
    with open(scroll_nom, "br") as file:
        scroll = file.read()
    scroll = tuple(scroll[sector_sz*i:sector_sz*(i+1)] for i in range(len(scroll)//sector_sz))