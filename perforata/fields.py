"""Scalar fields over normalized 2D space.

A Field maps points in the unit square ``(u, v) in [0, 1]^2`` to scalar
values, nominally in ``[0, 1]``. Fields are the mechanism for "convolving"
images (logos, letters) or mathematical gradients with grid/decoration
parameters: a :class:`~perforata.modifiers.FieldModulate` modifier maps
each point of a PointCloud into the unit square (via the cloud's bounding
box or an explicit region) and samples the field there.

Fields compose with arithmetic operators::

    field = 0.3 + 0.7 * ImageField("logo.png")   # keep a minimum floor
    field = RadialGradient() * ImageField("a.png")
"""

from __future__ import annotations

import numpy as np

from .graph import Node


class Field(Node):
    """Base class. Subclasses implement ``sample(uv) -> (N,) array``."""

    def sample(self, uv: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def run(self):
        # Fields are passed through the graph as themselves.
        return self

    # -- composition ----------------------------------------------------
    def __add__(self, other):
        return _BinOp(self, _coerce(other), np.add)

    def __radd__(self, other):
        return _BinOp(_coerce(other), self, np.add)

    def __sub__(self, other):
        return _BinOp(self, _coerce(other), np.subtract)

    def __rsub__(self, other):
        return _BinOp(_coerce(other), self, np.subtract)

    def __mul__(self, other):
        return _BinOp(self, _coerce(other), np.multiply)

    def __rmul__(self, other):
        return _BinOp(_coerce(other), self, np.multiply)

    def inverted(self) -> "Field":
        """1 - field."""
        return _BinOp(Constant(1.0), self, np.subtract)

    def clamped(self, lo: float = 0.0, hi: float = 1.0) -> "Field":
        return _Clamp(self, lo, hi)

    def gamma(self, g: float) -> "Field":
        """Nonlinear response curve: field ** g."""
        return _Gamma(self, g)


def _coerce(value) -> "Field":
    if isinstance(value, Field):
        return value
    return Constant(float(value))


class Constant(Field):
    def __init__(self, value: float):
        super().__init__(value=value)
        self.value = float(value)

    def sample(self, uv):
        return np.full(uv.shape[0], self.value)


class _BinOp(Field):
    def __init__(self, a: Field, b: Field, op):
        super().__init__()
        self.a, self.b, self.op = a, b, op

    def sample(self, uv):
        return self.op(self.a.sample(uv), self.b.sample(uv))


class _Clamp(Field):
    def __init__(self, inner: Field, lo: float, hi: float):
        super().__init__(lo=lo, hi=hi)
        self.inner, self.lo, self.hi = inner, lo, hi

    def sample(self, uv):
        return np.clip(self.inner.sample(uv), self.lo, self.hi)


class _Gamma(Field):
    def __init__(self, inner: Field, g: float):
        super().__init__(g=g)
        self.inner, self.g = inner, g

    def sample(self, uv):
        return np.clip(self.inner.sample(uv), 0.0, None) ** self.g


class LinearGradient(Field):
    """Gradient along a direction (degrees, 0 = +u/east, 90 = +v/north)."""

    def __init__(self, angle_deg: float = 0.0):
        super().__init__(angle_deg=angle_deg)
        self.angle = np.radians(angle_deg)

    def sample(self, uv):
        d = np.array([np.cos(self.angle), np.sin(self.angle)])
        # Project centered coords onto the direction, renormalize to 0..1
        t = (uv - 0.5) @ d
        span = np.abs(d).sum() / 2.0  # max |projection| within the square
        return np.clip(t / (2 * span) + 0.5, 0.0, 1.0)


class RadialGradient(Field):
    """0 at the center of the unit square, 1 at the corners
    (or inverted: 1 at center)."""

    def __init__(self, invert: bool = False):
        super().__init__(invert=invert)
        self.invert = invert

    def sample(self, uv):
        r = np.hypot(uv[:, 0] - 0.5, uv[:, 1] - 0.5) / (np.sqrt(2) / 2)
        r = np.clip(r, 0.0, 1.0)
        return 1.0 - r if self.invert else r


def apply_falloff(d: np.ndarray, falloff: str, k: float) -> np.ndarray:
    """Map a normalized distance d in [0, 1] through a response curve.

    falloff:
        "linear"      : d
        "quadratic"   : d**2
        "exponential" : (exp(k*d) - 1) / (exp(k) - 1)   (k != 0)
        "gaussian"    : 1 - exp(-k * d**2), renormalized to end at 1
        "sphere"      : 1 - sqrt(1 - d**2) — the sagitta of a unit
                        hemisphere. Inverted it reads sqrt(1 - d**2):
                        the height profile of a ball resting on the
                        panel, so modulated cutout sizes render a
                        shaded 3D sphere.
    ``k`` is the steepness for the nonlinear curves (ignored by
    "linear", "quadratic", and "sphere").
    """
    d = np.clip(d, 0.0, 1.0)
    if falloff == "linear":
        return d
    if falloff == "quadratic":
        return d ** 2
    if falloff == "exponential":
        if abs(k) < 1e-9:
            return d
        return (np.exp(k * d) - 1.0) / (np.exp(k) - 1.0)
    if falloff == "gaussian":
        k = max(k, 1e-9)
        return (1.0 - np.exp(-k * d ** 2)) / (1.0 - np.exp(-k))
    if falloff == "sphere":
        return 1.0 - np.sqrt(np.clip(1.0 - d ** 2, 0.0, 1.0))
    raise ValueError(f"unknown falloff {falloff!r}")


class ShapeGradient(Field):
    """Distance field shaped like one of the cutout primitives, passed
    through a nonlinear falloff curve.

    The field is 0 at the center of the unit square and reaches 1 on the
    boundary of the inscribed shape (clamped to 1 beyond it). ``invert``
    flips this (1 at center — e.g. big holes in the middle, shrinking
    outward with an exponential law).

    shape   : "circle", "square", or "hexagon" — the level sets of the
              distance metric take that shape.
    falloff : "linear", "quadratic", "exponential", "gaussian", or
              "sphere" (hemisphere profile — see :func:`apply_falloff`).
    k       : steepness of the nonlinear falloffs.

    Example — circular exponential gradient controlling cutout size::

        FieldModulate("scale",
                      ShapeGradient("circle", falloff="exponential",
                                    k=3.0, invert=True),
                      lo=0.2, hi=1.0)
    """

    def __init__(self, shape: str = "circle", falloff: str = "linear",
                 k: float = 3.0, invert: bool = False):
        super().__init__(shape=shape, falloff=falloff, k=k, invert=invert)
        self.shape = shape
        self.falloff = falloff
        self.k = float(k)
        self.invert = invert

    def sample(self, uv):
        du = uv[:, 0] - 0.5
        dv = uv[:, 1] - 0.5
        if self.shape == "circle":
            d = np.hypot(du, dv)
        elif self.shape == "square":
            d = np.maximum(np.abs(du), np.abs(dv))  # Chebyshev metric
        elif self.shape == "hexagon":
            # Regular hexagon metric (flat sides left/right)
            ax, ay = np.abs(du), np.abs(dv)
            d = np.maximum(ax, ax / 2.0 + ay * (np.sqrt(3) / 2.0))
        else:
            raise ValueError(f"unknown shape {self.shape!r}")
        vals = apply_falloff(d / 0.5, self.falloff, self.k)
        return 1.0 - vals if self.invert else vals


class Expression(Field):
    """Evaluate a numpy expression of ``u`` and ``v`` (both 0..1).

    Example: ``Expression("0.5 + 0.5*sin(6.28*u)")``
    """

    _NS = {name: getattr(np, name) for name in
           ("sin", "cos", "tan", "exp", "log", "sqrt", "abs",
            "minimum", "maximum", "clip", "pi", "hypot", "arctan2")}

    def __init__(self, expr: str):
        super().__init__(expr=expr)
        self.expr = expr
        self._code = compile(expr, "<field-expr>", "eval")

    def sample(self, uv):
        ns = dict(self._NS)
        ns["u"] = uv[:, 0]
        ns["v"] = uv[:, 1]
        out = eval(self._code, {"__builtins__": {}}, ns)  # noqa: S307
        return np.broadcast_to(np.asarray(out, dtype=float), uv.shape[0]).copy()


class TextField(Field):
    """Render text (a letter, word, or short phrase) as a field.

    The text is rasterized with PIL onto an offscreen canvas and sampled
    exactly like :class:`ImageField`. By default the glyph area is 1.0
    (strong effect) and the background 0.0 — i.e. "ink means big holes";
    pass ``invert=True`` to flip.

    Great out-of-the-box demo of image convolution without needing an
    uploaded file: modulate cutout sizes with the letter "A".

    text     : the string to render (centered in the unit square).
    bold     : try a bold system font first.
    blur     : edge softness as a fraction of the canvas (0 = hard-edged
               glyph, ~0.05 = soft halo). Implemented as a Gaussian blur
               of the rasterized glyph, so cutout sizes ramp smoothly
               across the letter's outline instead of stepping.
    font     : optional path to a .ttf file; otherwise a default sans
               font is located (DejaVuSans / Arial), falling back to
               PIL's built-in bitmap font.
    """

    _RESOLUTION = 256

    def __init__(self, text: str = "A", invert: bool = False,
                 bold: bool = True, blur: float = 0.0,
                 font: str | None = None):
        super().__init__(text=text, invert=invert, bold=bold, blur=blur,
                         font=font)
        self.text = text
        self.invert = invert
        self.blur = float(blur)
        lum = _render_text(text, self._RESOLUTION, bold=bold,
                           font_path=font)
        if self.blur > 0:
            lum = _gaussian_blur(lum, self.blur * self._RESOLUTION)
        self._lum = lum
        self._sampler = ImageField(self._lum, invert=invert, fit="contain",
                                   bg=1.0 if invert else 0.0)

    def sample(self, uv):
        return self._sampler.sample(uv)


def _gaussian_blur(img: np.ndarray, sigma_px: float) -> np.ndarray:
    """Separable Gaussian blur (numpy-only, no scipy dependency)."""
    if sigma_px <= 0:
        return img
    radius = max(1, int(round(3 * sigma_px)))
    x = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (x / sigma_px) ** 2)
    kernel /= kernel.sum()
    # Pad with edge values so the border doesn't darken
    padded = np.pad(img, radius, mode="edge")
    # Convolve rows then columns
    out = np.apply_along_axis(
        lambda r: np.convolve(r, kernel, mode="valid"), 1, padded)
    out = np.apply_along_axis(
        lambda c: np.convolve(c, kernel, mode="valid"), 0, out)
    return out


def _find_font(size: int, bold: bool, font_path: str | None):
    """Locate a scalable font, trying common cross-platform names."""
    from PIL import ImageFont
    candidates = []
    if font_path:
        candidates.append(font_path)
    if bold:
        candidates += ["DejaVuSans-Bold.ttf", "arialbd.ttf",
                       "Arial Bold.ttf", "seguisb.ttf"]
    candidates += ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_text(text: str, resolution: int, bold: bool = True,
                 font_path: str | None = None) -> np.ndarray:
    """Rasterize text to a (H, W) luminance array: glyph=1, background=0.

    The glyph is scaled to fill ~80% of the canvas and centered."""
    from PIL import Image, ImageDraw

    if not text:
        return np.zeros((resolution, resolution))

    # Render large, then crop to the glyph bbox and paste centered.
    probe_size = resolution * 2
    font = _find_font(probe_size, bold, font_path)
    canvas = Image.new("L", (probe_size * max(1, len(text)), probe_size * 2), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((canvas.width // 2, canvas.height // 2), text,
              fill=255, font=font, anchor="mm")
    bbox = canvas.getbbox()
    if bbox is None:
        return np.zeros((resolution, resolution))
    glyph = canvas.crop(bbox)

    # Fit into 80% of a square canvas, preserving aspect
    margin = 0.8
    scale = margin * resolution / max(glyph.width, glyph.height)
    glyph = glyph.resize((max(1, round(glyph.width * scale)),
                          max(1, round(glyph.height * scale))))
    out = Image.new("L", (resolution, resolution), 0)
    out.paste(glyph, ((resolution - glyph.width) // 2,
                      (resolution - glyph.height) // 2))
    return np.asarray(out, dtype=float) / 255.0


class ImageField(Field):
    """Sample an image's luminance as a field over the unit square.

    ``image`` may be a file path, a PIL Image, or an (H, W[, C]) numpy
    array. By convention, white = 1.0 and black = 0.0; pass
    ``invert=True`` for "dark ink means strong effect" semantics
    (usually what you want for logos drawn in black on white).

    ``fit`` controls how the (possibly non-square) image maps onto the
    unit square:

    * ``"stretch"`` — fill the square, ignoring aspect ratio.
    * ``"contain"`` — preserve aspect ratio, letterboxing with ``bg``.
    """

    def __init__(self, image, invert: bool = False, fit: str = "contain",
                 bg: float = 0.0, smooth: bool = True):
        super().__init__(invert=invert, fit=fit, bg=bg, smooth=smooth)
        self.invert = invert
        self.fit = fit
        self.bg = float(bg)
        self.smooth = smooth
        self._lum = _to_luminance(image)  # (H, W) float array in 0..1

    def sample(self, uv):
        h, w = self._lum.shape
        u = uv[:, 0].copy()
        v = uv[:, 1].copy()

        outside = np.zeros(uv.shape[0], dtype=bool)
        if self.fit == "contain":
            # Center the image in the square preserving aspect ratio
            aspect = w / h
            if aspect >= 1.0:  # wide image: full u span, reduced v span
                span_u, span_v = 1.0, 1.0 / aspect
            else:              # tall image: full v span, reduced u span
                span_u, span_v = aspect, 1.0
            u = (u - (1 - span_u) / 2) / span_u
            v = (v - (1 - span_v) / 2) / span_v
            outside = (u < 0) | (u > 1) | (v < 0) | (v > 1)

        # v=0 is the bottom of the pattern but row 0 is the top of the image
        px = np.clip(u, 0, 1) * (w - 1)
        py = (1.0 - np.clip(v, 0, 1)) * (h - 1)

        if self.smooth:
            vals = _bilinear(self._lum, px, py)
        else:
            vals = self._lum[np.round(py).astype(int), np.round(px).astype(int)]

        if self.invert:
            vals = 1.0 - vals
        vals = np.where(outside, self.bg, vals)
        return vals


def _to_luminance(image) -> np.ndarray:
    """Convert path / PIL image / ndarray to a (H, W) float array in 0..1."""
    arr = None
    if isinstance(image, np.ndarray):
        arr = image.astype(float)
        if arr.max() > 1.0:
            arr = arr / 255.0
    else:
        from PIL import Image
        if isinstance(image, Image.Image):
            img = image
        else:
            img = Image.open(image)
        img = img.convert("L")
        arr = np.asarray(img, dtype=float) / 255.0
    if arr.ndim == 3:
        # Rec. 601 luminance
        arr = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    return arr


def _bilinear(img: np.ndarray, px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of img at fractional pixel coords."""
    h, w = img.shape
    x0 = np.clip(np.floor(px).astype(int), 0, w - 1)
    y0 = np.clip(np.floor(py).astype(int), 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    fx = px - x0
    fy = py - y0
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return top * (1 - fy) + bot * fy
