#!/usr/bin/env bash
# deploy.sh — generuje i wgrywa pakiety + dashboardy HAPLA do Home Assistant.
#
#   import/hapla-ha-addons/config/deploy.sh                       # BRIDGE z hapla_ha.yaml
#   import/hapla-ha-addons/config/deploy.sh 192.168.0.150:5020    # tryb demo: HA → demo_bridge.py
#
# Wymaga SSH do dodatku Advanced SSH (użytkownik z sudo). Przy pierwszym
# wdrożeniu dopisuje do configuration.yaml include pakietów i dashboardy YAML
# (kopia: configuration.yaml.hapla-bak). Po wgraniu: kontrola konfiguracji
# i restart Core.
set -euo pipefail

HA_SSH=${HA_SSH:-hassio@192.168.0.208}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/build"

rm -rf "$OUT"
python3 "$HERE/gen_ha.py" --out-dir "$OUT" ${1:+--bridge "$1"}

tar -C "$OUT" -cf - packages dashboards www | ssh "$HA_SSH" 'sudo tar -xf - -C /homeassistant'

# Umowa (taryfa + net-billing) — edytowana w HA, W4 pobiera ją z /local/hapla/contract.yaml.
# Szablon z repo tylko przy pierwszym wdrożeniu: nie nadpisujemy zmian z HA.
ssh "$HA_SSH" 'test -e /homeassistant/www/hapla/contract.yaml' || \
    ssh "$HA_SSH" 'sudo tee /homeassistant/www/hapla/contract.yaml >/dev/null' < "$HERE/../../../tools/plc/ctrl/contract.yaml"

ssh "$HA_SSH" 'sudo sh -s' <<'EOF'
set -e
cfg=/homeassistant/configuration.yaml
grep -q 'hapla_sciana.yaml' "$cfg" && exit 0
if grep -q 'hapla_sterowanie.yaml' "$cfg"; then
  echo '[deploy] configuration.yaml ma już dashboardy HAPLA, ale brak hapla-sciana.' >&2
  echo '[deploy] Dodaj ręcznie pod lovelace.dashboards:' >&2
  echo '[deploy]     hapla-sciana:' >&2
  echo '[deploy]       mode: yaml' >&2
  echo '[deploy]       title: HAPLA Ściana' >&2
  echo '[deploy]       icon: mdi:tablet-dashboard' >&2
  echo '[deploy]       show_in_sidebar: true' >&2
  echo '[deploy]       filename: dashboards/hapla_sciana.yaml' >&2
  exit 0
fi
cp "$cfg" "$cfg.hapla-bak"
cat >> "$cfg" <<'YAML'

homeassistant:
  packages: !include_dir_named packages

lovelace:
  dashboards:
    hapla-sterowanie:
      mode: yaml
      title: HAPLA
      icon: mdi:home-thermometer
      show_in_sidebar: true
      filename: dashboards/hapla_sterowanie.yaml
    hapla-przeplyw:
      mode: yaml
      title: HAPLA przepływy
      icon: mdi:transmission-tower
      show_in_sidebar: true
      filename: dashboards/hapla_przeplyw.yaml
    hapla-sciana:
      mode: yaml
      title: HAPLA Ściana
      icon: mdi:tablet-dashboard
      show_in_sidebar: true
      filename: dashboards/hapla_sciana.yaml
YAML
EOF

ssh "$HA_SSH" 'bash -lc "ha core check && ha core restart"'
