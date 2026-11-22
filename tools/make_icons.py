# Generates the BeamCraft button icons in the style of flipH / rotateCW:
# 50 x 50 px, flat shapes, accent blue #57C8FF + grey #484848, 3 px margin.
# Usage: python tools/make_icons.py   (writes the PNG files next to beamcraft.py)

import os

from PIL import Image, ImageDraw

BLUE = (87, 200, 255, 255)
GREY = (72, 72, 72, 255)
K = 8                      # supersampling
S = 50 * K
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def canvas():
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def p(*v):
    return [x * K for x in v]


def save(im, name):
    im.resize((50, 50), Image.LANCZOS).save(os.path.join(OUT, name))


def home():
    im, d = canvas()
    d.polygon(p(25, 3, 47, 23, 3, 23), fill=BLUE)                 # roof
    d.rectangle(p(9, 23, 41, 47), fill=GREY)                       # walls
    d.rounded_rectangle(p(20, 30, 30, 47), radius=1 * K, fill=BLUE)  # door
    save(im, "home.png")


def aim():
    im, d = canvas()
    w = int(4.5 * K)
    d.ellipse(p(10, 10, 40, 40), outline=BLUE, width=w)          # ring
    for x0, y0, x1, y1 in ((25, 3, 25, 16), (25, 34, 25, 47), (3, 25, 16, 25), (34, 25, 47, 25)):
        d.line(p(x0, y0, x1, y1), fill=BLUE, width=w)              # cross hairs
    d.ellipse(p(21.5, 21.5, 28.5, 28.5), fill=GREY)               # centre dot
    save(im, "aim.png")


def pause():
    im, d = canvas()
    d.rounded_rectangle(p(12, 8, 21, 42), radius=2 * K, fill=BLUE)
    d.rounded_rectangle(p(29, 8, 38, 42), radius=2 * K, fill=BLUE)
    save(im, "pause.png")


def play():
    im, d = canvas()
    d.polygon(p(14, 7, 42, 25, 14, 43), fill=BLUE)
    save(im, "play.png")


def stop():
    im, d = canvas()
    d.rounded_rectangle(p(10, 10, 40, 40), radius=3 * K, fill=BLUE)
    save(im, "stop.png")


if __name__ == "__main__":
    for f in (home, aim, pause, play, stop):
        f()
    print("icons written to", OUT)
