"""
CONFIGURAZIONE DELLA SONDA
==========================
Tutti i parametri che potreste voler cambiare sono qui.
Non serve toccare gli altri file per l'uso normale.
"""

# --------------------------------------------------------------------------
# IDENTIFICAZIONE
# --------------------------------------------------------------------------
CALLSIGN = "IW3BAU"                  # nominativo radioamatore (va in ogni immagine + CW)
NOME_SCUOLA = "ITT MARCONI ROVERETO"  # testo usato se il logo non viene trovato
NOME_MISSIONE = "SONDA 3Q"            # piccola scritta nella barra in alto

# Logo: mettete il file PNG (meglio con sfondo trasparente) in assets/
LOGO_PATH = "assets/logo_marconi.png"

# --------------------------------------------------------------------------
# PIN / HARDWARE  (numerazione BCM, NON il numero fisico del pin)
# --------------------------------------------------------------------------
DHT_GPIO = 17            # DHT11 -> GPIO17 (pin fisico 11)
RF_GPIO = 20             # uscita RF -> GPIO20 (pin fisico 38), filtro + ampli
# ADS1115 su I2C: SDA = GPIO2 (pin 3), SCL = GPIO3 (pin 5), indirizzo 0x48 (ADDR a GND)
ADS_I2C_ADDR = 0x48
ADS_CANALE_BATTERIA = 0  # A0

# !!! L'ADS1115 NON regge 12 V sull'ingresso (massimo = VDD + 0.3 V) !!!
# Serve un partitore di tensione. Esempio: R1 = 33k (verso la batteria),
# R2 = 10k (verso GND) -> 12.6 V diventano 2.93 V.
# FATTORE = (R1 + R2) / R2. Calibratelo con un multimetro:
#   FATTORE = tensione_vera_batteria / tensione_letta_su_A0
PARTITORE_R1_OHM = 33000
PARTITORE_R2_OHM = 10000
# FATTORE_PARTITORE = (PARTITORE_R1_OHM + PARTITORE_R2_OHM) / PARTITORE_R2_OHM
# Calibrato col multimetro il 19/11/2026: 12.36 V veri / 3.074 V letti su A0
FATTORE_PARTITORE = 12.36 / 3.074

# Soglie batteria 12 V (solo per colorare il valore nell'immagine)
BATTERIA_OK_V = 11.5
BATTERIA_BASSA_V = 10.5

# --------------------------------------------------------------------------
# RS41 XDATA (seriale)
# --------------------------------------------------------------------------
# Pi TX (GPIO14, pin 8) -> XDATA IN della RS41   (frame OIF411 mandati dal Pi)
# Pi RX (GPIO15, pin 10) <- XDATA OUT della RS41  (dati letti, se la RS41 li manda)
# GND in comune! Livelli logici 3.3 V (compatibili con il Raspberry).
SERIALE_PORTA = "/dev/serial0"
SERIALE_BAUD = 9600      # standard XDATA = 9600 8N1
# Dopo quanti secondi senza dati validi i valori RS41 sono considerati "vecchi"
RS41_TIMEOUT_DATI_S = 60

# --- TX XDATA: il Pi manda alla RS41 frame OIF411 con temperatura e batteria,
#     che la RS41 trasmette a terra (protocollo di tx_xdata_frame_sensors.py)
XDATA_TX_ATTIVO = True
XDATA_TX_INTERVALLO_S = 1.0     # ~1 Hz, come sulla daisychain della RS41
XDATA_STALE_S = 30              # lettura più vecchia di così -> si usa il fallback
                                # (i sensori si leggono ogni INTERVALLO_SENSORI_S)
XDATA_FALLBACK_TEMP_C = 22.5    # valori mandati se il sensore non risponde
XDATA_FALLBACK_BATT_V = 12.3
XDATA_RADIATION = 0             # nessun sensore: costante (uint20)
XDATA_CURRENT_MA = 0            # nessun sensore: costante (0..4095)

# --------------------------------------------------------------------------
# TEMPI
# --------------------------------------------------------------------------
INTERVALLO_SENSORI_S = 10      # ogni quanti secondi leggere DHT11 + batteria e salvare
PAUSA_TRA_IMMAGINI_S = 90      # pausa tra la fine di una trasmissione e la successiva
                               # (serve anche a far raffreddare l'ampli: in quota non
                               #  c'è aria che lo raffreddi!)

# --------------------------------------------------------------------------
# RADIO / SSTV
# --------------------------------------------------------------------------
FREQUENZA_HZ = 433.500e6
# Modo SSTV: "robot36" (~36 s, 320x240), "martin1" (~114 s, 320x256),
#            "pd120" (~126 s, 640x496, qualità migliore)
MODO_SSTV = "robot36"
DEVIAZIONE_FM_HZ = 3000        # NBFM: 2500-3000 per canali 12.5 kHz, fino a 5000 per 25 kHz
SAMPLE_RATE = 48000
SILENZIO_INIZIALE_S = 1.0      # portante senza audio prima del VIS (apre lo squelch)
CW_ID = True                   # dopo ogni immagine trasmette "DE IW3BAU" in Morse
CW_WPM = 20
CW_TONO_HZ = 800
PPM_CORREZIONE = 0.0           # correzione quarzo del Pi (misuratela con un SDR)

# Se True non trasmette davvero: salva solo il WAV (utile per le prove a terra)
SOLO_SIMULAZIONE = False

# --------------------------------------------------------------------------
# CAMERA
# --------------------------------------------------------------------------
FOTO_LARGHEZZA = 2592          # risoluzione piena camera v1.2 (OV5647)
FOTO_ALTEZZA = 1944
FOTO_RUOTA_180 = False         # se la camera è montata a testa in giù

# --------------------------------------------------------------------------
# ARCHIVIO SU SD
# --------------------------------------------------------------------------
# Vuoto = cartella "dati" dentro il progetto (es. /home/pi/SONDA-BALOON-3Q/dati)
CARTELLA_DATI = ""
SALVA_WAV = False              # salva anche l'audio SSTV (occupa ~3-12 MB a immagine)
SPAZIO_MINIMO_MB = 200         # sotto questa soglia smette di salvare le foto grandi
