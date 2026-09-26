"""Genera icon.ico (multi-tamaño) e icon.png. Uso: python tools/make_icon.py  (requiere Pillow)"""
from PIL import Image, ImageDraw, ImageFilter
import os, sys

TOP, BOTTOM = (99, 102, 241), (67, 56, 202)   # indigo-500 -> indigo-700 (acento de la UI)

def lerp(a, b, t): return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

def render(size):
    S = 8                         # supersampling
    W = size * S
    small = size <= 32
    # Fondo: cuadrado redondeado casi a sangre, con degradado diagonal
    margin = (0.03 if small else 0.06) * W
    radius = (0.22 if small else 0.235) * W
    grad = Image.new('RGB', (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=lerp(TOP, BOTTOM, y / W))
    # brillo sutil arriba a la izquierda
    glow = Image.new('L', (W, W), 0)
    ImageDraw.Draw(glow).ellipse([-0.4 * W, -0.55 * W, 0.9 * W, 0.55 * W], fill=38)
    glow = glow.filter(ImageFilter.GaussianBlur(W * 0.08))
    grad = Image.composite(Image.new('RGB', (W, W), (255, 255, 255)), grad, glow)
    mask = Image.new('L', (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, W - margin, W - margin], radius, fill=255)
    img = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # Glifo en coordenadas del icono "music" de Lucide (viewBox 24), con la segunda
    # cabeza de nota reemplazada por una flecha de descarga
    glyph_w = (0.70 if small else 0.60) * W
    unit = glyph_w / 18.5
    ox = W / 2 - 13.3 * unit
    oy = W / 2 - 12.0 * unit
    P = lambda x, y: (ox + x * unit, oy + y * unit)
    stroke = (2.9 if small else 2.35) * unit
    white = (255, 255, 255, 255)

    def line(a, b):
        d.line([P(*a), P(*b)], fill=white, width=round(stroke))
        for p in (a, b):
            cx, cy = P(*p); r = stroke / 2
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)

    layer = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    line((9, 17.5), (9, 5.2))           # plica izquierda
    line((9, 5.2), (20, 3.2))           # barra
    line((20, 3.2), (20, 18.6))         # plica derecha = eje de la flecha
    line((15.9, 14.6), (20, 18.7))      # punta de flecha
    line((24.1, 14.6), (20, 18.7))
    hx, hy = P(5.9, 17.9); hr = 3.35 * unit   # cabeza de nota rellena
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=white)

    # sombra suave del glifo
    shadow = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    shadow.putalpha(layer.getchannel('A').point(lambda a: a * 0.28))
    shadow = shadow.filter(ImageFilter.GaussianBlur(W * 0.012))
    img.alpha_composite(shadow, (0, round(W * 0.012)))
    img.alpha_composite(layer)
    return img.resize((size, size), Image.LANCZOS)

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
imgs = {s: render(s) for s in sizes}
render(1024).save(f'{out}/icon.png')
imgs[256].save(f'{out}/icon.ico', sizes=[(s, s) for s in sizes], append_images=[imgs[s] for s in sizes[:-1]])
