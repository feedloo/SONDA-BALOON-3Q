"""
Sensori letti direttamente dal Raspberry:
  - DHT11 su GPIO17     -> temperatura e umidità interne alla scatola
  - ADS1115 su I2C (A0) -> tensione della batteria 12 V (tramite partitore!)

Ogni lettura è protetta: se un sensore si guasta in volo, il programma
continua e scrive valori vuoti invece di bloccarsi.
"""

import logging
import time

import config

log = logging.getLogger("sensori")


class DHT11:
    """
    Il DHT11 su Linux sbaglia spesso la lettura (problemi di tempistica):
    è normale, si riprova. NB: il DHT11 misura solo da 0 a 50 °C.
    """

    def __init__(self, gpio):
        self.dev = None
        try:
            import adafruit_dht
            import board
            pin = getattr(board, f"D{gpio}")
            try:
                self.dev = adafruit_dht.DHT11(pin)
            except Exception:
                self.dev = adafruit_dht.DHT11(pin, use_pulseio=False)
            log.info("DHT11 pronto su GPIO%d", gpio)
        except Exception as e:
            log.error("DHT11 non disponibile: %s", e)

    def leggi(self, tentativi=4):
        """Ritorna (temperatura_C, umidita_%) oppure (None, None)."""
        if self.dev is None:
            return None, None
        for i in range(tentativi):
            try:
                t = self.dev.temperature
                h = self.dev.humidity
                if t is not None and h is not None:
                    return float(t), float(h)
            except RuntimeError:
                pass                       # errore di checksum/timing: normale
            except Exception as e:
                log.warning("DHT11 errore: %s", e)
            time.sleep(2.1)                # il DHT11 vuole >= 2 s tra le letture
        return None, None


class ADS1115:
    """
    Lettura diretta dei registri dell'ADS1115 con smbus2 (niente librerie
    complicate). Misura "single-shot" sul canale scelto rispetto a GND.
    """

    REG_CONV = 0x00
    REG_CONF = 0x01
    FONDO_SCALA_V = 4.096                   # PGA = ±4.096 V

    def __init__(self, indirizzo, bus=1):
        self.addr = indirizzo
        self.bus = None
        try:
            from smbus2 import SMBus
            self.bus = SMBus(bus)
            self.bus.read_i2c_block_data(self.addr, self.REG_CONF, 2)
            log.info("ADS1115 trovato a 0x%02X", indirizzo)
        except Exception as e:
            log.error("ADS1115 non disponibile: %s", e)
            self.bus = None

    def _leggi_volt(self, canale):
        conf = (0x8000                       # OS: avvia una conversione
                | ((0b100 + canale) << 12)   # MUX: AINx rispetto a GND
                | (0b001 << 9)               # PGA ±4.096 V
                | (1 << 8)                   # modo single-shot
                | (0b100 << 5)               # 128 campioni/s
                | 0b11)                      # comparatore disattivato
        self.bus.write_i2c_block_data(self.addr, self.REG_CONF,
                                      [conf >> 8, conf & 0xFF])
        for _ in range(20):                  # aspetta fine conversione (~8 ms)
            time.sleep(0.005)
            hi, lo = self.bus.read_i2c_block_data(self.addr, self.REG_CONF, 2)
            if hi & 0x80:
                break
        hi, lo = self.bus.read_i2c_block_data(self.addr, self.REG_CONV, 2)
        grezzo = (hi << 8) | lo
        if grezzo > 0x7FFF:
            grezzo -= 0x10000
        return grezzo * self.FONDO_SCALA_V / 32768.0

    def leggi(self, canale, campioni=8):
        """Media di più letture. Ritorna volt sul pin, o None."""
        if self.bus is None:
            return None
        try:
            valori = [self._leggi_volt(canale) for _ in range(campioni)]
            return sum(valori) / len(valori)
        except Exception as e:
            log.warning("ADS1115 errore: %s", e)
            return None


class Sensori:
    def __init__(self):
        self.dht = DHT11(config.DHT_GPIO)
        self.ads = ADS1115(config.ADS_I2C_ADDR)

    def leggi(self):
        t, h = self.dht.leggi()
        v_pin = self.ads.leggi(config.ADS_CANALE_BATTERIA)
        v_batt = v_pin * config.FATTORE_PARTITORE if v_pin is not None else None
        return {
            "temp_int_c": t,
            "umid_int_pct": h,
            "adc_a0_v": v_pin,
            "batteria_v": v_batt,
        }
