"""
Trasmissione XDATA verso la RS41 (emulazione strumento OIF411).

Il Raspberry manda sulla seriale (Pi TX, GPIO14 pin 8 -> XDATA IN della RS41)
un frame al secondo come quelli di un sensore OIF411. La RS41 li inserisce
nella sua telemetria e li trasmette a terra: così temperatura interna e
batteria della scatola si ricevono anche con i decoder della RS41
(es. radiosonde_auto_rx / SondeHub), indipendentemente dall'SSTV.

Il protocollo (encode_*, build_frame, verify_frame) è IDENTICO a quello di
tx_xdata_frame.py / tx_xdata_frame_sensors.py già provato con la RS41.
Cambia solo la fonte dei valori: ora vengono dai sensori del programma
principale (DHT11 via kernel, ADS1115 calibrato), con lo stesso schema
"valore fresco oppure fallback".

Formato: xdata=0501TTTTRRRRRBBCCC00\\r   (27 byte)
    05   = ID strumento OIF411         01   = posizione nella catena
    TTTT = temperatura (bit15 segno, |T|*100)
    RRRRR= radiazione (uint20)          BB   = batteria (0.1 V)
    CCC  = corrente mA (uint12)         00   = non usato
"""

import logging
import threading
import time

import config

log = logging.getLogger("xdata_tx")

# =====================================================================
# PROTOCOLLO XDATA  --  IDENTICO a tx_xdata_frame.py, NON MODIFICARE
# =====================================================================

PREAMBLE = "xdata="
TERMINATOR = b"\r"      # single CR (0x0D) as observed on the RS41 stream
FRAME_LEN = 27          # 6 (preamble) + 20 (payload) + 1 (CR)

INST_ID = "05"          # fixed: OIF411 instrument ID
CHAIN_ID = "01"         # fixed: daisychain position


def encode_temp_c(temp_c: float) -> str:
    """Encode temperature as uint16: bit15 = sign, bits14..0 = abs*100.

    0.01 C resolution, 4 hex chars (NOAA/OIF411 method, see xdata-decoding.md).
    """
    magnitude = int(round(abs(temp_c) * 100))
    if magnitude > 0x7FFF:
        raise ValueError(f"temperature {temp_c} C out of range "
                         f"(-327.67..+327.67 C)")
    encoded = (magnitude | 0x8000) if temp_c < 0 else magnitude
    return f"{encoded:04X}"


def encode_radiation(raw: int) -> str:
    """Encode the radiation sensor value as uint20, 5 hex chars."""
    if not 0 <= raw <= 0xFFFFF:
        raise ValueError(f"radiation raw value {raw} out of range "
                         f"(0..1048575, uint20)")
    return f"{raw:05X}"


def encode_battery_v(volts: float) -> str:
    """Encode battery voltage as uint8 x 0.1 V, 2 hex chars (12.3 V -> 7B)."""
    raw = int(round(volts * 10))
    if not 0 <= raw <= 0xFF:
        raise ValueError(f"battery {volts} V out of range (0..25.5 V)")
    return f"{raw:02X}"


def encode_current_ma(raw: int) -> str:
    """Encode current as uint12, 1 mA units, 3 hex chars."""
    if not 0 <= raw <= 0xFFF:
        raise ValueError(f"current {raw} mA out of range (0..4095 mA)")
    return f"{raw:03X}"


def build_frame(temp_c: float, radiation: int, batt_v: float,
                current_ma: int) -> bytes:
    """Build the full XDATA frame: xdata= + 20 hex chars + CR (27 bytes)."""
    payload = (INST_ID + CHAIN_ID
               + encode_temp_c(temp_c)          # 4..7   internal temp
               + encode_radiation(radiation)    # 8..12  radiation sensor
               + encode_battery_v(batt_v)       # 13..14 battery voltage
               + encode_current_ma(current_ma)  # 15..17 current
               + "00")                          # 18..19 unused
    frame = PREAMBLE + payload
    if len(frame) != FRAME_LEN - 1:
        raise RuntimeError(f"frame length {len(frame)} != {FRAME_LEN - 1}")
    return frame.encode("ascii") + TERMINATOR


def verify_frame(frame: bytes) -> dict:
    """Re-parse the frame exactly like the decoders do (round-trip check)."""
    if len(frame) != FRAME_LEN:
        raise RuntimeError(f"frame must be {FRAME_LEN} bytes, got {len(frame)}")
    if not frame.endswith(TERMINATOR):
        raise RuntimeError("frame must end with a single CR (0x0D)")
    text = frame[:-1].decode("ascii")
    if not text.startswith(PREAMBLE):
        raise RuntimeError("frame must start with 'xdata='")
    payload = text[len(PREAMBLE):]
    if len(payload) != 20 or not all(c in "0123456789ABCDEF" for c in payload):
        raise RuntimeError("payload must be 20 uppercase hex chars")
    raw_temp = int(payload[4:8], 16)
    return {
        "id": int(payload[0:2], 16),
        "chain": int(payload[2:4], 16),
        "temp_c": (-(raw_temp & 0x7FFF) if raw_temp & 0x8000 else raw_temp) / 100.0,
        "radiation": int(payload[8:13], 16),          # uint20 raw
        "batt_v": int(payload[13:15], 16) / 10.0,     # uint8, 0.1 V
        "current_ma": int(payload[15:18], 16),        # uint12, 1 mA
        "unused": payload[18:20],
    }

# =====================================================================
# FINE PROTOCOLLO XDATA  --  tutto sopra e' byte-identico all'originale
# =====================================================================


class TrasmettitoreXDATA(threading.Thread):
    """
    Thread che ogni XDATA_TX_INTERVALLO_S secondi costruisce un frame e lo
    scrive sulla seriale condivisa con il lettore RS41.

    porta  : oggetto con metodo scrivi(bytes) -> bool  (rs41.LettoreRS41)
    valori : funzione che ritorna (temp_c, fonte_t, batt_v, fonte_b)
    stampa : True = una riga a schermo per ogni frame (modalità --solo-xdata)
    """

    def __init__(self, porta, valori, archivio=None, stampa=False):
        super().__init__(daemon=True, name="xdata_tx")
        self.porta = porta
        self.valori = valori
        self.archivio = archivio
        self.stampa = stampa
        self.inviati = 0
        self.da_sensore_t = 0
        self.da_sensore_b = 0

    def run(self):
        # controllo iniziale del formato con i valori di fallback (come l'originale)
        f0 = build_frame(config.XDATA_FALLBACK_TEMP_C, config.XDATA_RADIATION,
                         config.XDATA_FALLBACK_BATT_V, config.XDATA_CURRENT_MA)
        verify_frame(f0)
        log.info("XDATA TX attivo: frame di esempio %s\\r ogni %g s",
                 f0[:-1].decode("ascii"), config.XDATA_TX_INTERVALLO_S)
        while True:
            t0 = time.monotonic()
            try:
                self._invia_uno()
            except Exception:
                log.exception("errore XDATA TX")
            time.sleep(max(0.05, config.XDATA_TX_INTERVALLO_S - (time.monotonic() - t0)))

    def _invia_uno(self):
        temp_c, src_t, batt_v, src_b = self.valori()
        try:
            frame = build_frame(temp_c, config.XDATA_RADIATION, batt_v,
                                config.XDATA_CURRENT_MA)
        except ValueError as e:
            # valore fuori range (es. partitore sbagliato): salto il frame
            log.warning("valore fuori range, frame saltato: %s", e)
            return
        if not self.porta.scrivi(frame):
            return                       # seriale non ancora aperta: riprovo dopo
        self.inviati += 1
        self.da_sensore_t += src_t != "fallback"
        self.da_sensore_b += src_b != "fallback"
        testo = frame[:-1].decode("ascii")
        if self.archivio is not None:
            self.archivio.scrivi_xdata(testo, "TX")
        if self.stampa:
            print(f"{time.strftime('%H:%M:%S')}  sent #{self.inviati:<4}  "
                  f"T={temp_c:6.2f}C [{src_t:>8}]  V={batt_v:5.2f}V [{src_b:>8}]  "
                  f"raw={testo}", flush=True)


def valori_freschi(ultimi_validi, lock):
    """
    Crea la funzione 'valori' per il trasmettitore: usa l'ultima lettura
    valida di ogni sensore se ha meno di XDATA_STALE_S secondi, altrimenti
    il valore di fallback (stesso comportamento di tx_xdata_frame_sensors.py).
    ultimi_validi: dict nome -> (valore, time.monotonic())
    """
    def valori():
        ora = time.monotonic()
        with lock:
            t = ultimi_validi.get("temp_int_c")
            b = ultimi_validi.get("batteria_v")
        if t is not None and ora - t[1] <= config.XDATA_STALE_S:
            temp, src_t = t[0], "DHT11"
        else:
            temp, src_t = config.XDATA_FALLBACK_TEMP_C, "fallback"
        if b is not None and ora - b[1] <= config.XDATA_STALE_S:
            batt, src_b = b[0], "ADS1115"
        else:
            batt, src_b = config.XDATA_FALLBACK_BATT_V, "fallback"
        return temp, src_t, batt, src_b
    return valori
