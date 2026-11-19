"""
Trasmissione radio: audio SSTV -> modulazione FM (NBFM) -> rpitx (sendiq) -> GPIO20.

Come funziona:
  1. l'immagine impaginata diventa audio SSTV (sstv.py)
  2. l'audio viene modulato in FM "in banda base" (campioni I/Q complessi)
  3. il programma `sendiq` di rpitx usa il generatore di clock del Raspberry
     per produrre direttamente la portante a 433.500 MHz su un pin GPIO.

ATTENZIONE: il segnale del pin è un'onda QUADRA, piena di armoniche
(867 MHz, 1.3 GHz, ...). Il filtro passa-basso/passa-banda DEVE stare
tra il pin e l'amplificatore, altrimenti trasmettete anche fuori banda.

Il GPIO20 (pin fisico 38) è collegato internamente al clock GPCLK0
(funzione ALT5), lo stesso clock che rpitx usa di default sul GPIO4.
L'install.sh applica una piccola patch a librpitx per usare il GPIO20 al
posto del GPIO4; in più qui lo impostiamo a mano con `pinctrl` / `raspi-gpio`,
così funziona anche con un rpitx non patchato (in quel caso la RF esce
ANCHE sul GPIO4/pin 7: non collegateci fili).
"""

import logging
import os
import shutil
import subprocess
import wave

import numpy as np

import config
import codifica_sstv as sstv

log = logging.getLogger("radio")

# funzione alternativa che porta GPCLK0 su ciascun pin
_ALT_GPCLK0 = {4: "a0", 20: "a5", 32: "a0", 34: "a0"}


def prepara_audio(img, modo):
    """Crea la sequenza completa: silenzio + immagine SSTV + ID in Morse."""
    seg = sstv.Segmenti()
    seg.silenzio(config.SILENZIO_INIZIALE_S * 1000)
    seg.unisci(sstv.segmenti_immagine(img, modo))
    seg.silenzio(300)
    if config.CW_ID:
        seg.unisci(sstv.segmenti_cw(f"DE {config.CALLSIGN}",
                                    config.CW_WPM, config.CW_TONO_HZ))
    seg.silenzio(300)
    return seg


def genera_file(seg, percorso_iq, percorso_wav=None):
    """
    Sintetizza l'audio e scrive:
      - percorso_iq : campioni I/Q float32 interlacciati per sendiq (o None)
      - percorso_wav: (opzionale) audio 16 bit, utile per provare la
                      decodifica sul PC con MMSSTV/QSSTV o col telefono
    Restituisce la durata in secondi.
    """
    fs = config.SAMPLE_RATE
    k = 2.0 * np.pi * config.DEVIAZIONE_FM_HZ / fs
    fase_fm = 0.0
    campioni = 0
    wav = None
    if percorso_wav:
        wav = wave.open(percorso_wav, "wb")
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(fs)
    try:
        f_iq = open(percorso_iq, "wb") if percorso_iq else None
        try:
            for audio in sstv.sintetizza(seg, fs):
                if wav:
                    wav.writeframes((audio * 32000).astype("<i2").tobytes())
                if f_iq is not None:
                    # FM: la fase della portante segue l'integrale dell'audio
                    fasi = fase_fm + k * np.cumsum(audio, dtype=np.float64)
                    fase_fm = float(fasi[-1] % (2.0 * np.pi))
                    iq = np.empty(2 * len(fasi), dtype="<f4")
                    iq[0::2] = np.cos(fasi) * 0.9
                    iq[1::2] = np.sin(fasi) * 0.9
                    f_iq.write(iq.tobytes())
                campioni += len(audio)
        finally:
            if f_iq:
                f_iq.close()
    finally:
        if wav:
            wav.close()
    return campioni / fs


def _imposta_pin_rf():
    """Collega il clock GPCLK0 al pin scelto (GPIO20 = ALT5)."""
    alt = _ALT_GPCLK0.get(config.RF_GPIO)
    if alt is None:
        log.error("GPIO%d non ha il clock GPCLK0! Usare 4 o 20.", config.RF_GPIO)
        return
    if shutil.which("pinctrl"):            # Raspberry Pi OS Bookworm
        cmd = ["pinctrl", "set", str(config.RF_GPIO), alt]
    elif shutil.which("raspi-gpio"):       # versioni precedenti
        cmd = ["raspi-gpio", "set", str(config.RF_GPIO), alt]
    else:
        log.warning("né pinctrl né raspi-gpio trovati: mi affido alla patch di librpitx")
        return
    subprocess.run(cmd, check=False, timeout=10)


def _rilascia_pin_rf():
    """Finita la trasmissione rimette il pin come ingresso (nessun segnale)."""
    for prog in ("pinctrl", "raspi-gpio"):
        if shutil.which(prog):
            subprocess.run([prog, "set", str(config.RF_GPIO), "ip"],
                           check=False, timeout=10)
            return


def trasmetti(percorso_iq, durata_s):
    """Lancia sendiq e aspetta la fine. Ritorna True se tutto ok."""
    if config.SOLO_SIMULAZIONE:
        log.info("SIMULAZIONE: trasmissione saltata (%.0f s)", durata_s)
        try:
            os.remove(percorso_iq)
        except OSError:
            pass
        return True
    sendiq = shutil.which("sendiq")
    if not sendiq:
        log.error("sendiq non trovato: avete eseguito install.sh (rpitx)?")
        return False
    # correzione del quarzo: se il Pi trasmette X ppm più in alto, chiediamo meno
    freq = config.FREQUENZA_HZ * (1.0 - config.PPM_CORREZIONE / 1e6)
    cmd = [sendiq, "-i", percorso_iq, "-s", str(config.SAMPLE_RATE),
           "-f", f"{freq:.0f}", "-t", "float"]
    log.info("TX %.4f MHz, %.0f s: %s", freq / 1e6, durata_s, " ".join(cmd))
    _imposta_pin_rf()
    try:
        # sendiq legge il file e poi esce; il timeout evita blocchi infiniti
        r = subprocess.run(cmd, timeout=durata_s + 60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode != 0:
            log.error("sendiq ha restituito %d: %s", r.returncode,
                      r.stderr.decode(errors="replace")[-500:])
            return False
        return True
    except subprocess.TimeoutExpired:
        log.error("sendiq bloccato, interrotto")
        return False
    finally:
        _rilascia_pin_rf()
        try:
            os.remove(percorso_iq)
        except OSError:
            pass
