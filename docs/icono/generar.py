"""Icono de Cointra Electric: un radiador (marco con cuatro aletas y dos patas)
con una llama, en blanco y crema sobre un cuadrado redondeado naranja.

Uso: python3 generar.py <carpeta>  (escribe icon.png y icon@2x.png)
"""
import sys
from PIL import Image, ImageDraw


def dibujar(lado: int) -> Image.Image:
    S = 4  # sobremuestreo para bordes suaves
    W = lado * S
    u = W / 256  # unidades de un lienzo de 256

    # Fondo: cuadrado redondeado con un degradado vertical suave.
    fondo = Image.new("RGBA", (W, W))
    top, bottom = (241, 124, 62), (196, 69, 27)
    df = ImageDraw.Draw(fondo)
    for y in range(W):
        t = y / (W - 1)
        df.line([(0, y), (W, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mascara = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mascara).rounded_rectangle([0, 0, W - 1, W - 1], radius=56 * u, fill=255)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    img.paste(fondo, (0, 0), mascara)

    # Radiador en blanco, sobre una capa de alfa.
    capa = Image.new("L", (W, W), 0)
    d = ImageDraw.Draw(capa)

    def caja(x0, y0, x1, y1, r):
        d.rounded_rectangle([x0 * u, y0 * u, x1 * u, y1 * u], radius=r * u, fill=255)

    # Marco: rectángulo redondeado con el interior vaciado.
    caja(54, 82, 201, 190, 14)
    d.rounded_rectangle([66 * u, 94 * u, 189 * u, 178 * u], radius=4 * u, fill=0)
    # Cuatro aletas verticales entre el marco y el interior.
    for x0 in (78, 106, 134, 162):
        d.rectangle([x0 * u, 93 * u, (x0 + 11) * u, 179 * u], fill=255)
    # Dos patas.
    caja(66, 184, 78, 211, 6)
    caja(178, 184, 190, 211, 6)

    blanco = Image.new("RGBA", (W, W), (255, 255, 255, 255))
    img.paste(blanco, (0, 0), capa)

    # Llama en crema: una gota con una muesca y una punta curva a la izquierda.
    llama = Image.new("L", (W, W), 0)
    dl = ImageDraw.Draw(llama)
    cx, cy, r = 118 * u, 87 * u, 20 * u
    dl.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    def bezier(p0, p1, p2, n=40):
        return [
            (
                ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]) * u,
                ((1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]) * u,
            )
            for t in (k / n for k in range(n + 1))
        ]

    # Punta: media luna fina entre dos curvas que parten de la cima (117, 44).
    exterior = bezier((117, 44), (98, 62), (97, 88))
    interior = bezier((97, 88), (104, 66), (117, 44))
    dl.polygon(exterior + interior, fill=255)
    # Muesca de la llama (el fondo se ve a través).
    mx, my, mr = 127 * u, 74 * u, 12 * u
    dl.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=0)
    crema = Image.new("RGBA", (W, W), (255, 224, 178, 255))
    img.paste(crema, (0, 0), llama)
    return img.resize((lado, lado), Image.LANCZOS)


destino = sys.argv[1]
dibujar(256).save(f"{destino}/icon.png", optimize=True)
dibujar(512).save(f"{destino}/icon@2x.png", optimize=True)
print("ok")
