#!/usr/bin/env python3
"""
Controllo dell'hardware, un componente alla volta.

    sudo venv/bin/python sonda/verifica.py

Non trasmette niente via radio.
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)   # niente messaggi tecnici

import config  # noqa: E402

risultati = []


def esito(nome, ok, dettaglio=""):
    simbolo = "OK     " if ok else "ERRORE "
    print(f"  [{simbolo}] {nome}: {dettaglio}")
    risultati.append((nome, ok))


def titolo(t):
    print(f"\n=== {t} ===")


# --------------------------------------------------------------------------
titolo("1. ADS1115 + batteria (I2C)")
from sensori import ADS1115, DHT11  # noqa: E402

ads = ADS1115(config.ADS_I2C_ADDR)
if ads.bus is None:
    esito("ADS1115", False, "non trovato a 0x48: controllate SDA (pin 3), SCL (pin 5), "
          "3.3 V e GND, e che ADDR sia collegato a GND")
else:
    v = ads.leggi(config.ADS_CANALE_BATTERIA)
    if v is None:
        esito("ADS1115", False, "trovato ma la lettura non riesce")
    else:
        vb = v * config.FATTORE_PARTITORE
        esito("ADS1115", True, f"A0 = {v:.3f} V  ->  batteria = {vb:.2f} V "
              f"(fattore partitore {config.FATTORE_PARTITORE:.3f})")
        if v > 3.4:
            print("      !!! ATTENZIONE: su A0 c'è più di 3.4 V: il partitore è sbagliato, "
                  "rischiate di rompere l'ADS1115 !!!")
        elif v < 0.05:
            print("      La tensione è ~0: batteria scollegata o partitore non collegato ad A0?")
        print("      Misurate la batteria col multimetro: se il valore è diverso,")
        print("      FATTORE_PARTITORE = tensione_multimetro / %.3f" % v)

# --------------------------------------------------------------------------
titolo("2. DHT11 (GPIO17, pin 11)")
dht = DHT11(config.DHT_GPIO)
if dht.metodo is None:
    esito("DHT11", False, "non disponibile: manca 'dtoverlay=dht11,gpiopin=17' in "
          "config.txt (poi riavviate)")
else:
    print(f"  metodo: {dht.metodo}")
    print("  lettura in corso (fino a 10 secondi)...")
    t, h = dht.leggi(tentativi=5)
    if t is None:
        esito("DHT11", False, "nessuna risposta: controllate DATA su pin 11, alimentazione "
              "3.3 V, GND e la resistenza di pull-up 10k tra DATA e 3.3 V")
    else:
        esito("DHT11", True, f"temperatura {t:.0f} °C, umidità {h:.0f} %")

# --------------------------------------------------------------------------
titolo("3. Camera")
import camera  # noqa: E402

foto = os.path.join(tempfile.gettempdir(), "verifica_camera.jpg")
img = camera.scatta(foto)
if img is None:
    esito("Camera", False, "nessuna foto: controllate il verso della piattina "
          "(contatti verso la scheda) e che sia ben inserita")
else:
    esito("Camera", True, f"foto {img.size[0]}x{img.size[1]} salvata in {foto}")

# --------------------------------------------------------------------------
titolo("4. Seriale RS41 (XDATA)")
porta = os.path.realpath(config.SERIALE_PORTA)
if not os.path.exists(config.SERIALE_PORTA):
    esito("Seriale", False, f"{config.SERIALE_PORTA} non esiste: UART non attiva "
          "(rilanciate install.sh e riavviate)")
else:
    try:
        import serial
        from rs41 import analizza_riga
        print(f"  ascolto {config.SERIALE_PORTA} ({porta}) a {config.SERIALE_BAUD} baud "
              "per 15 secondi...")
        righe, valide = 0, 0
        with serial.Serial(config.SERIALE_PORTA, config.SERIALE_BAUD, timeout=1) as s:
            fine = time.monotonic() + 15
            while time.monotonic() < fine:
                b = s.readline()
                if not b:
                    continue
                r = b.decode("ascii", errors="replace").strip()
                if not r:
                    continue
                righe += 1
                d = analizza_riga(r)
                valide += bool(d)
                if righe <= 8:
                    print(f"    ricevuto: {r[:70]}")
                    if d:
                        print(f"      -> capito: {d}")
        if righe == 0:
            esito("Seriale RS41", False, "nessun dato ricevuto. Controllate: RS41 accesa, "
                  "XDATA OUT -> pin 10 (RX), GND in comune, baud rate. Se la RS41 non "
                  "manda dati sulla XDATA, vedete il README")
        elif valide == 0:
            esito("Seriale RS41", False, f"{righe} righe ricevute ma formato non riconosciuto: "
                  "mandate queste righe per adattare rs41.py")
        else:
            esito("Seriale RS41", True, f"{righe} righe, {valide} con dati riconosciuti")
    except Exception as e:
        esito("Seriale RS41", False, str(e))

# --------------------------------------------------------------------------
titolo("5. Radio (solo controllo, NON trasmette)")
esito("sendiq (rpitx)", bool(shutil.which("sendiq")),
      shutil.which("sendiq") or "non installato: rilanciate install.sh")
cfg = "/boot/firmware/config.txt" if os.path.exists("/boot/firmware/config.txt") else "/boot/config.txt"
try:
    testo = open(cfg).read()
    for riga in ("gpu_freq=250", "force_turbo=1", "enable_uart=1", "dtparam=i2c_arm=on",
                 f"dtoverlay=dht11,gpiopin={config.DHT_GPIO}"):
        esito(riga, riga in testo, cfg)
except OSError:
    esito("config.txt", False, "non leggibile")
esito("root", os.geteuid() == 0, "serve sudo per trasmettere" if os.geteuid() else "ok")

# --------------------------------------------------------------------------
titolo("RIEPILOGO")
errori = [n for n, ok in risultati if not ok]
if errori:
    print("  Da sistemare: " + ", ".join(errori))
else:
    print("  Tutto OK! Potete passare alla prova con --simula.")
print()
