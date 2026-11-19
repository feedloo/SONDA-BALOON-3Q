"""
Codificatore SSTV (Robot36, Martin M1, PD120) scritto con numpy.

L'immagine viene trasformata in una lista di "segmenti" (frequenza, durata),
poi i segmenti vengono sintetizzati in un'onda audio a fase continua.
Quell'audio è quello che un ricevitore FM sintonizzato a 433.500 MHz
"sente" e che MMSSTV / QSSTV / app Robot36 decodificano.

Per efficienza (il Pi Zero è lento) i pixel non sono generati uno alla volta
in Python ma a blocchi con numpy, e l'audio è prodotto a pezzi da 1 secondo
così non si riempie la RAM.
"""

import numpy as np

# Frequenze standard SSTV (Hz)
F_SYNC = 1200
F_NERO = 1500
F_BIANCO = 2300
F_VIS_LEADER = 1900
F_VIS_1 = 1100
F_VIS_0 = 1300

# Modi supportati: codice VIS e risoluzione
MODI = {
    "robot36": {"vis": 0x08, "larghezza": 320, "altezza": 240, "durata_s": 36},
    "martin1": {"vis": 0x2C, "larghezza": 320, "altezza": 256, "durata_s": 114},
    "pd120":   {"vis": 0x5F, "larghezza": 640, "altezza": 496, "durata_s": 126},
}

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", "/": "-..-.",
}


class Segmenti:
    """Accumula segmenti audio: frequenza (Hz), durata (ms), ampiezza (0/1)."""

    def __init__(self):
        self._f, self._d, self._a = [], [], []

    def tono(self, freq, ms, ampiezza=1.0):
        self._f.append(np.array([freq], dtype=np.float64))
        self._d.append(np.array([ms], dtype=np.float64))
        self._a.append(np.array([ampiezza], dtype=np.float32))

    def silenzio(self, ms):
        self.tono(0.0, ms, 0.0)

    def pixel(self, valori_0_255, ms_per_pixel):
        """Aggiunge una riga di pixel (array di valori 0..255)."""
        v = np.clip(np.asarray(valori_0_255, dtype=np.float64), 0, 255)
        self._f.append(F_NERO + v * (F_BIANCO - F_NERO) / 255.0)
        self._d.append(np.full(v.shape, ms_per_pixel, dtype=np.float64))
        self._a.append(np.ones(v.shape, dtype=np.float32))

    def unisci(self, altri):
        self._f += altri._f
        self._d += altri._d
        self._a += altri._a

    def array(self):
        return (np.concatenate(self._f), np.concatenate(self._d),
                np.concatenate(self._a))

    def durata_s(self):
        return float(sum(d.sum() for d in self._d)) / 1000.0


# ---------------------------------------------------------------------------
# Conversione colori (formule usate da MMSSTV / QSSTV, range "studio" 16-235)
# ---------------------------------------------------------------------------
def _rgb_in_yuv(img_rgb):
    a = np.asarray(img_rgb, dtype=np.float64)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    y = 16.0 + (65.738 * r + 129.057 * g + 25.064 * b) / 256.0
    ry = 128.0 + (112.439 * r - 94.154 * g - 18.285 * b) / 256.0   # R-Y (Cr)
    by = 128.0 + (-37.945 * r - 74.494 * g + 112.439 * b) / 256.0  # B-Y (Cb)
    return y, ry, by


def _vis(seg, codice):
    seg.tono(F_VIS_LEADER, 300)
    seg.tono(F_SYNC, 10)
    seg.tono(F_VIS_LEADER, 300)
    seg.tono(F_SYNC, 30)                     # bit di start
    uni = 0
    for i in range(7):                       # 7 bit, LSB per primo
        bit = (codice >> i) & 1
        uni += bit
        seg.tono(F_VIS_1 if bit else F_VIS_0, 30)
    seg.tono(F_VIS_1 if uni % 2 else F_VIS_0, 30)   # parità pari
    seg.tono(F_SYNC, 30)                     # bit di stop


def _robot36(seg, img):
    y, ry, by = _rgb_in_yuv(img)
    for riga in range(240):
        seg.tono(F_SYNC, 9.0)
        seg.tono(F_NERO, 3.0)
        seg.pixel(y[riga], 88.0 / 320)
        coppia = riga - (riga % 2)
        if riga % 2 == 0:          # righe pari: R-Y, separatore 1500 Hz
            croma = (ry[coppia] + ry[coppia + 1]) / 2
            seg.tono(F_NERO, 4.5)
        else:                      # righe dispari: B-Y, separatore 2300 Hz
            croma = (by[coppia] + by[coppia + 1]) / 2
            seg.tono(F_BIANCO, 4.5)
        seg.tono(F_VIS_LEADER, 1.5)
        croma = croma.reshape(160, 2).mean(axis=1)   # mezza risoluzione orizz.
        seg.pixel(croma, 44.0 / 160)


def _martin1(seg, img):
    a = np.asarray(img, dtype=np.float64)
    t_px = 146.432 / 320
    for riga in range(256):
        seg.tono(F_SYNC, 4.862)
        seg.tono(F_NERO, 0.572)
        for canale in (1, 2, 0):   # ordine Martin: verde, blu, rosso
            seg.pixel(a[riga, :, canale], t_px)
            seg.tono(F_NERO, 0.572)


def _pd120(seg, img):
    y, ry, by = _rgb_in_yuv(img)
    t_px = 0.19
    for riga in range(0, 496, 2):
        seg.tono(F_SYNC, 20.0)
        seg.tono(F_NERO, 2.08)
        seg.pixel(y[riga], t_px)
        seg.pixel((ry[riga] + ry[riga + 1]) / 2, t_px)
        seg.pixel((by[riga] + by[riga + 1]) / 2, t_px)
        seg.pixel(y[riga + 1], t_px)


def segmenti_immagine(img, modo):
    """img: PIL.Image RGB già della risoluzione giusta per il modo."""
    m = MODI[modo]
    if img.size != (m["larghezza"], m["altezza"]):
        raise ValueError(f"Immagine {img.size}, il modo {modo} vuole "
                         f"{m['larghezza']}x{m['altezza']}")
    seg = Segmenti()
    _vis(seg, m["vis"])
    {"robot36": _robot36, "martin1": _martin1, "pd120": _pd120}[modo](seg, img.convert("RGB"))
    return seg


def segmenti_cw(testo, wpm=20, tono=800):
    """Testo in Morse (es. 'DE IW3BAU'). Punto = 1200/wpm ms."""
    seg = Segmenti()
    punto = 1200.0 / wpm
    for parola in testo.upper().split():
        for lettera in parola:
            codice = MORSE.get(lettera)
            if not codice:
                continue
            for simbolo in codice:
                seg.tono(tono, punto if simbolo == "." else 3 * punto)
                seg.silenzio(punto)
            seg.silenzio(2 * punto)          # 3 punti tra lettere
        seg.silenzio(4 * punto)              # 7 punti tra parole
    return seg


def sintetizza(seg, fs=48000, blocco_s=1.0):
    """
    Generatore: restituisce l'audio (float32, -1..1) a blocchi.
    La fase è continua tra un segmento e l'altro (niente "click"),
    e i tempi sono calcolati in modo assoluto, quindi non si accumula
    deriva anche con centinaia di migliaia di pixel.
    """
    freq, durata_ms, ampiezza = seg.array()
    fine = np.cumsum(durata_ms) / 1000.0         # istante di fine di ogni segmento
    totale = int(np.ceil(fine[-1] * fs))
    passo = int(blocco_s * fs)
    fase = 0.0
    for inizio in range(0, totale, passo):
        n = np.arange(inizio, min(inizio + passo, totale), dtype=np.float64)
        idx = np.searchsorted(fine, n / fs, side="right")
        np.minimum(idx, len(fine) - 1, out=idx)
        inc = 2.0 * np.pi * freq[idx] / fs
        fasi = fase + np.cumsum(inc)
        fase = float(fasi[-1] % (2.0 * np.pi))
        yield (np.sin(fasi) * ampiezza[idx]).astype(np.float32)
