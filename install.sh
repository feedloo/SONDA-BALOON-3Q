#!/bin/bash
# =============================================================================
#  Installazione SONDA 3Q su Raspberry Pi Zero (Raspberry Pi OS Bookworm Lite)
#  Uso:   cd SONDA-BALOON-3Q && sudo ./install.sh
#  Serve internet SOLO durante l'installazione. Alla fine: sudo reboot
# =============================================================================
set -e
if [ "$(id -u)" -ne 0 ]; then echo "Eseguire con sudo"; exit 1; fi

DIR="$(cd "$(dirname "$0")" && pwd)"
CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt

echo "=== 1/5 Pacchetti di sistema ==="
apt-get update
apt-get install -y git build-essential python3-venv python3-pip python3-numpy \
    python3-pil python3-serial python3-libgpiod libgpiod2 i2c-tools \
    fonts-dejavu-core libsndfile1-dev libfftw3-dev
apt-get install -y rpicam-apps-lite || apt-get install -y libcamera-apps-lite || true
apt-get install -y libraspberrypi-dev || echo "ATTENZIONE: libraspberrypi-dev non trovato"

echo "=== 2/5 Ambiente Python ==="
python3 -m venv --system-site-packages "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade smbus2 pyserial adafruit-blinka adafruit-circuitpython-dht

echo "=== 3/5 rpitx (solo sendiq) con uscita RF su GPIO20 ==="
BUILD=/opt/rpitx-build
mkdir -p "$BUILD" && cd "$BUILD"
[ -d librpitx ] || git clone --depth 1 https://github.com/F5OEO/librpitx
[ -d rpitx ]    || git clone --depth 1 https://github.com/F5OEO/rpitx
# Patch: il clock RF esce su GPIO20 (ALT5) invece che su GPIO4
sed -i 's|clkgpio::enableclk(4);|clkgpio::enableclk(20); // SONDA: RF su GPIO20|' \
    librpitx/src/iqdmasync.cpp
grep -q "enableclk(20)" librpitx/src/iqdmasync.cpp && echo "patch GPIO20 OK"
(cd librpitx/src && make && make install)
(cd rpitx/src && make ../sendiq && install -m 0755 ../sendiq /usr/local/bin/sendiq)
command -v sendiq && echo "sendiq installato"

echo "=== 4/5 Configurazione Raspberry (I2C, UART, camera, clock stabile) ==="
raspi-config nonint do_i2c 0 || true
# UART hardware ON, console seriale OFF (la seriale serve alla RS41!)
raspi-config nonint do_serial_hw 0 2>/dev/null || true
raspi-config nonint do_serial_cons 1 2>/dev/null || raspi-config nonint do_serial 2 || true
aggiungi() { grep -qxF "$1" "$CFG" || echo "$1" >> "$CFG"; }
aggiungi "dtparam=i2c_arm=on"
aggiungi "enable_uart=1"
aggiungi "dtoverlay=disable-bt"   # su Zero W: UART "vera" su GPIO14/15
aggiungi "camera_auto_detect=1"
aggiungi "gpu_freq=250"           # richiesto da rpitx per frequenza stabile
aggiungi "force_turbo=1"
sed -i 's/console=serial0,[0-9]* //' /boot/firmware/cmdline.txt 2>/dev/null || \
    sed -i 's/console=serial0,[0-9]* //' /boot/cmdline.txt 2>/dev/null || true
systemctl disable --now hciuart 2>/dev/null || true

echo "=== 5/5 Servizio di avvio automatico ==="
sed "s|@DIR@|$DIR|g" "$DIR/sonda.service" > /etc/systemd/system/sonda.service
systemctl daemon-reload
echo
echo "Installazione completata."
echo "  - Mettete il logo in $DIR/assets/logo_marconi.png"
echo "  - Riavviate: sudo reboot"
echo "  - Prova:     sudo $DIR/venv/bin/python $DIR/sonda/main.py --simula"
echo "  - Volo:      sudo systemctl enable --now sonda   (parte da solo all'accensione)"
