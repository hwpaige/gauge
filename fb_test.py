import time, sys

fb = open('/dev/fb0', 'r+b')
W, H = 240, 280

colors = [
    (b'\x00\xf8', 'RED'),    # RGB565 red   = 0xF800 LE
    (b'\xe0\x07', 'GREEN'),  # RGB565 green = 0x07E0 LE
    (b'\x1f\x00', 'BLUE'),   # RGB565 blue  = 0x001F LE
    (b'\xff\xff', 'WHITE'),
    (b'\x00\x00', 'BLACK'),
]

for pixel, name in colors:
    print(f'Writing {name}...', flush=True)
    fb.seek(0)
    fb.write(pixel * (W * H))
    fb.flush()
    time.sleep(2)

fb.close()
print('Done')
