"""
Scatto foto con la Raspberry Pi Camera v1.2 (sensore OV5647), con
REGOLAZIONE AUTOMATICA dell'esposizione.

Due livelli di automatismo:

1. Dentro la camera (libcamera): esposizione automatica (AE), bilanciamento
   del bianco (AWB) e guadagno. Usiamo:
     --metering average   misura su tutta l'immagine (in quota c'è cielo
                          nero + terra/nuvole bianche: "centre" sbaglia spesso)
     --exposure sport     tempi di scatto brevi = meno mosso (il pallone
                          ruota e oscilla di continuo)
   e lasciamo 2.5 s alla camera per "assestarsi" prima dello scatto.

2. Controllo nostro tra uno scatto e l'altro: dopo ogni foto misuriamo la
   luminosità media e quanti pixel sono bruciati (bianchi) o neri, e
   correggiamo la compensazione EV (--ev) per lo scatto successivo.
   Se una foto è molto sbagliata (troppo scura/chiara) la rifacciamo subito
   una volta con la correzione.

Per ogni foto salviamo anche un file .json con i parametri usati
(tempo di esposizione, guadagno, EV, luminosità...), utile dopo il volo.

Usa il programma di sistema `rpicam-still` (o `libcamera-still`): se la
camera si blocca, il processo viene ucciso dopo il timeout e il resto del
programma continua.
"""

import json
import logging
import math
import os
import shutil
import subprocess

from PIL import Image, ImageStat

import config

log = logging.getLogger("camera")


def _programma():
    for prog in ("rpicam-still", "libcamera-still"):
        exe = shutil.which(prog)
        if exe:
            return exe
    return None


class Camera:
    def __init__(self):
        self.ev = float(config.FOTO_EV_INIZIALE)
        self.opzioni_avanzate = True     # se rpicam-still non le accetta, si spengono

    # ------------------------------------------------------------------
    def _comando(self, exe, percorso_jpg, percorso_meta, avanzate):
        cmd = [exe, "-n", "-t", str(config.FOTO_TEMPO_ASSESTAMENTO_MS),
               "--width", str(config.FOTO_LARGHEZZA),
               "--height", str(config.FOTO_ALTEZZA),
               "-q", str(config.FOTO_QUALITA_JPEG), "-o", percorso_jpg]
        if config.FOTO_RUOTA_180:
            cmd += ["--rotation", "180"]
        if avanzate:
            cmd += ["--metering", config.FOTO_METERING,
                    "--exposure", config.FOTO_MODO_ESPOSIZIONE,
                    "--awb", config.FOTO_AWB,
                    "--ev", f"{self.ev:.1f}",
                    "--metadata", percorso_meta]
        return cmd

    def _esegui(self, cmd):
        try:
            r = subprocess.run(cmd, timeout=40, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
            if r.returncode == 0:
                return True, ""
            return False, r.stderr.decode(errors="replace")[-300:]
        except subprocess.TimeoutExpired:
            return False, "camera bloccata (timeout), processo interrotto"
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    @staticmethod
    def misura(img):
        """Luminosità media (0..1) e frazione di pixel bruciati / neri."""
        g = img.convert("L")
        g.thumbnail((320, 240))
        media = ImageStat.Stat(g).mean[0] / 255.0
        hist = g.histogram()
        tot = float(sum(hist)) or 1.0
        bruciati = sum(hist[250:]) / tot
        neri = sum(hist[:6]) / tot
        return media, bruciati, neri

    def _correzione(self, media, bruciati, neri):
        """Di quanti EV correggere per avvicinarsi alla luminosità obiettivo."""
        err = math.log2(config.FOTO_LUMINOSITA_OBIETTIVO / max(media, 0.02))
        if bruciati > config.FOTO_MAX_BRUCIATI:          # troppe zone bianche
            err = min(err, -0.3 - 3 * (bruciati - config.FOTO_MAX_BRUCIATI))
        elif neri > 0.6 and bruciati < 0.01:              # quasi tutto nero
            err = max(err, 0.5)
        return err

    def _aggiorna_ev(self, err):
        nuovo = self.ev + config.FOTO_GUADAGNO_REGOLAZIONE * err
        self.ev = round(min(config.FOTO_EV_MAX, max(config.FOTO_EV_MIN, nuovo)), 1)

    # ------------------------------------------------------------------
    def scatta(self, percorso_jpg):
        """
        Scatta una foto (piena risoluzione, salvata in percorso_jpg).
        Ritorna (immagine_ridotta_per_SSTV, info) oppure (None, info).
        L'immagine restituita è ridotta (~1296x972) per lavorare veloci
        sul Pi Zero; l'originale resta intero sulla SD.
        """
        exe = _programma()
        info = {"ev": None}
        if not exe:
            log.error("nessun programma per la camera trovato (rpicam-still)")
            return None, info
        percorso_meta = os.path.splitext(percorso_jpg)[0] + ".json"

        img = None
        tentativi = 2 if config.FOTO_RISCATTA_SE_SBAGLIATA else 1
        for n in range(tentativi):
            info = {"ev": self.ev if self.opzioni_avanzate else None, "tentativo": n + 1}
            ok, err = self._esegui(self._comando(exe, percorso_jpg, percorso_meta,
                                                 self.opzioni_avanzate))
            if not ok and self.opzioni_avanzate:
                # forse questa versione di rpicam-still non accetta qualche
                # opzione: riprovo con il comando base. Se così funziona, da
                # ora uso solo quello; se fallisce anche il base, il problema
                # è la camera (occupata/scollegata) e le opzioni restano.
                ok_base, err_base = self._esegui(
                    self._comando(exe, percorso_jpg, percorso_meta, False))
                if ok_base:
                    log.warning("opzioni camera non accettate (%s): uso il comando base",
                                err.strip().splitlines()[-1] if err.strip() else "?")
                    self.opzioni_avanzate = False
                    info["ev"] = None
                ok, err = ok_base, err_base
            if not ok:
                log.error("errore camera: %s", err)
                return None, info
            try:
                img = Image.open(percorso_jpg)
                img.draft("RGB", (1296, 972))     # decodifica JPEG ridotta: veloce
                img = img.convert("RGB")
            except Exception as e:
                log.error("foto illeggibile: %s", e)
                return None, info

            media, bruciati, neri = self.misura(img)
            info.update(luminosita=round(media, 3), bruciati=round(bruciati, 3),
                        neri=round(neri, 3))
            info.update(self._leggi_metadati(percorso_meta))
            err_ev = self._correzione(media, bruciati, neri)
            if self.opzioni_avanzate:
                self._aggiorna_ev(err_ev)
            info["ev_prossima"] = self.ev
            log.info("foto: luminosità %.2f, bruciati %.1f%%, EV %s -> %s, "
                     "esposizione %s us, guadagno %s",
                     media, bruciati * 100, info.get("ev"), self.ev,
                     info.get("esposizione_us"), info.get("guadagno"))
            molto_sbagliata = (media < config.FOTO_LUMINOSITA_MIN
                               or media > config.FOTO_LUMINOSITA_MAX
                               or bruciati > 2 * config.FOTO_MAX_BRUCIATI)
            if not (molto_sbagliata and self.opzioni_avanzate and n + 1 < tentativi):
                break
            log.info("foto troppo %s: la rifaccio con EV %.1f",
                     "scura" if media < config.FOTO_LUMINOSITA_MIN else "chiara", self.ev)

        # salvo i parametri di scatto accanto alla foto
        try:
            with open(percorso_meta, "w") as f:
                json.dump(info, f, indent=1)
        except OSError:
            pass
        return img, info

    @staticmethod
    def _leggi_metadati(percorso_meta):
        """Tempo di esposizione, guadagno ecc. scritti da rpicam-still."""
        try:
            with open(percorso_meta) as f:
                m = json.load(f)
        except Exception:
            return {}
        out = {}
        for chiave, nome in (("ExposureTime", "esposizione_us"),
                             ("AnalogueGain", "guadagno"),
                             ("DigitalGain", "guadagno_digitale"),
                             ("Lux", "lux"),
                             ("ColourTemperature", "temperatura_colore_k")):
            v = m.get(chiave)
            if isinstance(v, (int, float)):
                out[nome] = round(v, 2) if isinstance(v, float) else v
        return out


_camera = None


def scatta(percorso_jpg):
    """Compatibilità (usata da verifica.py): ritorna solo l'immagine o None."""
    global _camera
    if _camera is None:
        _camera = Camera()
    img, _ = _camera.scatta(percorso_jpg)
    return img
