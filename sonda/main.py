#!/usr/bin/env python3
"""
SONDA 3Q - ITT Marconi Rovereto - IW3BAU
=======================================

Programma principale. Lavora con due "fili" (thread) in parallelo:

  * thread RS41      : legge di continuo i dati XDATA dalla seriale
  * thread XDATA TX  : ogni secondo manda alla RS41 un frame OIF411 con
                       temperatura interna e batteria (la RS41 li trasmette)
  * thread SENSORI   : ogni INTERVALLO_SENSORI_S legge DHT11 + batteria
                       e scrive una riga su telemetria.csv
  * ciclo principale : scatta foto -> impagina -> codifica SSTV ->
                       trasmette a 433.400 MHz -> pausa -> ripete

Uso:
    sudo python3 main.py            # volo vero
    sudo python3 main.py --simula   # tutto vero tranne la trasmissione (salva WAV)
    sudo python3 main.py --solo-xdata  # solo frame XDATA verso la RS41 (prova seriale)
    python3 main.py --test          # PC senza hardware: crea immagine + WAV di prova
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time

# permette di lanciare il programma da qualsiasi cartella
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config          # noqa: E402
import impaginazione   # noqa: E402
import radio           # noqa: E402
import codifica_sstv as sstv  # noqa: E402

log = logging.getLogger("main")


def temperatura_cpu():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except Exception:
        return None


def imposta_logging(file_log=None):
    handlers = [logging.StreamHandler(sys.stdout)]
    if file_log:
        handlers.append(logging.FileHandler(file_log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)-8s %(message)s")


class Stato:
    """Ultime letture dei sensori del Pi, condivise tra i thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sensori = {}
        # ultima lettura VALIDA di ogni sensore: nome -> (valore, time.monotonic())
        # (usata dal trasmettitore XDATA per decidere "fresco" o "fallback")
        self.ultimi_validi = {}
        self.immagini = 0
        self.ora_impostata = False
        self.xdata_tx = None


def aggiorna_validi(stato, letture):
    ora = time.monotonic()
    with stato.lock:
        stato.sensori = letture
        for k in ("temp_int_c", "umid_int_pct", "batteria_v"):
            if letture.get(k) is not None:
                stato.ultimi_validi[k] = (letture[k], ora)


def imposta_ora_da_gps(rs):
    """
    Il Pi Zero senza internet non sa che ore sono. Se la RS41 manda data e
    ora GPS e l'orologio di sistema è chiaramente sbagliato, lo corregge.
    """
    if time.gmtime().tm_year >= 2025 or not rs.get("data_gps") or not rs.get("ora_gps"):
        return False
    try:
        subprocess.run(["date", "-u", "-s", f"{rs['data_gps']} {rs['ora_gps']}"],
                       check=True, timeout=5, stdout=subprocess.DEVNULL)
        log.info("ora di sistema impostata da GPS: %s %s", rs["data_gps"], rs["ora_gps"])
        return True
    except Exception as e:
        log.warning("impossibile impostare l'ora: %s", e)
        return False


def thread_sensori(sensori, lettore, archivio, stato):
    """Ogni INTERVALLO_SENSORI_S esatti: legge i sensori e scrive una riga CSV."""
    prossimo = time.monotonic()
    while True:
        try:
            letture = sensori.leggi()
            rs = lettore.stato() if lettore else {}
            aggiorna_validi(stato, letture)
            with stato.lock:
                n_img = stato.immagini
                if not stato.ora_impostata and rs:
                    stato.ora_impostata = imposta_ora_da_gps(rs)
            riga = dict(rs, **letture, cpu_temp_c=temperatura_cpu(),
                        immagini_trasmesse=n_img,
                        xdata_tx_frame=stato.xdata_tx.inviati if stato.xdata_tx else None)
            archivio.scrivi_telemetria(riga)
            log.info("T.int=%s°C  U=%s%%  Batt=%sV  Alt=%s m  (RS41 %s)",
                     letture["temp_int_c"], letture["umid_int_pct"],
                     None if letture["batteria_v"] is None else round(letture["batteria_v"], 2),
                     rs.get("alt_m"), "ok" if not rs.get("rs41_vecchi", True) else "assente")
        except Exception:
            log.exception("errore nel ciclo sensori")
        # cadenza fissa: la prossima lettura è esattamente 10 s dopo la precedente
        prossimo += config.INTERVALLO_SENSORI_S
        attesa = prossimo - time.monotonic()
        if attesa < 0:                       # siamo in ritardo: riparto da adesso
            prossimo = time.monotonic()
            attesa = 0
        time.sleep(attesa)


def dati_per_immagine(stato, lettore):
    rs = lettore.stato() if lettore else {"rs41_vecchi": True}
    with stato.lock:
        s = dict(stato.sensori)
    d = dict(rs, **s)
    d["ora_utc"] = rs.get("ora_gps") if not rs.get("rs41_vecchi") else None
    return d


def ciclo_immagini(archivio, stato, lettore):
    """
    Ogni INTERVALLO_IMMAGINI_S (da inizio a inizio):
      1. foto con esposizione automatica  -> foto/img_....jpg (+ .json)
      2. impaginazione con i dati attuali -> sstv/img_....png  (= ciò che va giù)
      3. codifica SSTV + FM               -> sstv/img_....wav  (= audio trasmesso)
      4. trasmissione a 433.4 MHz
      5. riga in immagini.csv
    Dopo ogni trasmissione c'è comunque almeno PAUSA_MINIMA_TX_S di pausa.
    """
    import camera
    cam = camera.Camera()
    while True:
        t_ciclo = time.monotonic()
        riga = {"modo": config.MODO_SSTV, "trasmessa": 0}
        try:
            with stato.lock:
                stato.immagini += 1
                n = stato.immagini
            base = f"img_{n:04d}_{time.strftime('%H%M%S')}"
            riga["numero"] = n
            spazio_ok = archivio.spazio_libero_mb() >= config.SPAZIO_MINIMO_MB

            # 1) foto originale (a piena risoluzione, con parametri di scatto)
            foto_path = os.path.join(archivio.foto, base + ".jpg")
            foto, info_cam = cam.scatta(foto_path)
            if foto is not None and not spazio_ok:
                for p in (foto_path, os.path.splitext(foto_path)[0] + ".json"):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                log.warning("SD quasi piena: foto originale non conservata")
            elif foto is not None:
                riga["file_foto"] = os.path.basename(foto_path)
            for k in ("ev", "esposizione_us", "guadagno", "luminosita", "bruciati",
                      "tentativo"):
                riga[k] = info_cam.get(k)

            # 2) immagine impaginata = esattamente ciò che viene trasmesso
            dati = dati_per_immagine(stato, lettore)
            img = impaginazione.impagina(foto, dati, n)
            sstv_path = os.path.join(archivio.sstv, base + ".png")
            img.save(sstv_path)
            riga["file_sstv"] = os.path.basename(sstv_path)
            for k in ("alt_m", "lat", "lon", "temp_int_c", "batteria_v"):
                riga[k] = dati.get(k)
            del foto

            # 3) codifica SSTV + modulazione FM (I/Q in RAM, WAV sulla SD)
            seg = radio.prepara_audio(img, config.MODO_SSTV)
            durata_stimata = seg.durata_s()
            iq_path = radio.percorso_temp_iq(archivio.cartella, durata_stimata)
            wav_path = None
            if config.SALVA_WAV and spazio_ok:
                wav_path = os.path.join(archivio.sstv, base + ".wav")
            t0 = time.monotonic()
            durata = radio.genera_file(seg, iq_path, wav_path)
            riga["tempo_codifica_s"] = round(time.monotonic() - t0, 1)
            riga["durata_audio_s"] = round(durata, 1)
            if wav_path and os.path.exists(wav_path):
                riga["file_wav"] = os.path.basename(wav_path)
            log.info("immagine #%d pronta (%s, %.0f s di audio) in %.0f s",
                     n, config.MODO_SSTV, durata, riga["tempo_codifica_s"])
            del seg, img

            # 4) trasmissione
            riga["ora_inizio_tx_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ok, reale = radio.trasmetti(iq_path, durata)
            riga["trasmessa"] = int(ok)
            riga["durata_tx_reale_s"] = round(reale, 1)
        except Exception:
            log.exception("errore nel ciclo immagini")
        finally:
            # 5) la riga in immagini.csv si scrive SEMPRE, anche se qualcosa è fallito
            try:
                if "numero" in riga:
                    archivio.scrivi_immagine(riga)
            except Exception:
                log.exception("impossibile scrivere immagini.csv")

        trascorso = time.monotonic() - t_ciclo
        attesa = max(config.PAUSA_MINIMA_TX_S, config.INTERVALLO_IMMAGINI_S - trascorso)
        log.info("ciclo immagine durato %.0f s, prossima tra %.0f s", trascorso, attesa)
        time.sleep(attesa)


def modo_test(cartella):
    """Prova senza hardware: crea immagine impaginata + WAV per ogni modo SSTV."""
    from PIL import Image, ImageDraw
    os.makedirs(cartella, exist_ok=True)
    imposta_logging()
    # finta foto: cielo sfumato con orizzonte curvo
    W, H = 1296, 972
    foto = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(foto)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(5 + 60 * k), int(10 + 110 * k), int(40 + 200 * k)))
    d.ellipse([-W * 0.6, H * 0.62, W * 1.6, H * 2.2], fill=(70, 110, 160))
    d.ellipse([-W * 0.5, H * 0.66, W * 1.5, H * 2.2], fill=(235, 240, 245))
    dati = {"alt_m": 28431.0, "lat": 45.88912, "lon": 11.04012, "vel_vert": 5.2,
            "temp_est_c": -47.3, "temp_int_c": 12.0, "batteria_v": 11.62,
            "ora_utc": "10:42:17", "rs41_vecchi": False}
    for modo in sstv.MODI:
        img = impaginazione.impagina(foto, dati, 12, modo)
        png = os.path.join(cartella, f"test_{modo}.png")
        img.save(png)
        t0 = time.monotonic()
        dur = radio.genera_file(radio.prepara_audio(img, modo), None,
                                os.path.join(cartella, f"test_{modo}.wav"))
        log.info("%s: %s + WAV %.1f s (generato in %.1f s)", modo, png, dur,
                 time.monotonic() - t0)
    # stessa immagine senza dati e senza foto, per vedere come appare un guasto
    impaginazione.impagina(None, {"rs41_vecchi": True}, 13, "robot36").save(
        os.path.join(cartella, "test_senza_dati.png"))


def modo_solo_xdata():
    """
    Prova come tx_xdata_frame_sensors.py: legge DHT11 + ADS1115 e manda
    solo i frame XDATA alla RS41, stampando una riga per frame.
    Niente camera, niente radio SSTV, niente archivio su SD.
    """
    from rs41 import LettoreRS41
    from sensori import Sensori
    import xdata_tx
    imposta_logging()
    stato = Stato()
    lettore = LettoreRS41(archivio=None)
    lettore.start()
    sensori = Sensori()
    tx = xdata_tx.TrasmettitoreXDATA(
        lettore, xdata_tx.valori_freschi(stato.ultimi_validi, stato.lock), stampa=True)
    print(f"Trasmetto su {config.SERIALE_PORTA} @ {config.SERIALE_BAUD} baud, 8N1, "
          f"ogni {config.XDATA_TX_INTERVALLO_S:g} s  (Ctrl+C per fermare)\n")
    tx.start()
    inizio = time.monotonic()
    try:
        while True:
            aggiorna_validi(stato, sensori.leggi())
            time.sleep(2)
    except KeyboardInterrupt:
        durata = time.monotonic() - inizio
        n = max(tx.inviati, 1)
        print("\n-- riepilogo --")
        print(f"frame inviati   : {tx.inviati}  in {durata:.0f} s")
        print(f"temp da DHT11   : {tx.da_sensore_t}/{tx.inviati} "
              f"({tx.da_sensore_t / n * 100:.0f}%)")
        print(f"batt da ADS1115 : {tx.da_sensore_b}/{tx.inviati} "
              f"({tx.da_sensore_b / n * 100:.0f}%)")
        print(f"righe ricevute dalla RS41: {lettore.righe_ricevute}")


def main():
    ap = argparse.ArgumentParser(description="Sonda 3Q - IW3BAU")
    ap.add_argument("--test", action="store_true", help="prova senza hardware")
    ap.add_argument("--simula", action="store_true", help="non trasmette, salva WAV")
    ap.add_argument("--solo-xdata", action="store_true",
                    help="manda solo i frame XDATA alla RS41 (prova della seriale)")
    args = ap.parse_args()

    if args.solo_xdata:
        modo_solo_xdata()
        return
    if args.test:
        modo_test(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "test_output"))
        return
    if args.simula:
        config.SOLO_SIMULAZIONE = True

    from archivio import Archivio
    from rs41 import LettoreRS41
    from sensori import Sensori

    # arresto pulito (systemctl stop / spegnimento): SIGTERM diventa un'uscita
    # normale, così i blocchi "finally" spengono il pin RF e chiudono i file
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    radio.rilascia_pin_rf()      # se un crash precedente l'aveva lasciato acceso

    archivio = Archivio()
    imposta_logging(archivio.percorso_log())
    log.info("=== SONDA %s avviata, dati in %s ===", config.CALLSIGN, archivio.cartella)
    log.info("sensori ogni %d s, un'immagine ogni %d s (%s, %.3f MHz)%s",
             config.INTERVALLO_SENSORI_S, config.INTERVALLO_IMMAGINI_S, config.MODO_SSTV,
             config.FREQUENZA_HZ / 1e6, "  [SIMULAZIONE: niente TX]"
             if config.SOLO_SIMULAZIONE else "")
    if not config.SOLO_SIMULAZIONE and os.geteuid() != 0:
        log.warning("non sono root: rpitx (sendiq) non potrà trasmettere! Usare sudo.")

    stato = Stato()
    lettore = LettoreRS41(archivio)
    lettore.start()
    if config.XDATA_TX_ATTIVO:
        import xdata_tx
        stato.xdata_tx = xdata_tx.TrasmettitoreXDATA(
            lettore, xdata_tx.valori_freschi(stato.ultimi_validi, stato.lock), archivio)
        stato.xdata_tx.start()
    sensori = Sensori()
    threading.Thread(target=thread_sensori, args=(sensori, lettore, archivio, stato),
                     daemon=True, name="sensori").start()

    time.sleep(5)    # lascia arrivare le prime letture
    try:
        ciclo_immagini(archivio, stato, lettore)
    finally:
        radio.rilascia_pin_rf()
        log.info("=== SONDA fermata ===")


if __name__ == "__main__":
    main()
