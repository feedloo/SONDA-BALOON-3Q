"""
Impaginazione dell'immagine da trasmettere in SSTV.

    +--------------------------------------------------+
    | [LOGO]  IW3BAU                SONDA 3Q   #012    |  <- barra in alto
    |                               12:34:56 UTC       |
    |                                                  |
    |              FOTO DELLA CAMERA                   |
    |                                                  |
    | ALT 12345 m                     BATT 11.8 V      |  <- pannello dati
    | LAT 45.88912 N                  T.INT 21.0°C     |
    | LON 11.04012 E                  T.EST -45.0°C    |
    +--------------------------------------------------+

Regole per SSTV: testi GRANDI, in grassetto, ad alto contrasto, con bordo
nero. Il rumore radio "sporca" i dettagli piccoli e il Robot36 ha i colori
a metà risoluzione, quindi niente scritte colorate sottili.
"""

import os
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config
import codifica_sstv as sstv

_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

GIALLO = (255, 221, 0)
BIANCO = (255, 255, 255)
AZZURRO = (120, 200, 255)
ARANCIO = (255, 150, 0)
ROSSO = (255, 60, 60)
VERDE = (90, 255, 90)
BLU_BARRA = (8, 32, 96, 235)
NERO_PANNELLO = (0, 0, 0, 185)

_cache_font = {}


def _font(px):
    px = max(8, int(round(px)))
    if px not in _cache_font:
        f = None
        for p in _FONT_BOLD:
            if os.path.exists(p):
                f = ImageFont.truetype(p, px)
                break
        if f is None:
            try:
                f = ImageFont.load_default(px)      # Pillow >= 10.1
            except TypeError:
                f = ImageFont.load_default()
        _cache_font[px] = f
    return _cache_font[px]


def _testo(draw, xy, txt, px, colore=BIANCO, ancora="la"):
    draw.text(xy, txt, font=_font(px), fill=colore, anchor=ancora,
              stroke_width=max(1, int(px / 12)), stroke_fill=(0, 0, 0))


def _carica_logo(percorso, altezza_max, larghezza_max):
    if not os.path.isabs(percorso):
        percorso = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), percorso)
    if not os.path.exists(percorso):
        return None
    try:
        logo = Image.open(percorso).convert("RGBA")
        logo.thumbnail((larghezza_max, altezza_max), Image.LANCZOS)
        return logo
    except Exception:
        return None


def _fmt(valore, formato, unita="", nd="--"):
    if valore is None:
        return nd
    try:
        return f"{valore:{formato}}{unita}"
    except (ValueError, TypeError):
        return nd


def _coord(valore, pos, neg):
    if valore is None:
        return "--"
    return f"{abs(valore):.5f} {pos if valore >= 0 else neg}"


def impagina(foto, dati, numero, modo=None):
    """
    foto  : PIL.Image (o None se la camera non va)
    dati  : dizionario con i valori (vedi main.py -> dati_per_immagine)
    numero: contatore immagini
    Ritorna una PIL.Image RGB della dimensione giusta per il modo SSTV.
    """
    modo = modo or config.MODO_SSTV
    W, H = sstv.MODI[modo]["larghezza"], sstv.MODI[modo]["altezza"]
    s = W / 320.0                     # fattore di scala (PD120 = 2x)

    # ---- sfondo: foto ritagliata a riempire tutto il riquadro ----
    if foto is not None:
        base = ImageOps.fit(foto.convert("RGB"), (W, H), Image.LANCZOS)
    else:
        base = Image.new("RGB", (W, H), (40, 40, 40))
        d0 = ImageDraw.Draw(base)
        _testo(d0, (W / 2, H / 2), "NO CAMERA", 22 * s, ARANCIO, "mm")
    img = base.convert("RGBA")

    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    # ---- barra in alto ----
    h_barra = int(36 * s)
    d.rectangle([0, 0, W, h_barra], fill=BLU_BARRA)
    d.line([0, h_barra, W, h_barra], fill=GIALLO + (255,), width=max(1, int(2 * s)))

    # ---- pannello dati in basso ----
    h_pann = int(64 * s)
    y_p = H - h_pann
    d.rectangle([0, y_p, W, H], fill=NERO_PANNELLO)
    img = Image.alpha_composite(img, over)
    d = ImageDraw.Draw(img)

    # logo (o nome della scuola se il logo non c'è)
    x = int(4 * s)
    logo = _carica_logo(config.LOGO_PATH, h_barra - int(6 * s), int(90 * s))
    if logo is not None:
        img.alpha_composite(logo, (x, (h_barra - logo.height) // 2))
        x += logo.width + int(6 * s)

    # nominativo: l'elemento più importante, grande e giallo
    _testo(d, (x, h_barra / 2), config.CALLSIGN, 24 * s, GIALLO, "lm")

    # destra della barra: missione/numero e ora
    xr = W - int(4 * s)
    riga1 = f"{config.NOME_MISSIONE}  #{numero:03d}"
    if logo is None:
        riga1 = config.NOME_SCUOLA
    _testo(d, (xr, int(10 * s)), riga1, 10 * s, BIANCO, "rm")
    ora = dati.get("ora_utc") or time.strftime("%H:%M:%S", time.gmtime())
    _testo(d, (xr, int(25 * s)), f"{ora} UTC", 12 * s, BIANCO, "rm")

    # ---- dati ----
    vecchi = dati.get("rs41_vecchi", True)
    col_rs = ARANCIO if vecchi else BIANCO   # arancio = dati RS41 non aggiornati

    batt = dati.get("batteria_v")
    if batt is None:
        col_batt = ARANCIO
    elif batt >= config.BATTERIA_OK_V:
        col_batt = VERDE
    elif batt >= config.BATTERIA_BASSA_V:
        col_batt = GIALLO
    else:
        col_batt = ROSSO

    xl = int(5 * s)
    xd = W - int(5 * s)
    y1 = y_p + int(14 * s)
    y2 = y_p + int(36 * s)
    y3 = y_p + int(53 * s)

    # riga 1, grande: altitudine e batteria
    _testo(d, (xl, y1), "ALT", 11 * s, AZZURRO, "lm")
    _testo(d, (xl + int(28 * s), y1), _fmt(dati.get("alt_m"), ".0f", " m"),
           19 * s, col_rs, "lm")
    _testo(d, (xd, y1), _fmt(batt, ".1f", " V"), 17 * s, col_batt, "rm")
    xe = int(192 * s)                 # colonna delle etichette di destra
    _testo(d, (xe, y1), "BATT", 11 * s, AZZURRO, "lm")

    # righe 2-3: coordinate a sinistra, temperature a destra
    px = 13 * s
    _testo(d, (xl, y2), "LAT", 10 * s, AZZURRO, "lm")
    _testo(d, (xl + int(28 * s), y2), _coord(dati.get("lat"), "N", "S"), px, col_rs, "lm")
    _testo(d, (xl, y3), "LON", 10 * s, AZZURRO, "lm")
    _testo(d, (xl + int(28 * s), y3), _coord(dati.get("lon"), "E", "W"), px, col_rs, "lm")

    _testo(d, (xe, y2), "T.INT", 10 * s, AZZURRO, "lm")
    _testo(d, (xd, y2), _fmt(dati.get("temp_int_c"), ".0f", "°C"), px, BIANCO, "rm")
    if dati.get("temp_est_c") is not None:
        _testo(d, (xe, y3), "T.EST", 10 * s, AZZURRO, "lm")
        _testo(d, (xd, y3), _fmt(dati.get("temp_est_c"), ".0f", "°C"), px, col_rs, "rm")
    else:
        _testo(d, (xe, y3), "V.V.", 10 * s, AZZURRO, "lm")
        _testo(d, (xd, y3), _fmt(dati.get("vel_vert"), "+.1f", " m/s"), px, col_rs, "rm")

    return img.convert("RGB")
