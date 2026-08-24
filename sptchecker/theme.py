"""Shared visual language: fonts, color math, and the PIL-rendered primitives
Tk cannot draw itself.

Tk's canvas has no anti-aliasing on Windows, so every rounded corner, pill and
small glyph here is drawn at SS times its final size with Pillow and then
LANCZOS-downsampled. The same shapes drawn with create_oval/create_arc come
out visibly stair-stepped at these sizes, at any display scaling.

Rendered images are cached by their full parameter tuple and held for the
process lifetime: a Tk widget keeps no Python reference to its own PhotoImage,
so an uncached one is garbage collected out from under the widget and renders
as an empty box.
"""
import tkinter as tk
import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

from .config import (
    ACCENT_NEW, BORDER, CARD_BG, CARD_HOVER, CARD_RADIUS, SEPARATOR,
    TEXT, TEXT_BRIGHT, TEXT_DIM, TEXT_FAINT,
)

SS = 4  # supersample factor for every rendered primitive

FONT_FAMILY = "Segoe UI"

_fonts = {}
_images = {}


# ── Fonts ──────────────────────────────────────────────────────────────

def font(size=9, weight="normal", slant="roman", family=FONT_FAMILY):
    """Cached tkfont. Sharing one object per (family, size, weight) keeps the
    measure() calls the card layout makes on every render cheap."""
    key = (family, size, weight, slant)
    if key not in _fonts:
        _fonts[key] = tkfont.Font(family=family, size=size, weight=weight, slant=slant)
    return _fonts[key]


def ellipsize(fnt, text, max_px):
    """Truncate text to fit max_px, ending in a single ellipsis character.

    Binary search rather than a character-at-a-time walk: this runs for the
    title and description of every visible card on every resize event."""
    if max_px <= 0 or not text:
        return ""
    if fnt.measure(text) <= max_px:
        return text
    ell = "…"
    ell_w = fnt.measure(ell)
    if ell_w > max_px:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fnt.measure(text[:mid]) + ell_w <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


# ── Color math ─────────────────────────────────────────────────────────

def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def blend(fg_hex, bg_hex, alpha):
    """Mix fg over bg at the given alpha. Tk fills are opaque, so anything
    meant to read as translucent -- a tinted badge, a soft chart fill -- has to
    have the translucency pre-baked into a solid color."""
    fg, bg = _rgb(fg_hex), _rgb(bg_hex)
    return "#%02x%02x%02x" % tuple(
        round(bg[i] + (fg[i] - bg[i]) * alpha) for i in range(3))


def lighten(hex_color, amount):
    return blend("#ffffff", hex_color, amount)


def darken(hex_color, amount):
    return blend("#000000", hex_color, amount)


# ── Rendered primitives ────────────────────────────────────────────────

def _rgba(hex_color, alpha=255):
    return _rgb(hex_color) + (alpha,)


def _render(key, size, painter):
    """Render an RGBA primitive at SS scale, downsample, cache, return a
    PhotoImage. RGBA rather than a pre-composited opaque tile, so one cached
    image works on any surface: the transparent area outside the corners lets
    the widget or canvas background show through, which is what makes a single
    card cap valid on both the resting and the hovered card."""
    if key in _images:
        return _images[key]
    w, h = int(size[0]), int(size[1])
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    painter(ImageDraw.Draw(img), w * SS, h * SS)
    photo = ImageTk.PhotoImage(img.resize((max(1, w), max(1, h)), Image.LANCZOS))
    _images[key] = photo
    return photo


def rounded_rect(w, h, radius, fill, outline=None, outline_w=1):
    """A standalone rounded rectangle, transparent outside its corners."""
    def paint(d, W, H):
        d.rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=radius * SS,
            fill=_rgba(fill) if fill else None,
            outline=_rgba(outline) if outline else None,
            width=outline_w * SS if outline else 0,
        )
    return _render(("rr", w, h, radius, fill, outline, outline_w), (w, h), paint)


def pill(w, h, fill, outline=None, outline_w=1):
    """A stadium: a rounded rect whose radius is exactly half its height."""
    return rounded_rect(w, h, h / 2, fill, outline, outline_w)


def dot(size, color):
    def paint(d, W, H):
        d.ellipse([0, 0, W - 1, H - 1], fill=_rgba(color))
    return _render(("dot", size, color), (size, size), paint)


def ring(size, color, width=2):
    def paint(d, W, H):
        inset = width * SS / 2
        d.ellipse([inset, inset, W - 1 - inset, H - 1 - inset],
                  outline=_rgba(color), width=width * SS)
    return _render(("ring", size, color, width), (size, size), paint)


def info_glyph(size, color):
    """The lowercase-i-in-a-circle used for the category legend."""
    def paint(d, W, H):
        stroke = max(SS, int(SS * 1.15))
        d.ellipse([stroke / 2, stroke / 2, W - 1 - stroke / 2, H - 1 - stroke / 2],
                  outline=_rgba(color), width=stroke)
        cx = W / 2
        r = SS * 1.1
        cy = H * 0.29
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgba(color))
        d.line([cx, H * 0.44, cx, H * 0.73], fill=_rgba(color), width=int(SS * 1.3))
    return _render(("info", size, color), (size, size), paint)


def endorse_glyph(size, color, filled=False):
    """A thumbs-up, hollow until the mod has been opened to endorse it.

    Drawn as a polygon rather than a font character because the emoji fonts
    that carry 👍 render it in their own colors, which cannot be tinted to
    match the card and look nothing like the rest of the app's iconography.
    """
    def paint(d, W, H):
        def p(x, y):
            return (x * W / 24, y * H / 24)
        hand = [p(8, 22), p(8, 10.5), p(12, 6.5), p(12.8, 2.2), p(14.6, 2.2),
                p(15.4, 4.2), p(14.6, 8.6), p(20, 8.6), p(21.6, 10.4),
                p(20.2, 21.2), p(18.4, 22)]
        cuff = [*p(1.8, 10.6), *p(6.6, 22)]
        radius = W / 24 * 1.4
        if filled:
            d.polygon(hand, fill=_rgba(color))
            d.rounded_rectangle(cuff, radius=radius, fill=_rgba(color))
        else:
            stroke = max(1, int(W / 24 * 1.7))
            d.line(hand + [hand[0]], fill=_rgba(color), width=stroke, joint="curve")
            d.rounded_rectangle(cuff, radius=radius, outline=_rgba(color), width=stroke)
    return _render(("endorse", size, color, filled), (size, size), paint)


def notes_glyph(w, h, color):
    """A document-with-lines mark, shown on cards that carry change notes."""
    def paint(d, W, H):
        stroke = max(1, int(SS * 0.9))
        d.rounded_rectangle([stroke, stroke, W - 1 - stroke, H - 1 - stroke],
                            radius=SS * 1.5, outline=_rgba(color), width=stroke)
        for frac, right in ((0.32, 0.72), (0.52, 0.72), (0.72, 0.56)):
            y = H * frac
            d.line([W * 0.28, y, W * right, y], fill=_rgba(color), width=stroke)
    return _render(("notes", w, h, color), (w, h), paint)


# ── Card background (3-slice) ──────────────────────────────────────────

# A card is drawn as two rendered end caps plus a plain canvas rectangle
# between them. Re-rendering a full-width rounded rect at SS scale on every
# <Configure> would mean a multi-megapixel Pillow pass per card per resize
# step; the caps are CAP_W wide, cached, and never change with the card width.
CAP_W = 20

# The category color still outlines the whole card, but as a 1px rounded stroke
# with a thicker rail down the left edge rather than the flat 2px rectangle it
# replaced. Same information, and the rail gives each card a definite reading
# order -- but a full column of them no longer reads as a stack of competing
# boxes, because the weight sits on one edge instead of all four.
CARD_BORDER_W = 1
CARD_RAIL_W = 4


def _slice(h, fill, accent, side, radius, rail_w, border_w):
    def paint(d, W, H):
        # Drawn three caps wide and cropped by the image bounds, so the corners
        # on the far side fall outside the slice instead of rounding into it.
        span = W * 3
        b = border_w * SS
        left_inset = rail_w * SS if side == "left" else 0
        right_inset = 0 if side == "left" else b
        x0 = 0 if side == "left" else W - span
        d.rounded_rectangle([x0, 0, x0 + span - 1, H - 1],
                            radius=radius * SS, fill=_rgba(accent or fill))
        d.rounded_rectangle(
            [x0 + left_inset, b, x0 + span - 1 - right_inset, H - 1 - b],
            radius=max(0, radius * SS - b), fill=_rgba(fill))
    key = ("slice", h, fill, accent, side, radius, rail_w, border_w)
    return _render(key, (CAP_W, h), paint)


def card_caps(h, fill, accent, radius=CARD_RADIUS):
    """(left, right) end caps for a mod card: a category-colored border, made
    several times thicker down the left edge, wrapped around a `fill` body."""
    return (_slice(h, fill, accent, "left", radius, CARD_RAIL_W, CARD_BORDER_W),
            _slice(h, fill, accent, "right", radius, CARD_RAIL_W, CARD_BORDER_W))


def panel_caps(h, fill, outline=None, radius=CARD_RADIUS):
    """(left, right) end caps for a plain rounded panel -- same 3-slice trick as
    a card, but with an even 1px border (or none at all)."""
    return (_slice(h, fill, outline, "left", radius, CARD_BORDER_W, CARD_BORDER_W),
            _slice(h, fill, outline, "right", radius, CARD_BORDER_W, CARD_BORDER_W))


def rounded_photo(pil_img, radius=6):
    """Round a thumbnail's corners through its alpha channel, so they take on
    whatever surface the card is currently painted rather than baking in one."""
    img = pil_img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], radius=radius * SS, fill=255)
    out = img.copy()
    out.putalpha(mask.resize((w, h), Image.LANCZOS))
    return out


# ── Buttons ────────────────────────────────────────────────────────────

# Tk's own Button draws a fixed square, beveled frame with no option to round
# it, so every button in the app is a small canvas painting a cached pill under
# a text item.
_BUTTON_KINDS = {
    # kind: (resting fill, hover fill, outline, resting text, hover text)
    "subtle": (CARD_BG, CARD_HOVER, BORDER, TEXT, TEXT_BRIGHT),
    "ghost": (None, CARD_BG, None, TEXT_DIM, TEXT_BRIGHT),
}


class PillButton(tk.Canvas):
    """A rounded, flat button covering the subset of tk.Button this app uses:
    .configure(text=..., state=...) plus the usual geometry managers."""

    def __init__(self, parent, text, command, font_size=9, kind="subtle",
                 accent=None, padx=12, pady=5, bg=None, **_unused):
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2", takefocus=0)
        self._command = command
        self._font = font(font_size)
        self._kind = kind
        self._accent = accent
        self._text = text
        self._state = "normal"
        self._hover = False
        self._padx, self._pady = padx, pady
        self._photo = None
        self._pressed = False
        self._resize()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _colors(self):
        if self._accent:
            if self._state == "disabled":
                return CARD_BG, BORDER, TEXT_FAINT
            fill = lighten(self._accent, 0.12) if self._hover else self._accent
            return fill, None, "#0c0e13"
        fill, hover_fill, outline, fg, hover_fg = _BUTTON_KINDS[self._kind]
        if self._state == "disabled":
            return fill, outline, TEXT_FAINT
        if self._hover:
            return hover_fill, outline, hover_fg
        return fill, outline, fg

    def _resize(self):
        self._box_w = self._font.measure(self._text) + self._padx * 2
        self._box_h = self._font.metrics("linespace") + self._pady * 2
        super().configure(width=self._box_w, height=self._box_h)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        fill, outline, fg = self._colors()
        if fill or outline:
            self._photo = pill(self._box_w, self._box_h, fill or self._bg, outline)
            self.create_image(0, 0, anchor="nw", image=self._photo)
        self.create_text(self._box_w / 2, self._box_h / 2, text=self._text,
                         font=self._font, fill=fg)

    def _on_enter(self, _e):
        if self._state == "disabled":
            super().configure(cursor="arrow")
            return
        super().configure(cursor="hand2")
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()

    def _on_press(self, _e):
        self._pressed = self._state != "disabled"

    def _on_release(self, e):
        """Fire only on a release that follows a press on this button.

        The press half matters: when a window is destroyed on mouse-down, the
        implicit pointer grab dies with it and the OS delivers the release to
        whatever is underneath. A button that acted on any release it received
        would then activate itself from a click aimed at the window that just
        closed -- which is exactly what happened when a popup's close button
        happened to sit over one.
        """
        was_pressed, self._pressed = self._pressed, False
        if not was_pressed or self._state == "disabled" or not self._command:
            return
        if 0 <= e.x <= self._box_w and 0 <= e.y <= self._box_h:
            self._command()

    # -- tk.Button-compatible surface ---------------------------------

    def configure(self, cnf=None, **kw):
        text = kw.pop("text", None)
        state = kw.pop("state", None)
        # tk.Button options this widget renders its own way: accepted and
        # ignored so existing call sites don't each need a special case.
        for ignored in ("fg", "foreground", "bg", "background", "activebackground",
                        "activeforeground", "relief", "font", "padx", "pady"):
            kw.pop(ignored, None)
        if cnf or kw:
            super().configure(cnf, **kw)
        if state is not None:
            self._state = state
            self._hover = False
        if text is not None and text != self._text:
            self._text = text
            self._resize()
        elif state is not None:
            self._redraw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)


def flat_button(parent, text, command, font_size=9, **kw):
    """The app's standard button. Kept as a function under its original name
    because every header and popup builds its buttons through it."""
    kw.pop("fg", None)
    return PillButton(parent, text, command, font_size=font_size,
                      kind=kw.pop("kind", "subtle"),
                      accent=kw.pop("accent", None),
                      padx=kw.pop("padx", 12), pady=kw.pop("pady", 5),
                      bg=kw.pop("bg", None))


# ── Toggle switch ──────────────────────────────────────────────────────

class ToggleSwitch(tk.Canvas):
    """A switch and its label drawn as one canvas.

    Replaces tk.Checkbutton, which on Windows draws a native light-mode check
    box with a white field that no combination of color options restyles to
    match a dark theme.
    """

    TRACK_W, TRACK_H, KNOB = 28, 16, 12

    def __init__(self, parent, text, variable, command=None, font_size=9,
                 bg=None, fg=TEXT_DIM):
        self._bg = bg or parent.cget("bg")
        self._font = font(font_size)
        self._fg = fg
        self._text = text
        self._var = variable
        self._command = command
        self._gap = 8
        w = self.TRACK_W + self._gap + self._font.measure(text)
        h = max(self.TRACK_H, self._font.metrics("linespace")) + 4
        super().__init__(parent, bg=self._bg, width=w, height=h,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=0)
        self._box_w, self._box_h = w, h
        self._hover = False
        self._track_img = self._knob_img = None
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._var.trace_add("write", lambda *_a: self._redraw())
        self._redraw()

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _toggle(self, _e):
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _redraw(self):
        self.delete("all")
        on = bool(self._var.get())
        y = (self._box_h - self.TRACK_H) // 2
        if on:
            track = lighten(ACCENT_NEW, 0.08) if self._hover else ACCENT_NEW
        else:
            track = CARD_HOVER if self._hover else SEPARATOR
        self._track_img = pill(self.TRACK_W, self.TRACK_H, track)
        self.create_image(0, y, anchor="nw", image=self._track_img)
        pad = (self.TRACK_H - self.KNOB) // 2
        knob_x = self.TRACK_W - self.KNOB - pad if on else pad
        self._knob_img = dot(self.KNOB, "#0c0e13" if on else TEXT_DIM)
        self.create_image(knob_x, y + pad, anchor="nw", image=self._knob_img)
        self.create_text(self.TRACK_W + self._gap, self._box_h / 2, anchor="w",
                         text=self._text, font=self._font,
                         fill=TEXT_BRIGHT if self._hover else self._fg)


# ── Rounded surfaces ───────────────────────────────────────────────────

class RoundedPanel(tk.Canvas):
    """A rounded, optionally outlined surface with a normal Frame inside it.

    Content goes in `.body` and is laid out with pack/grid as usual; the canvas
    paints the rounded background behind it and tracks the body's requested
    height, so a panel grows with whatever is packed into it. Embedded windows
    always stack above canvas items in Tk, so the background can never end up
    drawn over the content.
    """

    def __init__(self, parent, fill=CARD_BG, outline=None, radius=CARD_RADIUS,
                 padx=12, pady=10, bg=None, height=None):
        self._bg = bg or parent.cget("bg")
        super().__init__(parent, bg=self._bg, highlightthickness=0, bd=0)
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._padx, self._pady = padx, pady
        self._fixed_h = height
        self.body = tk.Frame(self, bg=fill)
        self._body_id = self.create_window(padx, pady, window=self.body, anchor="nw")
        if height:
            super().configure(height=height)
        self.bind("<Configure>", lambda _e: self._paint())
        self.body.bind("<Configure>", lambda _e: self._sync_height())

    def _sync_height(self):
        if self._fixed_h:
            return
        want = self.body.winfo_reqheight() + self._pady * 2
        if want != int(self["height"]):
            super().configure(height=want)

    def set_fill(self, fill):
        self._fill = fill
        self.body.configure(bg=fill)
        self._paint()

    def _paint(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        self.delete("panelbg")
        left, right = panel_caps(h, self._fill, self._outline, self._radius)
        self._caps = (left, right)
        self.create_image(0, 0, anchor="nw", image=left, tags="panelbg")
        self.create_image(w - CAP_W, 0, anchor="nw", image=right, tags="panelbg")
        x0, x1 = CAP_W, w - CAP_W
        if x1 > x0:
            b = CARD_BORDER_W
            self.create_rectangle(x0, b, x1, h - b, fill=self._fill,
                                  width=0, tags="panelbg")
            if self._outline:
                self.create_rectangle(x0, 0, x1, b, fill=self._outline,
                                      width=0, tags="panelbg")
                self.create_rectangle(x0, h - b, x1, h, fill=self._outline,
                                      width=0, tags="panelbg")
        self.tag_lower("panelbg")
        self.itemconfigure(self._body_id, width=max(1, w - self._padx * 2))


def chip(parent, text, color, surface=CARD_BG, font_size=7, weight="bold",
         padx=7, tint=0.18):
    """A small tinted pill of text -- the NEW / UPDATE AVAILABLE style badges.

    A tinted fill with colored text rather than a saturated block with dark
    text on it: at badge size the solid version is the loudest thing on a card,
    which is backwards when the mod title is what someone is actually reading.
    """
    fnt = font(font_size, weight)
    w = fnt.measure(text) + padx * 2
    h = fnt.metrics("linespace") + 4
    img = pill(w, h, blend(color, surface, tint))
    lbl = tk.Label(parent, text=text, image=img, compound="center",
                   font=fnt, fg=color, bg=surface, bd=0, highlightthickness=0)
    lbl._chip_photo = img  # Tk keeps no reference of its own
    return lbl
