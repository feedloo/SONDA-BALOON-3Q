#!/usr/bin/env python3
"""
Diagnostica della seriale (GPIO14 TX pin 8, GPIO15 RX pin 10).

  sudo venv/bin/python sonda/prova_seriale.py loopback
      Prova del Raspberry da solo: collegate con un filo il pin 8 al pin 10
      (RS41 scollegata). Se torna indietro quello che si manda, la seriale
      del Pi funziona.

  sudo venv/bin/python sonda/prova_seriale.py ascolta
      Ascolta la RS41 a varie velocità e le manda anche un "Invio" per vedere
      se risponde. Mostra i byte grezzi (testo ed esadecimale).

Fermate il servizio prima:  sudo systemctl stop sonda
"""

import sys
import time

import serial

PORTA = "/dev/serial0"
VELOCITA = [9600, 4800, 19200, 38400, 57600, 115200]


def mostra(dati):
    testo = "".join(chr(b) if 32 <= b < 127 else "." for b in dati[:80])
    hexa = " ".join(f"{b:02X}" for b in dati[:32])
    print(f"      testo: {testo}")
    print(f"      hex  : {hexa}{' ...' if len(dati) > 32 else ''}")


def loopback():
    print("Prova loopback: il pin 8 (TX) deve essere collegato al pin 10 (RX).")
    with serial.Serial(PORTA, 9600, timeout=1) as s:
        s.reset_input_buffer()
        msg = b"PROVA-SERIALE-IW3BAU\r\n"
        s.write(msg)
        s.flush()
        time.sleep(0.3)
        r = s.read(len(msg))
    if r == msg:
        print("  OK: la seriale del Raspberry funziona.")
    elif r:
        print("  Ricevuto qualcosa ma diverso:")
        mostra(r)
    else:
        print("  ERRORE: non torna niente. Controllate il ponticello tra pin 8 e 10,")
        print("  e che la console seriale sia disattivata (install.sh + reboot).")


def ascolta():
    print("Ascolto la RS41 (accesa, TX/RX/GND collegati)...")
    trovato = False
    for v in VELOCITA:
        with serial.Serial(PORTA, v, timeout=0.5) as s:
            s.reset_input_buffer()
            print(f"\n  {v} baud: ascolto 6 s...")
            dati = bytearray()
            fine = time.monotonic() + 6
            while time.monotonic() < fine:
                dati += s.read(256)
            if not dati:
                print("    invio un Invio e aspetto 3 s...")
                s.write(b"\r\n")
                s.flush()
                fine = time.monotonic() + 3
                while time.monotonic() < fine:
                    dati += s.read(256)
            if dati:
                trovato = True
                print(f"    RICEVUTI {len(dati)} byte:")
                mostra(dati)
            else:
                print("    niente")
    print()
    if trovato:
        print("Arrivano dati: mandate queste righe per adattare la lettura in rs41.py.")
        print("(Se il testo è illeggibile alla velocità giusta, il testo diventa leggibile.)")
    else:
        print("Dalla RS41 non arriva nulla a nessuna velocità.")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "ascolta"
    loopback() if modo == "loopback" else ascolta()
