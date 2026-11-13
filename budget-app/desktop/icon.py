"""Zeichnet das Programmsymbol (grünes Feld mit drei steigenden Balken) als PNG: python3 icon.py ziel.png"""
import sys

from PIL import Image, ImageDraw

G = 1024
bild = Image.new("RGBA", (G, G), (0, 0, 0, 0))
d = ImageDraw.Draw(bild)
d.rounded_rectangle((64, 64, G - 64, G - 64), radius=200, fill=(22, 163, 107))
for i, hoehe in enumerate((260, 420, 600)):
    x = 250 + i * 190
    d.rounded_rectangle((x, 800 - hoehe, x + 130, 800), radius=36, fill=(255, 255, 255))
bild.save(sys.argv[1])
