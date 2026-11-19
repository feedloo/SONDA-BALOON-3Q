"""
Lettura dati dalla Vaisala RS41 tramite la porta XDATA (seriale 3.3 V).

IMPORTANTE - verificate PRIMA del volo cosa esce davvero dalla vostra RS41:
collegate un adattatore USB-seriale 3.3 V al pin XDATA e guardate i dati
(es. `screen /dev/ttyUSB0 9600`). Il formato dipende dal firmware.
Per questo il parser accetta più formati:

  1. Frasi NMEA GPS ($GPGGA / $GNGGA / $GPRMC / $GNRMC)
  2. Coppie chiave=valore, es.  "alt=12345,lat=45.88,lon=11.04,temp=-40.2"
     (separatori , ; spazio, e anche chiave:valore)

Ogni riga ricevuta viene comunque salvata così com'è nel file
rs41_xdata_raw.log, quindi anche se il formato non viene riconosciuto
i dati NON si perdono e potete analizzarli dopo il recupero.
"""

import logging
import re
import threading
import time

import config

log = logging.getLogger("rs41")

# nomi alternativi riconosciuti nelle righe chiave=valore -> nome interno
ALIAS = {
    "alt": "alt_m", "altitude": "alt_m", "height": "alt_m", "h": "alt_m", "quota": "alt_m",
    "lat": "lat", "latitude": "lat",
    "lon": "lon", "lng": "lon", "long": "lon", "longitude": "lon",
    "temp": "temp_est_c", "t": "temp_est_c", "temperature": "temp_est_c",
    "hum": "umid_est_pct", "rh": "umid_est_pct", "humidity": "umid_est_pct",
    "press": "press_hpa", "p": "press_hpa", "pressure": "press_hpa",
    "vs": "vel_vert", "vspeed": "vel_vert", "climb": "vel_vert", "vel_v": "vel_vert",
    "sats": "satelliti", "sat": "satelliti", "numsv": "satelliti",
    "speed": "vel_orizz_kmh", "frame": "frame_rs41", "batt": "batt_rs41_v",
}

CAMPI = ["ora_gps", "data_gps", "lat", "lon", "alt_m", "vel_vert", "vel_orizz_kmh",
         "satelliti", "temp_est_c", "umid_est_pct", "press_hpa", "frame_rs41",
         "batt_rs41_v"]

# chiave = valore  oppure  chiave: valore   (valore numerico o ora hh:mm:ss)
_COPPIA = re.compile(r"([A-Za-z_]+)\s*[=:]\s*(\d\d:\d\d:\d\d|[+-]?\d+(?:\.\d+)?)")


def _nmea_checksum_ok(riga):
    if "*" not in riga:
        return True
    corpo, cs = riga[1:].split("*", 1)
    calc = 0
    for c in corpo:
        calc ^= ord(c)
    try:
        return calc == int(cs[:2], 16)
    except ValueError:
        return False


def _nmea_coord(valore, emisfero):
    if not valore:
        return None
    v = float(valore)
    gradi = int(v / 100)
    deg = gradi + (v - gradi * 100) / 60.0
    return -deg if emisfero in ("S", "W") else deg


def analizza_riga(riga):
    """Ritorna un dizionario con i campi riconosciuti (può essere vuoto)."""
    riga = riga.strip()
    out = {}
    if not riga:
        return out
    try:
        if riga.startswith("$") and _nmea_checksum_ok(riga):
            p = riga.split("*")[0].split(",")
            tipo = p[0][3:]
            if tipo == "GGA" and len(p) >= 10 and p[6] not in ("", "0"):
                out["ora_gps"] = f"{p[1][0:2]}:{p[1][2:4]}:{p[1][4:6]}"
                out["lat"] = _nmea_coord(p[2], p[3])
                out["lon"] = _nmea_coord(p[4], p[5])
                out["satelliti"] = int(p[7]) if p[7] else None
                out["alt_m"] = float(p[9]) if p[9] else None
            elif tipo == "RMC" and len(p) >= 10 and p[2] == "A":
                out["ora_gps"] = f"{p[1][0:2]}:{p[1][2:4]}:{p[1][4:6]}"
                out["lat"] = _nmea_coord(p[3], p[4])
                out["lon"] = _nmea_coord(p[5], p[6])
                if p[7]:
                    out["vel_orizz_kmh"] = float(p[7]) * 1.852
                if len(p[9]) == 6:
                    out["data_gps"] = f"20{p[9][4:6]}-{p[9][2:4]}-{p[9][0:2]}"
            return {k: v for k, v in out.items() if v is not None}

        # formato chiave=valore (o chiave: valore)
        for k, v in _COPPIA.findall(riga):
            k = k.lower()
            if k in ("time", "ora", "utc") and ":" in v:
                out["ora_gps"] = v
                continue
            nome = ALIAS.get(k)
            if nome and ":" not in v:
                out[nome] = int(float(v)) if nome in ("satelliti", "frame_rs41") else float(v)
    except (ValueError, IndexError):
        pass
    return out


class LettoreRS41(threading.Thread):
    """
    Thread che gestisce la seriale XDATA:
      - legge di continuo quello che arriva dalla RS41 (Pi RX) e tiene gli
        ultimi valori riconosciuti;
      - offre scrivi() per mandare i frame XDATA alla RS41 (Pi TX), usato
        da xdata_tx.TrasmettitoreXDATA sulla stessa porta aperta.
    Le righe possono finire con CR, LF o CR+LF.
    """

    def __init__(self, archivio=None):
        super().__init__(daemon=True, name="rs41")
        self.archivio = archivio
        self._lock = threading.Lock()
        self._lock_ser = threading.Lock()
        self._ser = None
        self._dati = {}
        self._ultimo_agg = None       # time.monotonic() dell'ultimo dato valido
        self._alt_prec = None         # per calcolare la velocità verticale
        self.righe_ricevute = 0

    def scrivi(self, dati):
        """Scrive byte sulla seriale. Ritorna False se la porta non è aperta."""
        with self._lock_ser:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write(dati)
                ser.flush()
                return True
            except Exception as e:
                log.warning("scrittura seriale fallita: %s", e)
                return False

    def run(self):
        try:
            import serial
        except ImportError:
            log.error("pyserial non installato: niente dati RS41")
            return
        while True:
            try:
                with serial.Serial(config.SERIALE_PORTA, config.SERIALE_BAUD,
                                   timeout=1) as ser:
                    with self._lock_ser:
                        self._ser = ser
                    log.info("seriale %s aperta a %d baud",
                             config.SERIALE_PORTA, config.SERIALE_BAUD)
                    buffer = b""
                    while True:
                        buffer += ser.read(max(1, ser.in_waiting))
                        if len(buffer) > 4096:          # niente fine riga: svuoto
                            self._riga(buffer)
                            buffer = b""
                        while True:
                            pos = [p for p in (buffer.find(b"\r"), buffer.find(b"\n")) if p >= 0]
                            if not pos:
                                break
                            fine = min(pos)
                            self._riga(buffer[:fine])
                            buffer = buffer[fine + 1:]
            except Exception as e:
                log.error("errore seriale: %s (riprovo tra 5 s)", e)
            finally:
                with self._lock_ser:
                    self._ser = None
            time.sleep(5)

    def _riga(self, b):
        riga = b.decode("ascii", errors="replace").strip()
        if not riga:
            return
        self.righe_ricevute += 1
        if self.archivio is not None:
            self.archivio.scrivi_xdata(riga, "RX")
        self._aggiorna(analizza_riga(riga))

    def _aggiorna(self, nuovi):
        if not nuovi:
            return
        ora = time.monotonic()
        with self._lock:
            # velocità verticale calcolata se la RS41 non la manda
            if "alt_m" in nuovi and "vel_vert" not in nuovi:
                if self._alt_prec is not None:
                    a0, t0 = self._alt_prec
                    if ora - t0 >= 2:
                        nuovi["vel_vert"] = (nuovi["alt_m"] - a0) / (ora - t0)
                        self._alt_prec = (nuovi["alt_m"], ora)
                else:
                    self._alt_prec = (nuovi["alt_m"], ora)
            self._dati.update(nuovi)
            self._ultimo_agg = ora

    def stato(self):
        """Copia degli ultimi dati + da quanti secondi non arrivano dati."""
        with self._lock:
            d = {k: self._dati.get(k) for k in CAMPI}
            eta = None if self._ultimo_agg is None else time.monotonic() - self._ultimo_agg
        d["rs41_eta_s"] = round(eta, 1) if eta is not None else None
        d["rs41_vecchi"] = eta is None or eta > config.RS41_TIMEOUT_DATI_S
        return d
