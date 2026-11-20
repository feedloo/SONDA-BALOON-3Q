#!/bin/bash
# =============================================================================
#  AVVIO DELLA SONDA PER IL VOLO
#      cd ~/SONDA-BALOON-3Q && sudo ./avvia_volo.sh
#  (opzionale)  sudo ./avvia_volo.sh pulisci   -> cancella prima i dati delle prove
#
#  Ferma qualunque altra istanza, controlla tutto, installa e avvia il servizio
#  di sistema "sonda" e aspetta la prima trasmissione. Il programma gira come
#  SERVIZIO: chiudendo PuTTY continua a funzionare, e riparte da solo (dopo
#  20 s) ad ogni accensione o se dovesse bloccarsi.
# =============================================================================
if [ "$(id -u)" -ne 0 ]; then echo "Eseguire con:  sudo ./avvia_volo.sh"; exit 1; fi
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/venv/bin/python"
ERR=0
ok() { echo "  [OK]      $*"; }
ko() { echo "  [ERRORE]  $*"; ERR=1; }

echo "=== 1/5 Fermo programmi vecchi o lanciati a mano"
systemctl disable --now sstv.service >/dev/null 2>&1
systemctl stop sonda.service >/dev/null 2>&1
pkill -f "sonda/main.py" 2>/dev/null
pkill -x sendiq 2>/dev/null
pkill -f "rpicam-still" 2>/dev/null
pinctrl set 20 ip >/dev/null 2>&1 || raspi-gpio set 20 ip >/dev/null 2>&1
sleep 2
ok "nessun altro programma usa camera, seriale o radio"

if [ "${1:-}" = "pulisci" ]; then
    rm -rf "$DIR"/dati/volo_*
    ok "dati delle prove cancellati"
fi

echo "=== 2/5 Controlli"
[ -x "$PY" ] && ok "Python del progetto" || ko "manca $PY (eseguire sudo ./install.sh)"
command -v sendiq >/dev/null && ok "sendiq (rpitx)" || ko "manca sendiq (eseguire sudo ./install.sh)"
command -v rpicam-still >/dev/null && ok "rpicam-still (camera)" || ko "manca rpicam-still"
[ -e /dev/serial0 ] && ok "seriale /dev/serial0" || ko "manca /dev/serial0 (UART non attiva)"
if "$PY" -m py_compile "$DIR"/sonda/*.py 2>/tmp/sonda_compile.txt; then
    ok "codice senza errori di sintassi"
else
    ko "errore nel codice:"; cat /tmp/sonda_compile.txt
fi
CONF=$(cd "$DIR/sonda" && "$PY" -c "
import config as c
print(f'{c.FREQUENZA_HZ/1e6:.3f} MHz | {c.MODO_SSTV} | immagine ogni {c.INTERVALLO_IMMAGINI_S} s | sensori ogni {c.INTERVALLO_SENSORI_S} s | scatto max {c.FOTO_SHUTTER_MAX_US} us')
print('SIMULAZIONE' if c.SOLO_SIMULAZIONE else 'TX')
" 2>&1)
echo "$CONF" | head -1 | sed 's/^/  [INFO]    /'
if echo "$CONF" | tail -1 | grep -qx "TX"; then
    ok "trasmissione radio ATTIVA"
else
    ko "SOLO_SIMULAZIONE = True in config.py: la sonda NON trasmetterebbe"
fi
LIBERO=$(df -m "$DIR" | awk 'NR==2 {print $4}')
[ "${LIBERO:-0}" -gt 1000 ] && ok "spazio libero sulla SD: ${LIBERO} MB" || ko "poco spazio sulla SD: ${LIBERO} MB"

if [ "$ERR" -ne 0 ]; then
    echo; echo "!!! Ci sono errori: sistemateli prima di lanciare. Servizio NON avviato. !!!"
    exit 1
fi

echo "=== 3/5 Installo e attivo l'avvio automatico"
sed "s|@DIR@|$DIR|g" "$DIR/sonda.service" > /etc/systemd/system/sonda.service
systemctl daemon-reload
systemctl enable sonda.service >/dev/null 2>&1
T0=$(date +%s)
systemctl restart sonda.service
[ "$(systemctl is-enabled sonda.service)" = "enabled" ] && ok "avvio automatico ad ogni accensione" \
    || ko "avvio automatico NON attivo"

echo "=== 4/5 Attendo l'avvio del programma (20 s di attesa previsti)..."
AVVIATO=0
for i in $(seq 1 60); do
    sleep 1
    if journalctl -u sonda --since "@$T0" -o cat --no-pager 2>/dev/null | grep -q "avviata, dati in"; then
        AVVIATO=1; break
    fi
done
if [ "$AVVIATO" -eq 1 ]; then
    ok "programma partito dopo ${i} s"
    journalctl -u sonda --since "@$T0" -o cat --no-pager | grep "avviata, dati in" | sed 's/^.*dati in /  [INFO]    dati salvati in /'
else
    ko "il programma non è partito entro 60 s"
fi

echo "=== 5/5 Attendo la prima trasmissione SSTV (fino a 3 minuti)..."
TX=0
for i in $(seq 1 180); do
    sleep 1
    LOG=$(journalctl -u sonda --since "@$T0" -o cat --no-pager 2>/dev/null)
    if echo "$LOG" | grep -q "TX inizio"; then TX=1; break; fi
    if echo "$LOG" | grep -q "Traceback\|errore nel ciclo immagini\|sendiq non trovato"; then break; fi
done
journalctl -u sonda --since "@$T0" -o cat --no-pager | grep -E "foto \[|pronta|TX inizio|ERROR" | tail -5 | sed 's/^/  > /'
if [ "$TX" -eq 1 ]; then
    ok "LA SONDA STA TRASMETTENDO"
else
    ko "nessuna trasmissione ancora: controllate con  journalctl -u sonda -f"
fi

echo
if [ "$ERR" -eq 0 ]; then
    echo "#############################################################"
    echo "#  TUTTO OK. Potete chiudere PuTTY e scollegare il PC:      #"
    echo "#  la sonda continua da sola (immagine ogni minuto, dati    #"
    echo "#  ogni 10 s) e riparte da sola ad ogni accensione.         #"
    echo "#############################################################"
else
    echo "Ci sono errori: guardate i messaggi qui sopra e  journalctl -u sonda -n 50"
fi
echo "Per vedere cosa fa:   journalctl -u sonda -f        (Ctrl+C esce, la sonda continua)"
echo "Per fermarla:         sudo systemctl stop sonda"
