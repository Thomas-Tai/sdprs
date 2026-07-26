"""Regenerate the app icon and splash image.

    cd webcam_client && /c/Python314/python assets/make_assets.py

Generated from code so they are reproducible and diffable in review. Replace the
two output files directly if real branding becomes available.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BG = (18, 32, 52)
ACCENT = (63, 138, 224)
TEXT = (238, 242, 248)
MUTED = (150, 168, 190)
# Small-size palette: the 256px navy body disappears against a dark taskbar once
# downsampled, and the thin ring closes up. Lift both for 16/24px.
BG_SMALL = (30, 52, 84)
ACCENT_SMALL = (99, 170, 246)

# PIL's default bitmap font renders CJK as boxes, so find a real CJK face.
# Absent one, the splash falls back to ASCII rather than shipping tofu.
_CJK_CANDIDATES = ["C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/msyh.ttc"]


def _font(size, prefer_cjk=True):
    if prefer_cjk:
        for path in _CJK_CANDIDATES:
            if Path(path).is_file():
                try:
                    return ImageFont.truetype(path, size), True
                except OSError:
                    pass
    try:
        return ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", size), False
    except OSError:
        return ImageFont.load_default(), False


def _icon_detailed():
    """256px master: full lens with mount nub. Reads well at 32px and up."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 248, 248], radius=52, fill=BG)
    d.ellipse([76, 76, 180, 180], fill=ACCENT)          # lens
    d.ellipse([104, 104, 152, 152], fill=BG)            # aperture
    d.rounded_rectangle([112, 34, 144, 62], radius=8, fill=ACCENT)  # mount
    return img


def _icon_compact(size):
    """Hand-tuned 16/24px artwork.

    Naive downsampling of the master closes the aperture into a dot and reduces
    the mount nub to a stray pixel, so it reads as a blob rather than a camera.
    This drops the nub, thickens the ring, and lifts both colours for contrast on
    a dark taskbar. Drawn at 8x then downsampled -- drawing directly at 16px
    gives jagged, uneven strokes.
    """
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.02
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.22, fill=BG_SMALL)
    inset = s * 0.20
    d.ellipse([inset, inset, s - inset, s - inset],
              outline=ACCENT_SMALL, width=int(s * 0.15))
    return img.resize((size, size), Image.LANCZOS)


def make_icon():
    """Write a multi-resolution .ico with DIFFERENT artwork at small sizes.

    Pillow's ICO writer uses an entry from `append_images` verbatim when its size
    matches a requested size exactly, and otherwise resamples the master -- so
    16/24 come from _icon_compact and the rest from the 256px master.
    (Verified against Pillow 12.1.1 IcoImagePlugin._save.)
    """
    master = _icon_detailed()
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    master.save(HERE / "sdprs.ico", sizes=sizes,
                append_images=[_icon_compact(16), _icon_compact(24)])
    print("wrote sdprs.ico")


def make_splash():
    img = Image.new("RGB", (420, 240), BG)
    d = ImageDraw.Draw(img)
    d.ellipse([170, 40, 250, 120], outline=ACCENT, width=7)
    d.ellipse([196, 66, 224, 94], fill=ACCENT)
    title, _ = _font(30, prefer_cjk=False)
    d.text((210, 145), "SDPRS", font=title, fill=TEXT, anchor="mm")
    sub, is_cjk = _font(15)
    msg = "啟動中，請稍候…" if is_cjk else "Starting, please wait..."
    d.text((210, 178), msg, font=sub, fill=MUTED, anchor="mm")
    img.save(HERE / "splash.png")
    print(f"wrote splash.png (cjk={is_cjk})")


if __name__ == "__main__":
    make_icon()
    make_splash()
