"""
Scatto foto con la Raspberry Pi Camera v1.2 (sensore OV5647).

Usa il programma di sistema `rpicam-still` (Raspberry Pi OS Bookworm),
oppure `libcamera-still` (Bullseye) o `raspistill` (vecchio stack).
Lanciare un programma esterno è più robusto in volo: se la camera si
blocca, viene ucciso dopo il timeout e il resto continua a funzionare.
"""

import logging
import shutil
import subprocess

from PIL import Image

import config

log = logging.getLogger("camera")


def scatta(percorso_jpg):
    """Scatta una foto e la salva. Ritorna una PIL.Image o None."""
    for prog in ("rpicam-still", "libcamera-still", "raspistill"):
        exe = shutil.which(prog)
        if not exe:
            continue
        if prog == "raspistill":
            cmd = [exe, "-n", "-t", "1500", "-w", str(config.FOTO_LARGHEZZA),
                   "-h", str(config.FOTO_ALTEZZA), "-q", "90", "-o", percorso_jpg]
            if config.FOTO_RUOTA_180:
                cmd += ["-rot", "180"]
        else:
            # -t 2000: due secondi per far adattare esposizione e bilanciamento
            cmd = [exe, "-n", "-t", "2000", "--width", str(config.FOTO_LARGHEZZA),
                   "--height", str(config.FOTO_ALTEZZA), "-q", "90", "-o", percorso_jpg]
            if config.FOTO_RUOTA_180:
                cmd += ["--rotation", "180"]
        try:
            r = subprocess.run(cmd, timeout=30, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
            if r.returncode == 0:
                img = Image.open(percorso_jpg)
                img.load()
                return img
            log.error("%s errore %d: %s", prog, r.returncode,
                      r.stderr.decode(errors="replace")[-300:])
        except subprocess.TimeoutExpired:
            log.error("%s bloccato, interrotto", prog)
        except Exception as e:
            log.error("errore camera: %s", e)
        return None
    log.error("nessun programma per la camera trovato (rpicam-still)")
    return None
