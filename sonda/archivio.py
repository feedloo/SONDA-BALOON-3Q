"""
Salvataggio dati sulla SD.

Struttura (una cartella nuova per ogni accensione, niente sovrascritture):

    dati/
      volo_001_2026-11-19_10-30-00/
        telemetria.csv        <- una riga ogni INTERVALLO_SENSORI_S secondi
        rs41_xdata_raw.log    <- tutto ciò che arriva dalla RS41, grezzo
        immagini.csv          <- elenco immagini trasmesse con i dati usati
        eventi.log            <- log del programma (errori, trasmissioni...)
        foto/                 <- foto originali a piena risoluzione
        sstv/                 <- immagini impaginate esattamente come trasmesse

Il Pi Zero non ha un orologio con batteria: senza internet l'ora di
sistema all'accensione può essere sbagliata. Per questo ogni riga ha
anche "uptime_s" (secondi dall'avvio, sempre affidabile) e l'ora GPS
della RS41 quando disponibile.

Dopo ogni riga si fa flush+fsync: se la batteria si stacca di colpo
(atterraggio!) al massimo si perde l'ultima riga.
"""

import csv
import os
import re
import shutil
import threading
import time

import config

COLONNE_TELEMETRIA = [
    "ora_sistema_utc", "uptime_s",
    # sensori del Raspberry
    "temp_int_c", "umid_int_pct", "batteria_v", "adc_a0_v",
    # dati RS41 (XDATA)
    "ora_gps", "data_gps", "lat", "lon", "alt_m", "vel_vert", "vel_orizz_kmh",
    "satelliti", "temp_est_c", "umid_est_pct", "press_hpa", "frame_rs41",
    "batt_rs41_v", "rs41_eta_s",
    # stato
    "cpu_temp_c", "immagini_trasmesse",
]

COLONNE_IMMAGINI = ["ora_sistema_utc", "uptime_s", "numero", "modo", "file_foto",
                    "file_sstv", "trasmessa", "durata_tx_s", "alt_m", "lat", "lon",
                    "temp_int_c", "batteria_v"]

_AVVIO = time.monotonic()


def uptime():
    return round(time.monotonic() - _AVVIO, 1)


def ora_sistema():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


class Archivio:
    def __init__(self, radice=None):
        radice = radice or config.CARTELLA_DATI or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dati")
        os.makedirs(radice, exist_ok=True)
        numeri = [int(m.group(1)) for n in os.listdir(radice)
                  if (m := re.match(r"volo_(\d+)", n))]
        n = max(numeri, default=0) + 1
        nome = f"volo_{n:03d}_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
        self.cartella = os.path.join(radice, nome)
        self.foto = os.path.join(self.cartella, "foto")
        self.sstv = os.path.join(self.cartella, "sstv")
        os.makedirs(self.foto)
        os.makedirs(self.sstv)
        self._lock = threading.Lock()
        self._csv = self._apri_csv("telemetria.csv", COLONNE_TELEMETRIA)
        self._csv_img = self._apri_csv("immagini.csv", COLONNE_IMMAGINI)
        self._raw = open(os.path.join(self.cartella, "rs41_xdata_raw.log"),
                         "a", buffering=1, encoding="utf-8")

    def _apri_csv(self, nome, colonne):
        f = open(os.path.join(self.cartella, nome), "a", newline="", encoding="utf-8")
        w = csv.writer(f)
        w.writerow(colonne)
        f.flush()
        return f, w, colonne

    def _scrivi(self, quale, dati):
        f, w, colonne = quale
        with self._lock:
            w.writerow([_fmt(dati.get(c)) for c in colonne])
            f.flush()
            os.fsync(f.fileno())

    def scrivi_telemetria(self, dati):
        d = dict(dati, ora_sistema_utc=ora_sistema(), uptime_s=uptime())
        self._scrivi(self._csv, d)

    def scrivi_immagine(self, dati):
        d = dict(dati, ora_sistema_utc=ora_sistema(), uptime_s=uptime())
        self._scrivi(self._csv_img, d)

    def scrivi_xdata(self, riga):
        with self._lock:
            self._raw.write(f"{ora_sistema()}\t{uptime()}\t{riga}\n")

    def spazio_libero_mb(self):
        return shutil.disk_usage(self.cartella).free / 1e6

    def percorso_log(self):
        return os.path.join(self.cartella, "eventi.log")
