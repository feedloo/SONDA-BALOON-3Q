"""
Scatto foto con la Raspberry Pi Camera v1.2 (sensore OV5647), con
REGOLAZIONE AUTOMATICA dell'esposizione e TEMPO DI SCATTO CORTO (niente mosso).

Come funziona
-------------
1. Primo scatto in automatico (libcamera): la camera misura la luce da sola
   (metering average, modo "sport" = tempi brevi, EV di partenza negativo
   per non bruciare). Dai metadati leggiamo tempo di scatto e guadagno usati.

2. Dagli scatti successivi l'esposizione la decidiamo NOI (modo manuale):
     esposizione totale  E = tempo_di_scatto [us] x guadagno
   - il tempo di scatto è il PIÙ CORTO possibile: al massimo
     FOTO_SHUTTER_MAX_US (1/1000 s) -> foto non mosse anche se il pallone
     ruota e oscilla;
   - se la luce non basta si alza il guadagno (fino a FOTO_GAIN_MAX), mai
     il tempo oltre il limite;
   - dopo ogni foto misuriamo luminosità media e pixel bruciati e
     correggiamo E per la foto successiva (obiettivo: immagine NON troppo
     luminosa, cielo e nuvole senza zone bianche piatte).
   Se una foto è molto sbagliata viene rifatta subito una volta.

3. Sicurezze: se lo scatto manuale fallisce si torna all'automatico; se
   anche le opzioni dell'automatico non sono accettate da rpicam-still si
   usa il comando base. La camera non blocca mai il resto del programma.

Per ogni foto si salva un .json con modo, tempo di scatto, guadagno,
luminosità... utile per studiare il volo dopo il recupero.
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


def _cfg(nome, default):
    return getattr(config, nome, default)


class Camera:
    def __init__(self):
        self.ev = float(_cfg("FOTO_EV_INIZIALE", 0.0))
        self.avanzate = True                                  # opzioni libcamera ok?
        self.manuale = bool(_cfg("FOTO_ESPOSIZIONE_MANUALE", True))
        self.E = None             # esposizione per il prossimo scatto manuale (us x guadagno)
        self.sh_max = float(_cfg("FOTO_SHUTTER_MAX_US", 1000))
        self.sh_min = float(_cfg("FOTO_SHUTTER_MIN_US", 60))
        self.g_max = float(_cfg("FOTO_GAIN_MAX", 8.0))

    # ------------------------------------------------------------------
    def parametri_manuali(self):
        """Da E ricava (tempo_us, guadagno): tempo il più corto possibile."""
        E = min(max(self.E, self.sh_min), self.sh_max * self.g_max)
        tempo = min(E, self.sh_max)
        guadagno = min(max(E / tempo, 1.0), self.g_max)
        return int(round(tempo)), round(guadagno, 2)

    def _comando(self, exe, jpg, meta, modo):
        cmd = [exe, "-n", "-t", str(config.FOTO_TEMPO_ASSESTAMENTO_MS),
               "--width", str(config.FOTO_LARGHEZZA),
               "--height", str(config.FOTO_ALTEZZA),
               "-q", str(config.FOTO_QUALITA_JPEG), "-o", jpg]
        if config.FOTO_RUOTA_180:
            cmd += ["--rotation", "180"]
        if modo == "manuale":
            tempo, guadagno = self.parametri_manuali()
            cmd += ["--shutter", str(tempo), "--gain", f"{guadagno:.2f}",
                    "--awb", config.FOTO_AWB, "--metadata", meta]
        elif modo == "auto":
            cmd += ["--metering", config.FOTO_METERING,
                    "--exposure", config.FOTO_MODO_ESPOSIZIONE,
                    "--awb", config.FOTO_AWB, "--ev", f"{self.ev:.1f}",
                    "--metadata", meta]
        return cmd

    @staticmethod
    def _esegui(cmd):
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

    def _scatto_con_ripieghi(self, exe, jpg, meta):
        """Prova manuale -> auto -> base. Ritorna (ok, modo_usato, errore)."""
        if self.manuale and self.E is not None:
            modo = "manuale"
        elif self.avanzate:
            modo = "auto"
        else:
            modo = "base"
        ok, err = self._esegui(self._comando(exe, jpg, meta, modo))
        if ok:
            return True, modo, ""
        if modo == "manuale":
            ok2, err2 = self._esegui(self._comando(exe, jpg, meta, "auto"))
            if ok2:
                log.warning("scatto manuale non riuscito (%s): uso l'automatico",
                            _ultima_riga(err))
                self.manuale = False
                return True, "auto", ""
            modo, err = "auto", err2
        if modo == "auto":
            ok3, err3 = self._esegui(self._comando(exe, jpg, meta, "base"))
            if ok3:
                log.warning("opzioni camera non accettate (%s): uso il comando base",
                            _ultima_riga(err))
                self.avanzate = False
                self.manuale = False
                return True, "base", ""
            err = err3
        return False, modo, err

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

    @staticmethod
    def correzione(media, bruciati, neri):
        """Di quanti 'stop' di luminosità correggere (negativo = più scura)."""
        err = math.log2(config.FOTO_LUMINOSITA_OBIETTIVO / max(media, 0.02))
        if bruciati > config.FOTO_MAX_BRUCIATI:          # zone bianche: scurisco
            err = min(err, -0.3 - 4 * (bruciati - config.FOTO_MAX_BRUCIATI))
        elif neri > 0.6 and bruciati < 0.01:              # quasi tutto nero
            err = max(err, 0.5)
        return max(-3.0, min(3.0, err))

    # ------------------------------------------------------------------
    def scatta(self, percorso_jpg):
        """
        Scatta una foto (piena risoluzione, salvata in percorso_jpg).
        Ritorna (immagine_ridotta_per_SSTV, info) oppure (None, info).
        """
        exe = _programma()
        info = {}
        if not exe:
            log.error("nessun programma per la camera trovato (rpicam-still)")
            return None, info
        meta = os.path.splitext(percorso_jpg)[0] + ".json"
        img = None
        tentativi = 2 if config.FOTO_RISCATTA_SE_SBAGLIATA else 1
        k = config.FOTO_GUADAGNO_REGOLAZIONE

        for n in range(tentativi):
            E_prima = self.E
            ok, modo, err = self._scatto_con_ripieghi(exe, percorso_jpg, meta)
            info = {"modo": modo, "tentativo": n + 1}
            if not ok:
                log.error("errore camera: %s", err)
                return None, info
            try:
                img = Image.open(percorso_jpg)
                img.draft("RGB", (1296, 972))      # decodifica JPEG ridotta: veloce
                img = img.convert("RGB")
            except Exception as e:
                log.error("foto illeggibile: %s", e)
                return None, info

            media, bruciati, neri = self.misura(img)
            m = self._leggi_metadati(meta)
            info.update(m)
            info.update(luminosita=round(media, 3), bruciati=round(bruciati, 3),
                        neri=round(neri, 3))
            err_lum = self.correzione(media, bruciati, neri)

            # esposizione effettivamente usata in questo scatto
            if modo == "manuale":
                tempo, guadagno = self.parametri_manuali()
                info.update(esposizione_us=tempo, guadagno=guadagno)
                E_usata = tempo * guadagno
            elif m.get("esposizione_us") and m.get("guadagno"):
                E_usata = m["esposizione_us"] * m["guadagno"]
            else:
                E_usata = None
            if modo == "auto":
                info["ev"] = self.ev
                self.ev = round(min(config.FOTO_EV_MAX,
                                    max(config.FOTO_EV_MIN, self.ev + k * err_lum)), 1)

            # nuova esposizione per lo scatto successivo (modo manuale):
            # la luminosità dell'immagine cresce più piano dell'esposizione
            # (curva gamma), quindi ~2 stop di esposizione per 1 di luminosità
            if self.manuale and E_usata:
                E_nuova = E_usata * 2 ** (2.0 * k * err_lum)
                self.E = min(max(E_nuova, self.sh_min), self.sh_max * self.g_max)
                t_next, g_next = self.parametri_manuali()
                info.update(prossimo_tempo_us=t_next, prossimo_guadagno=g_next)

            log.info("foto [%s]: luminosità %.2f, bruciati %.1f%%, scatto %s us, "
                     "guadagno %s -> prossima %s us x %s",
                     modo, media, bruciati * 100, info.get("esposizione_us"),
                     info.get("guadagno"), info.get("prossimo_tempo_us"),
                     info.get("prossimo_guadagno"))

            molto_sbagliata = (media < config.FOTO_LUMINOSITA_MIN
                               or media > config.FOTO_LUMINOSITA_MAX
                               or bruciati > 2 * config.FOTO_MAX_BRUCIATI)
            # rifaccio solo se la correzione cambia davvero qualcosa
            cambia = (self.E is not None and (E_prima is None
                                              or abs(math.log2(self.E / E_prima)) > 0.15)) \
                or modo == "auto"
            if not (molto_sbagliata and cambia and n + 1 < tentativi):
                break
            log.info("foto troppo %s: la rifaccio subito",
                     "scura" if media < config.FOTO_LUMINOSITA_MIN else "chiara")

        try:
            with open(meta, "w") as f:
                json.dump(info, f, indent=1)
        except OSError:
            pass
        return img, info

    @staticmethod
    def _leggi_metadati(meta):
        """Tempo di esposizione, guadagno ecc. scritti da rpicam-still."""
        try:
            with open(meta) as f:
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


def _ultima_riga(testo):
    righe = testo.strip().splitlines()
    return righe[-1] if righe else "?"


_camera = None


def scatta(percorso_jpg):
    """Compatibilità (usata da verifica.py): ritorna solo l'immagine o None."""
    global _camera
    if _camera is None:
        _camera = Camera()
    img, _ = _camera.scatta(percorso_jpg)
    return img
