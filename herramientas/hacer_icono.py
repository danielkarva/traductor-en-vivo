"""Genera icono.ico (y icono.png) del Traductor en vivo.
Uso: .venv\\Scripts\\python.exe herramientas\\hacer_icono.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024  # se dibuja grande y se reduce (bordes suaves)


def font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(r"C:\Windows\Fonts", n), size)
        except OSError:
            continue
    return ImageFont.load_default()


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# fondo: cuadrado redondeado con degradado azul
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
top, bottom = (95, 179, 255), (32, 92, 205)
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
# burbuja de chat blanca con colita
d.rounded_rectangle([150, 210, S - 150, 720], radius=150, fill=(255, 255, 255, 255))
d.polygon([(300, 690), (270, 880), (470, 700)], fill=(255, 255, 255, 255))

# "A" y "文" dentro de la burbuja, separados por una flecha
fa = font(["seguibl.ttf", "seguisb.ttf", "arialbd.ttf"], 330)
fz = font(["msyhbd.ttc", "msyh.ttc", "simsun.ttc"], 300)
fx = font(["seguisb.ttf", "arialbd.ttf"], 150)
d.text((330, 470), "A", font=fa, fill=(32, 92, 205, 255), anchor="mm")
d.text((S / 2, 470), "›", font=fx, fill=(150, 165, 185, 255), anchor="mm")
d.text((S - 330, 475), "文", font=fz, fill=(255, 140, 50, 255), anchor="mm")

png = os.path.join(BASE, "icono.png")
ico = os.path.join(BASE, "icono.ico")
img.resize((256, 256), Image.LANCZOS).save(png)
img.save(ico, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
                     (64, 64), (128, 128), (256, 256)])
print("listo:", ico)
