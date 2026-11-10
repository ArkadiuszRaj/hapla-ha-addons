# HAPLA — Home Assistant

Wszystko, co HAPLA instaluje w Home Assistant, w jednym repo.

| Katalog | Zawartość |
|---|---|
| `hapla_plc/`, `hapla_modbus/`, `whistle_stt/`, `pc_view_agent/` | dodatki HA — sklep dodatków: `https://github.com/ArkadiuszRaj/hapla-ha-addons` |
| `config/` | konfiguracja HA: generator pakietów, dashboardów i SVG (`gen_ha.py` + `hapla_ha.yaml`), wdrożenie (`deploy.sh`), testy, symulator BRIDGE (`demo_bridge.py`), rzut parteru do ciemnego panelu ściennego (`gen_plan.py`: warstwy podłoga/ściany/drzwi + obrysy pokoi; `patch_panel.py`: jednorazowe wpięcie ich do makiety panelu) |

Dashboardy: `hapla_sterowanie` (HAPLA), `hapla_przeplyw` (przepływy), `hapla_sciana` (panel ścienny na tablet — sekcja `wall_panel` w `hapla_ha.yaml`: orientacja, kamera albo rzut, ulubione pokoje, ładowanie tabletu).

`config/` działa jako submoduł `import/hapla-ha-addons` repo HAPLA: generator czyta mapę
rejestrów BRIDGE (`tools/plc/ctrl/build/hapla-regs.csv`, `app/bridge/doc/regs.json`),
a `deploy.sh` wgrywa też szablon umowy `tools/plc/ctrl/contract.yaml` (W4 ma go jako
kopię awaryjną).

```bash
import/hapla-ha-addons/config/deploy.sh                  # z korzenia HAPLA
python3 -m pytest import/hapla-ha-addons/config/test_gen_ha.py
```
