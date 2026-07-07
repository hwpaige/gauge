import mmap, os, time, subprocess

print("=== FB0 diagnostics ===")
print("virtual_size:", open('/sys/class/graphics/fb0/virtual_size').read().strip())
print("bits_per_pixel:", open('/sys/class/graphics/fb0/bits_per_pixel').read().strip())

try:
    print("stride:", open('/sys/class/graphics/fb0/stride').read().strip())
except:
    pass

print("\n=== mmap test ===")
fd = os.open('/dev/fb0', os.O_RDWR)
size = 240 * 280 * 2
m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)

colors = [
    (b'\x00\xf8', 'RED    (0xF800)'),
    (b'\xe0\x07', 'GREEN  (0x07E0)'),
    (b'\x1f\x00', 'BLUE   (0x001F)'),
    (b'\xff\xff', 'WHITE  (0xFFFF)'),
    (b'\x00\x00', 'BLACK  (0x0000)'),
]

for pixel, name in colors:
    print(f"Writing {name}...", flush=True)
    m.seek(0)
    m.write(pixel * (240 * 280))
    time.sleep(3)

m.close()
os.close(fd)

print("\n=== SPI stats ===")
for path in ['/sys/bus/spi/devices/spi1.0/', '/sys/class/spi_master/spi1/']:
    try:
        print(path, os.listdir(path))
    except Exception as e:
        print(path, e)

print("\n=== fbtft module params ===")
import glob
for f in glob.glob('/sys/module/fbtft/parameters/*'):
    try:
        print(f.split('/')[-1], '=', open(f).read().strip())
    except:
        pass

print("\n=== deferred_io ===")
# Try to force a flush by reading and rewriting
fd = os.open('/dev/fb0', os.O_RDWR)
m = mmap.mmap(fd, size)
# Write solid red then check dmesg
m.seek(0)
m.write(b'\x00\xf8' * (240 * 280))
print("Wrote red via mmap, waiting 2s for deferred_io timer...")
time.sleep(2)
m.close()
os.close(fd)
print("Done")
