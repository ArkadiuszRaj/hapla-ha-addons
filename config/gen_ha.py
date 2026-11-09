#!/usr/bin/env python3
"""
gen_ha.py — pakiety Home Assistant + dashboardy HAPLA z mapy rejestrów.

Wejście:
    import/hapla-ha-addons/config/hapla_ha.yaml                 — ręczna konfiguracja (pokoje, programy, bridge)
    tools/plc/ctrl/build/hapla-regs.csv    — mapa rejestrów (scripts/gen_hapla_regs.py)
    app/bridge/doc/regs.json               — rejestry diagnostyczne BRIDGE (scripts/xlsx2modbus.py)

Wyjście (--out-dir, domyślnie import/hapla-ha-addons/config/build/, gitignore'd) — układ katalogu
/config w HA:
    packages/hapla_modbus.yaml      — hub Modbus TCP do BRIDGE + sensory
    packages/hapla_rooms.yaml       — skrypt zapisu nastaw pokojów (rooms[i].tz)
    packages/hapla_control.yaml     — helpery (program, przesunięcie) + sensory pochodne
    packages/hapla_automation.yaml  — automatyki i skrypty przycisków
    dashboards/hapla_sterowanie.yaml
    dashboards/hapla_przeplyw.yaml
    www/hapla/*.svg                 — schemat przepływu + nakładki animowane
    www/hapla/rzut*.svg             — rzut parteru + podświetlenia pokojów z włączonym światłem

Użycie:
    python3 import/hapla-ha-addons/config/gen_ha.py [--out-dir DIR] [--bridge HOST:PORT]
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]   # HAPLA: dodatki są submodułem import/hapla-ha-addons
REGS_CSV = REPO / 'tools/plc/ctrl/build/hapla-regs.csv'
BRIDGE_REGS_JSON = REPO / 'app/bridge/doc/regs.json'
HUB = 'hapla'

PROGRAMS = ['Normalny', 'Impreza', 'Nieobecność', 'Urlop']

# region ─── Wejście ─────────────────────────────────────────────────────────


def load_cfg(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def parse_scale(text: str) -> tuple[str | None, float]:
    """'°C ×10' → ('°C', 0.1); 'W' → ('W', 1); '-' → (None, 1)."""
    text = text.strip()
    if text in ('', '-'):
        return None, 1
    m = re.fullmatch(r'(.*?)\s*×(\d+)', text)
    if m:
        return (m.group(1) or None), 1 / int(m.group(2))
    return text, 1


def load_regs(path: Path) -> dict:
    regs = {}
    with path.open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            unit, scale = parse_scale(row['scale'])
            regs[row['name']] = {
                'addr': int(row['reg_start']),
                'input_type': 'input' if row['dir'] == 'I' else 'holding',
                'unit': unit,
                'scale': scale,
            }
    return regs


def load_bridge_regs(path: Path) -> dict:
    """Rejestry statyczne BRIDGE (regs.xlsx) po nazwie: addr, input_type, data_type."""
    out = {}
    for name, r in json.loads(path.read_text(encoding='utf-8'))['registers'].items():
        out[name] = {'addr': r['addr'], 'input_type': 'input' if r['class'] == 'INPUT' else 'holding',
                     'data_type': r['type'] if r['type'] != 'uint8' else 'uint16'}
    return out

# endregion
# region ─── Katalog sensorów ────────────────────────────────────────────────

# (klucz encji bez prefiksu, rejestr, nazwa przyjazna)
SENSORS = [
    # bufor CO i obieg grzewczy
    ('tb1_c',  'bridge_sens.tb1_c', 'Bufor CO – góra'),
    ('tb2_c',  'bridge_sens.tb2_c', 'Bufor CO – środek'),
    ('tb3_c',  'bridge_sens.tb3_c', 'Bufor CO – dół'),
    ('m1_c',   'bridge_sens.m1_c',  'Zasilanie z bufora'),
    ('m2_c',   'bridge_sens.m2_c',  'Powrót do bufora'),
    ('m3_c',   'bridge_sens.m3_c',  'Powrót z rozdzielacza'),
    ('m4_c',   'bridge_sens.m4_c',  'Zasilanie rozdzielacza'),
    ('m1p_w',  'bridge_sens.m1p_w', 'Pompa CO – moc'),
    ('m2p_w',  'bridge_sens.m2p_w', 'Pompa CWU – moc'),
    ('m3p_w',  'bridge_sens.m3p_w', 'Pompa ZWU – moc'),
    ('tcwu_c', 'bridge_ctrl.tcwu_c', 'CWU – temperatura'),
    ('m1_pct', 'bridge_ctrl.m1_pct', 'Pompa CO – wysterowanie'),
    ('m2_pct', 'bridge_ctrl.m2_pct', 'Cyrkulacja CWU – wysterowanie'),
    ('m3_pct', 'bridge_ctrl.m3_pct', 'Cyrkulacja ZWU – wysterowanie'),
    ('mix_pct', 'bridge_ctrl.mix_pct', 'Mieszacz – otwarcie'),
    ('h1_pct', 'bridge_ctrl.h1_pct', 'Grzałka bufora 1'),
    ('h2_pct', 'bridge_ctrl.h2_pct', 'Grzałka bufora 2'),
    ('h3_pct', 'bridge_ctrl.h3_pct', 'Grzałka bufora 3'),
    ('hcwu_pct', 'bridge_ctrl.hcwu_pct', 'Grzałka CWU'),
    # ESS Victron
    ('soc',       'ess.batt_soc_pct', 'Bateria – SOC'),
    ('batt_w',    'ess.batt_power_w', 'Bateria – moc'),
    ('pv_w',      'ess.pv_power_w',   'PV – moc'),
    ('ac_l1_w',   'ess.ac_l1_w',      'Odbiory AC – L1'),
    ('ac_l2_w',   'ess.ac_l2_w',      'Odbiory AC – L2'),
    ('ac_l3_w',   'ess.ac_l3_w',      'Odbiory AC – L3'),
    ('grid_l1_w', 'ess.grid_l1_w',    'Sieć – L1'),
    ('grid_l2_w', 'ess.grid_l2_w',    'Sieć – L2'),
    ('grid_l3_w', 'ess.grid_l3_w',    'Sieć – L3'),
    ('ess_age_s', 'ess.age_s',        'ESS – wiek danych'),
    ('soc_min_cmd', 'ess_cmd.soc_min_cmd', 'ESS – minimalny SOC (komenda)'),
    ('grid_sp_w',   'ess_grid_sp_w',       'ESS – nastawa sieci'),
    # rekuperator
    ('rec_out_c', 'recuperator.t_outdoor_c', 'Rekuperator – powietrze zewn. (po GWC)'),
    ('rec_sup_c', 'recuperator.t_supply_c',  'Rekuperator – nawiew'),
    ('rec_ext_c', 'recuperator.t_extract_c', 'Rekuperator – wywiew'),
    ('rec_exh_c', 'recuperator.t_exhaust_c', 'Rekuperator – wyrzut'),
    ('rec_eff_pct', 'w3_diag.rec_efficiency_pct', 'Rekuperator – sprawność'),
    # plan W3
    ('energy_mode',     'w3_plan.energy_mode',     'Tryb energetyczny'),
    ('t_buf_target_c',  'w3_plan.t_buf_target_c',  'Bufor CO – cel'),
    ('soc_target_pct',  'w3_plan.soc_target_pct',  'Bateria – cel SOC'),
    ('heat_demand_kwh', 'w3_plan.heat_demand_kwh', 'Zapotrzebowanie ciepła 24 h'),
    ('reserve_h',       'w3_plan.reserve_h',       'Rezerwa energii'),
    ('plan_valid',      'w3_plan.valid',           'Plan W3 ważny'),
    ('pv_d0_kwh', 'w3_diag.pred_pv_d0_kwh', 'Prognoza PV – dziś'),
    ('pv_d1_kwh', 'w3_diag.pred_pv_d1_kwh', 'Prognoza PV – jutro'),
    ('pv_d2_kwh', 'w3_diag.pred_pv_d2_kwh', 'Prognoza PV – pojutrze'),
    ('t_out_c',   'forecast[0].t_c',        'Temperatura zewnętrzna'),
    # depozyt prosumencki — stan księgi W4 (deposit_ledger.py, contract.yaml)
    ('deposit_balance_zl',    'deposit.balance_zl',    'Depozyt – saldo'),
    ('deposit_refundable_zl', 'deposit.refundable_zl', 'Depozyt – zwrot do wypłaty'),
    ('deposit_budget_zl',     'deposit.budget_zl',     'Depozyt – prąd bez utraty zwrotu'),
    ('deposit_inv_diff_zl',   'deposit.inv_diff_zl',   'Depozyt – licznik minus faktura'),
    ('deposit_free',          'deposit.free',          'Depozyt – prąd z sieci „za darmo”'),
    ('deposit_meter_age_h',   'deposit.meter_age_h',   'Licznik sieci – wiek odczytu'),
    ('deposit_hist_days',     'deposit.hist_days',     'Licznik sieci – dni historii'),
    # alarmy i statusy programów pomocniczych
    ('legionella_alarm',  'legionella_alarm',  'Legionella'),
    ('recuperator_alarm', 'recuperator_alarm', 'Rekuperator – alarm'),
    ('forecast_alarm',    'forecast_alarm',    'Prognoza – alarm'),
    ('wm_alarm',          'wm_alarm',          'Wodomierz – alarm'),
    ('circulation_status', 'circulation_status', 'Cyrkulacja CWU – aktywna'),
    ('flush_status',      'flush_status',      'Płukanie ZWU – aktywne'),
    ('vacation_mode',     'vacation_mode',     'Tryb urlopu (rolety)'),
]

# (sufiks, pole w rooms[i] / room_cmd[i], opis)
ROOM_SENSORS = [
    ('t',   'rooms[{i}].to_c',            'temperatura'),
    ('tz',  'rooms[{i}].tz',              'nastawa'),
    ('rh',  'rooms[{i}].rh_pct',          'wilgotność'),
    ('sp',  'room_cmd[{i}].t_setpoint_c', 'nastawa nocna'),
    ('pwm', 'room_cmd[{i}].loop_pwm',     'pętla CO'),
    ('dew', 'room_cmd[{i}].dew_alarm',    'alarm punktu rosy'),
]

# Magistrala CAN — rejestry diagnostyczne BRIDGE 400–414 (app/bridge/regs.xlsx):
# (klucz encji bez prefiksu, rejestr, nazwa przyjazna, dodatkowe pola sensora)
_TOTAL = {'state_class': 'total_increasing'}
CAN_SENSORS = [
    ('can_status',       'CAN_STATUS',        'CAN – stan (bity)', {}),
    ('can_online',       'CAN_SLAVES_ONLINE', 'CAN – węzły online', {'state_class': 'measurement'}),
    ('can_time_source',  'CAN_TIME_SOURCE',   'CAN – źródło czasu (kod)', {}),
    ('can_phase_us',     'CAN_PHASE_US',      'CAN – błąd fazy wobec TRK',
     {'unit_of_measurement': 'µs', 'state_class': 'measurement'}),
    ('can_polls_ok',     'CAN_POLLS_OK',      'CAN – udane odczyty', _TOTAL),
    ('can_polls_fail',   'CAN_POLLS_FAIL',    'CAN – nieudane odczyty', _TOTAL),
    ('can_writes_fail',  'CAN_WRITES_FAIL',   'CAN – nieudane zapisy', _TOTAL),
    ('can_went_offline', 'CAN_WENT_OFFLINE',  'CAN – utraty węzłów', _TOTAL),
    ('can_phase_steps',  'CAN_PHASE_STEPS',   'CAN – korekty fazy', _TOTAL),
]

NO_UNIT = {'bitmask', 'bitfield', 'cykle'}
DEVICE_CLASS = {'°C': 'temperature', 'W': 'power', 'hPa': 'pressure', 's': 'duration'}


def room_key(room: dict) -> str:
    return f"hapla_room{room['id']:02d}"


def _sensor(key: str, reg: dict, scan: int) -> dict:
    s = {
        'name': key,
        'unique_id': key,
        'device_address': 0,     # nadpisywane w build_modbus
        'address': reg['addr'],
        'input_type': reg['input_type'],
        'data_type': 'int16',
        'scan_interval': scan,
    }
    if reg['scale'] != 1:
        s['scale'] = reg['scale']
        s['precision'] = len(str(reg['scale']).split('.')[1])
    unit = reg['unit']
    if unit and unit not in NO_UNIT:
        s['unit_of_measurement'] = unit
        s['state_class'] = 'measurement'
        dc = DEVICE_CLASS.get(unit)
        if key.endswith('_rh'):
            dc = 'humidity'
        elif key == 'hapla_soc':
            dc = 'battery'
        if dc:
            s['device_class'] = dc
    return s


def build_modbus(cfg: dict, regs: dict, bregs: dict) -> dict:
    br = cfg['bridge']
    scan = br['scan_interval']
    sensors, customize = [], {}

    def add(key, regname, friendly):
        s = _sensor(key, regs[regname], scan)
        s['device_address'] = br['unit']
        sensors.append(s)
        customize[f'sensor.{key}'] = {'friendly_name': friendly}

    for key, regname, friendly in SENSORS:
        add(f'hapla_{key}', regname, friendly)

    # Licznik wody: dwa słowa INT16, młodsze pierwsze (wm_lo, wm_hi).
    lo = regs['bridge_sens.wm_lo']
    sensors.append({
        'name': 'hapla_water_m3', 'unique_id': 'hapla_water_m3',
        'device_address': br['unit'], 'address': lo['addr'], 'input_type': lo['input_type'],
        'data_type': 'uint32', 'swap': 'word', 'scale': 0.001, 'precision': 3,
        'unit_of_measurement': 'm³', 'device_class': 'water', 'state_class': 'total_increasing',
        'scan_interval': scan,
    })
    customize['sensor.hapla_water_m3'] = {'friendly_name': 'Licznik wody'}

    for i, room in enumerate(cfg['rooms']):
        for suffix, field, what in ROOM_SENSORS:
            add(f'{room_key(room)}_{suffix}', field.format(i=i), f"{room['name']} – {what}")

    for key, regname, friendly, extra in CAN_SENSORS:
        r = bregs[regname]
        key = f'hapla_{key}'
        sensors.append({'name': key, 'unique_id': key, 'device_address': br['unit'],
                        'address': r['addr'], 'input_type': r['input_type'],
                        'data_type': r['data_type'], 'scan_interval': scan, **extra})
        customize[f'sensor.{key}'] = {'friendly_name': friendly}

    return {
        'modbus': [{
            'name': HUB, 'type': 'tcp', 'host': br['host'], 'port': br['port'],
            'sensors': sensors,
        }],
        'homeassistant': {'customize': customize},
    }

# endregion
# region ─── Nastawy pokojów ─────────────────────────────────────────────────


def setpoint(cfg: dict, room: dict, program: str, offset: int) -> int:
    """Referencja: nastawa rooms[i].tz [°C] dla programu i przesunięcia."""
    day = room['dzien'] + offset
    if program == 'Urlop':
        v = room['urlop']
    elif program == 'Impreza':
        v = day + room.get('impreza', 0)
    elif program == 'Nieobecność':
        v = min(day, max(day - cfg['absence_drop'], cfg['absence_floor']))
    else:
        v = day
    return min(max(v, cfg['tz_min']), cfg['tz_max'])


def setpoint_template(cfg: dict) -> str:
    """Ten sam algorytm co setpoint(), w Jinja (zmienne: program, offset, r)."""
    return (
        "{%- set o = offset | int(0) -%}"
        "{%- set day = r.dzien + o -%}"
        "{%- if program == 'Urlop' -%}{%- set v = r.urlop -%}"
        "{%- elif program == 'Impreza' -%}{%- set v = day + r.impreza -%}"
        "{%- elif program == 'Nieobecność' -%}"
        f"{{%- set v = [day, [day - {cfg['absence_drop']}, {cfg['absence_floor']}] | max] | min -%}}"
        "{%- else -%}{%- set v = day -%}{%- endif -%}"
        f"{{{{ [[v, {cfg['tz_min']}] | max, {cfg['tz_max']}] | min }}}}"
    )


def room_items(cfg: dict) -> list[dict]:
    """Dane pokojów dla pętli w skrypcie: indeks rooms[i] = pozycja na liście."""
    return [{'idx': i, 'addr': 2000 + 10 * i, 'name': r['name'],
             'dzien': r['dzien'], 'urlop': r['urlop'], 'impreza': r.get('impreza', 0)}
            for i, r in enumerate(cfg['rooms'])]


def build_rooms(cfg: dict) -> dict:
    unit = cfg['bridge']['unit']
    write = {'action': 'modbus.write_register'}
    return {'script': {'hapla_apply_setpoints': {
        'alias': 'HAPLA – zapis nastaw pokojów',
        'mode': 'queued',
        'variables': {
            'program': "{{ states('input_select.hapla_program') }}",
            'offset': "{{ states('input_number.hapla_offset') | int(0) }}",
        },
        'sequence': [
            {'repeat': {
                'for_each': room_items(cfg),
                'sequence': [
                    {'variables': {'r': '{{ repeat.item }}'}},
                    {**write, 'data': {'hub': HUB, 'slave': unit, 'address': '{{ r.addr }}',
                                       'value': setpoint_template(cfg)}},
                ],
            }},
            {**write, 'data': {'hub': HUB, 'slave': unit, 'address': 3016,
                               'value': "{{ 1 if program == 'Urlop' else 0 }}"}},
        ],
    }}}

# endregion
# region ─── Sterowanie: helpery + sensory pochodne ──────────────────────────


def _num(e: str) -> str:
    return f"states('{e}') | float(0)"


def _tpl(uid: str, state: str, **extra) -> dict:
    return {'name': uid, 'unique_id': uid, 'state': state, **extra}


# Nakładki przepływu: binary_sensor → (plik SVG, warunek)
FLOWS = {
    'pv':        "{{ states('sensor.hapla_pv_w') | float(0) > 50 }}",
    'grid_in':   "{{ states('sensor.hapla_grid_w') | float(0) > 50 }}",
    'grid_out':  "{{ states('sensor.hapla_grid_w') | float(0) < -50 }}",
    'batt_chg':  "{{ states('sensor.hapla_batt_w') | float(0) > 50 }}",
    'batt_dis':  "{{ states('sensor.hapla_batt_w') | float(0) < -50 }}",
    'house':     "{{ states('sensor.hapla_house_w') | float(0) > 50 }}",
    'heaters':   "{{ states('sensor.hapla_heaters_w') | float(0) > 0 }}",
    'pumps':     "{{ states('sensor.hapla_pumps_w') | float(0) > 0 }}",
    'hcwu':      "{{ states('sensor.hapla_hcwu_pct') | float(0) > 0 }}",
    'co':        "{{ states('sensor.hapla_m1_pct') | float(0) > 0 }}",
    'cwu_circ':  "{{ states('sensor.hapla_m2_pct') | float(0) > 0 }}",
}

ESS_STALE_S = 60     # = C_ESS_STALE_S (hapla-const.st)

# region ─── Kalendarz świąt ─────────────────────────────────────────────────
# Listy w BRIDGE: holiday_fixed[16] jako MMDD (%MW9100), holiday_easter[8] jako dni po
# Niedzieli Wielkanocnej (%MW9116); cała lista z zer = domyślna (hapla-calendar.st,
# HOL_FIXED_DEF / HOL_EASTER_DEF; W4: HOLIDAYS_*_DEFAULT). HA trzyma je jako tekst
# do edycji i liczy daty tym samym algorytmem Wielkanocy (Meeus/Jones/Butcher).
HOLIDAY_FIXED_REG, HOLIDAY_FIXED_N = 9100, 16
HOLIDAY_EASTER_REG, HOLIDAY_EASTER_N = 9116, 8
HOL_FIXED_DEF = [101, 106, 501, 503, 815, 1101, 1111, 1224, 1225, 1226]
HOL_EASTER_DEF = [1, 49, 60]

# Pole tekstowe → lista liczb do rejestrów. Stałe: „D.MM” rozdzielone przecinkami
# (np. „1.01, 11.11”); ruchome: dni po Wielkanocy („1, 49, 60”). Błędne wpisy pomijane.
_HOL_FIXED_PARSE = (
    "{%- set ns = namespace(v=[]) -%}"
    "{%- for s in states('input_text.hapla_holidays_fixed').split(',') -%}"
    "{%- set p = s.strip().split('.') -%}"
    "{%- if p | length == 2 and 1 <= p[1] | int(0) <= 12 and 1 <= p[0] | int(0) <= 31 -%}"
    "{%- set ns.v = ns.v + [p[1] | int(0) * 100 + p[0] | int(0)] -%}"
    "{%- endif -%}{%- endfor -%}")
_HOL_EASTER_PARSE = (
    "{%- set ne = namespace(v=[]) -%}"
    "{%- for s in states('input_text.hapla_holidays_easter').split(',') -%}"
    "{%- if 1 <= s.strip() | int(0) <= 120 -%}{%- set ne.v = ne.v + [s.strip() | int(0)] -%}{%- endif -%}"
    "{%- endfor -%}")


def holiday_regs_template(kind: str) -> str:
    """Wartości do zapisu w rejestrach (lista z zerami do pełnej długości)."""
    if kind == 'fixed':
        return _HOL_FIXED_PARSE + "{{ (ns.v + [0] * " + str(HOLIDAY_FIXED_N) + ")[:" + str(HOLIDAY_FIXED_N) + "] }}"
    return _HOL_EASTER_PARSE + "{{ (ne.v + [0] * " + str(HOLIDAY_EASTER_N) + ")[:" + str(HOLIDAY_EASTER_N) + "] }}"


def holidays_template(what: str) -> str:
    """Daty świąt na bieżący i następny rok — sama arytmetyka całkowita (dzień juliański),
    bez filtrów HA, żeby test liczył ją zwykłym Jinja. what: 'next' — najbliższe święto
    (DD.MM.RRRR), 'list' — lista dat."""
    return (
        _HOL_FIXED_PARSE + _HOL_EASTER_PARSE +
        "{%- set fixed = ns.v if ns.v else " + str(HOL_FIXED_DEF) + " -%}"
        "{%- set offs = ne.v if ne.v else " + str(HOL_EASTER_DEF) + " -%}"
        "{%- macro jdn(y, m, d) -%}{%- set a = (14 - m) // 12 -%}{%- set yy = y + 4800 - a -%}"
        "{%- set mm = m + 12 * a - 3 -%}"
        "{{ d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045 }}{%- endmacro -%}"
        "{%- macro ymd(j) -%}{%- set a = j + 32044 -%}{%- set b = (4 * a + 3) // 146097 -%}"
        "{%- set c = a - 146097 * b // 4 -%}{%- set d = (4 * c + 3) // 1461 -%}{%- set e = c - 1461 * d // 4 -%}"
        "{%- set m = (5 * e + 2) // 153 -%}"
        "{{ '%02d.%02d.%04d' | format(e - (153 * m + 2) // 5 + 1, m + 3 - 12 * (m // 10), 100 * b + d - 4800 + m // 10) }}"
        "{%- endmacro -%}"
        "{%- set t = now() -%}{%- set today = jdn(t.year, t.month, t.day) | int -%}"
        "{%- set nd = namespace(days=[]) -%}"
        "{%- for y in [t.year, t.year + 1] -%}"
        "{%- set a = y % 19 -%}{%- set b = y // 100 -%}{%- set c = y % 100 -%}"
        "{%- set h = (19 * a + b - b // 4 - (b - (b + 8) // 25 + 1) // 3 + 15) % 30 -%}"
        "{%- set l = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7 -%}"
        "{%- set mo = (a + 11 * h + 22 * l) // 451 -%}"
        "{%- set easter = jdn(y, (h + l - 7 * mo + 114) // 31, (h + l - 7 * mo + 114) % 31 + 1) | int -%}"
        "{%- for f in fixed -%}{%- set nd.days = nd.days + [jdn(y, f // 100, f % 100) | int] -%}{%- endfor -%}"
        "{%- for o in offs -%}{%- set nd.days = nd.days + [easter + o] -%}{%- endfor -%}"
        "{%- endfor -%}"
        "{%- set days = nd.days | unique | sort -%}"
        + ("{{ ymd(days | select('>=', today) | first) }}" if what == 'next' else
           "{%- set out = namespace(v=[]) -%}{%- for j in days -%}{%- set out.v = out.v + [ymd(j)] -%}{%- endfor -%}"
           "{{ out.v }}"))

# endregion


DEPOSIT_REG = 9130   # deposit.inv_year … inv_balance_zl (hapla-regs.json, T_Deposit)
INVOICE_REGS_TEMPLATE = (
    "{%- set d = states('input_datetime.hapla_invoice_date') -%}"
    "{%- set p = d.split('-') -%}"
    "{%- set b = states('input_number.hapla_invoice_balance') | float(-1) -%}"
    "{%- if p | length == 3 and p[0] | int(0) > 2000 and b >= 0 -%}"
    "{{ [p[0] | int(0), p[1] | int(0), p[2] | int(0), b | int(0)] }}"
    "{%- else -%}{{ [0, 0, 0, 0] }}{%- endif -%}")


def build_control(cfg: dict) -> dict:
    hk, ck = cfg['heater_kw'] * 1000, cfg['hcwu_kw'] * 1000
    rooms = [room_key(r) for r in cfg['rooms']]
    power = {'unit_of_measurement': 'W', 'device_class': 'power', 'state_class': 'measurement'}
    sensors = [
        _tpl('hapla_grid_w', "{{ (" + ' + '.join(_num(f'sensor.hapla_grid_l{p}_w') for p in (1, 2, 3)) + ") | round(0) }}", **power),
        _tpl('hapla_load_w', "{{ (" + ' + '.join(_num(f'sensor.hapla_ac_l{p}_w') for p in (1, 2, 3)) + ") | round(0) }}", **power),
        _tpl('hapla_heaters_w', "{{ ((" + ' + '.join(_num(f'sensor.hapla_h{n}_pct') for n in (1, 2, 3))
             + f") / 100 * {hk:g}) | round(0) }}}}", **power),
        _tpl('hapla_hcwu_w', "{{ (" + _num('sensor.hapla_hcwu_pct') + f" / 100 * {ck:g}) | round(0) }}}}", **power),
        _tpl('hapla_pumps_w', "{{ (" + ' + '.join(_num(f'sensor.hapla_m{n}p_w') for n in (1, 2, 3)) + ") | round(0) }}", **power),
        _tpl('hapla_house_w', "{{ [0, " + _num('sensor.hapla_load_w') + ' - ' + _num('sensor.hapla_heaters_w')
             + ' - ' + _num('sensor.hapla_hcwu_w') + ' - ' + _num('sensor.hapla_pumps_w') + "] | max | round(0) }}", **power),
        _tpl('hapla_loops_pct', "{{ ((" + ' + '.join(_num(f'sensor.{k}_pwm') for k in rooms)
             + f") / {len(rooms)}) | round(0) }}}}", unit_of_measurement='%', state_class='measurement'),
        _tpl('hapla_energy_mode_txt',
             "{{ {0: 'Obfitość', 1: 'Normalny', 2: 'Oszczędzanie', 3: 'Kryzys'}"
             ".get(states('sensor.hapla_energy_mode') | int(-1), 'brak danych') }}"),
        _tpl('hapla_holidays', holidays_template('next'), icon='mdi:calendar-star',
             attributes={'daty': holidays_template('list')}),
        _tpl('hapla_can_time_source_txt',
             "{{ {0: 'brak', 1: 'TRK (GPS)', 2: 'NTP'}"
             ".get(states('sensor.hapla_can_time_source') | int(-1), 'brak danych') }}"),
    ]
    binary = [_tpl(f'hapla_flow_{k}', v) for k, v in FLOWS.items()]
    for k, bit in (('can_a', 1), ('can_b', 2)):
        binary.append(_tpl(f'hapla_{k}', "{{ states('sensor.hapla_can_status') | int(0) | bitwise_and("
                           + str(bit) + ") > 0 }}", device_class='connectivity'))
    binary.append(_tpl('hapla_can_time_ok', "{{ states('sensor.hapla_can_status') | int(0) | bitwise_and(4) > 0 }}"))
    binary += [
        _tpl('hapla_ess_ok', "{{ 0 <= states('sensor.hapla_ess_age_s') | int(32767) < " + str(ESS_STALE_S) + " }}",
             device_class='connectivity'),
        _tpl('hapla_alarm',
             "{{ states('sensor.hapla_legionella_alarm') | int(0) == 98"
             " or states('sensor.hapla_recuperator_alarm') | int(0) > 0"
             " or states('sensor.hapla_forecast_alarm') | int(0) > 0"
             " or states('sensor.hapla_wm_alarm') | int(0) > 0"
             " or (" + ' + '.join(f"states('sensor.{k}_dew') | int(0)" for k in rooms) + ") > 0 }}",
             device_class='problem'),
    ]
    customize = {
        'sensor.hapla_grid_w': {'friendly_name': 'Sieć (+pobór / −oddawanie)'},
        'sensor.hapla_load_w': {'friendly_name': 'Odbiory AC razem'},
        'sensor.hapla_heaters_w': {'friendly_name': 'Grzałki bufora'},
        'sensor.hapla_hcwu_w': {'friendly_name': 'Grzałka CWU – moc'},
        'sensor.hapla_pumps_w': {'friendly_name': 'Pompy'},
        'sensor.hapla_house_w': {'friendly_name': 'Dom – pozostałe odbiory'},
        'sensor.hapla_loops_pct': {'friendly_name': 'Pętle podłogówki – średnio'},
        'sensor.hapla_energy_mode_txt': {'friendly_name': 'Tryb energetyczny'},
        'binary_sensor.hapla_ess_ok': {'friendly_name': 'Łącze z Victron ESS'},
        'binary_sensor.hapla_alarm': {'friendly_name': 'Alarm HAPLA'},
        'sensor.hapla_can_time_source_txt': {'friendly_name': 'CAN – źródło czasu'},
        'sensor.hapla_holidays': {'friendly_name': 'Najbliższe święto'},
        'binary_sensor.hapla_can_a': {'friendly_name': 'CAN – gałąź A'},
        'binary_sensor.hapla_can_b': {'friendly_name': 'CAN – gałąź B'},
        'binary_sensor.hapla_can_time_ok': {'friendly_name': 'CAN – czas ważny'},
    }
    return {
        'input_select': {'hapla_program': {
            'name': 'Program domu', 'options': list(PROGRAMS), 'icon': 'mdi:home-switch'}},
        'input_datetime': {
            'hapla_vacation_return': {
                'name': 'Powrót z urlopu', 'has_date': True, 'has_time': True, 'icon': 'mdi:airplane-landing'},
            'hapla_invoice_date': {
                'name': 'Faktura – saldo na dzień', 'has_date': True, 'has_time': False,
                'icon': 'mdi:file-document-outline'}},
        # Puste pole = lista domyślna (jak zera w BRIDGE).
        # Faktura: saldo depozytu na dzień (data dowolna — faktury nie przychodzą 1. dnia).
        # Saldo −1 (= min, wartość nowego pola) = brak faktury: data nowego pola to dziś, nie faktura.
        'input_number': {
            'hapla_offset': {
                'name': 'Cieplej / chłodniej', 'min': cfg['offset_min'], 'max': cfg['offset_max'],
                'step': 1, 'unit_of_measurement': 'K', 'icon': 'mdi:thermometer', 'mode': 'box'},
            'hapla_invoice_balance': {
                'name': 'Faktura – saldo depozytu (−1 = brak faktury)', 'min': -1, 'max': 32767, 'step': 1,
                'unit_of_measurement': 'zł', 'icon': 'mdi:cash-multiple', 'mode': 'box'}},
        'input_text': {
            'hapla_holidays_fixed': {'name': 'Święta stałe (D.MM, przecinki)', 'max': 255,
                                     'icon': 'mdi:calendar-star'},
            'hapla_holidays_easter': {'name': 'Święta ruchome (dni po Wielkanocy)', 'max': 100,
                                      'icon': 'mdi:egg-easter'}},
        'template': [{'sensor': sensors}, {'binary_sensor': binary}],
        'homeassistant': {'customize': customize},
    }


def build_automation(cfg: dict) -> dict:
    lo, hi = cfg['offset_min'], cfg['offset_max']
    step = lambda d: [{  # noqa: E731
        'action': 'input_number.set_value', 'target': {'entity_id': 'input_number.hapla_offset'},
        'data': {'value': f"{{{{ [[states('input_number.hapla_offset') | int(0) {d}, {lo}] | max, {hi}] | min }}}}"},
    }]
    return {
        'script': {
            'hapla_warmer': {'alias': 'HAPLA – cieplej', 'icon': 'mdi:thermometer-plus', 'sequence': step('+ 1')},
            'hapla_cooler': {'alias': 'HAPLA – chłodniej', 'icon': 'mdi:thermometer-minus', 'sequence': step('- 1')},
        },
        'automation': [
            {
                'id': 'hapla_apply_setpoints',
                'alias': 'HAPLA – zapis nastaw do BRIDGE',
                'description': 'Po zmianie programu lub przesunięcia, po starcie HA i co 5 min '
                               '(BRIDGE po restarcie ma rooms[i].tz = 0).',
                'mode': 'queued', 'max': 2,
                'triggers': [
                    {'trigger': 'state', 'entity_id': ['input_select.hapla_program', 'input_number.hapla_offset']},
                    {'trigger': 'homeassistant', 'event': 'start'},
                    {'trigger': 'time_pattern', 'minutes': '/5'},
                ],
                'actions': [{'action': 'script.hapla_apply_setpoints'}],
            },
            {
                'id': 'hapla_write_holidays',
                'alias': 'HAPLA – zapis świąt do BRIDGE',
                'description': 'Po zmianie list, po starcie HA i co 15 min (BRIDGE po restarcie ma zera = '
                               'lista domyślna). Czyta je kalendarz ST i W4 (taryfa: dzień wolny).',
                'mode': 'queued', 'max': 2,
                'triggers': [
                    {'trigger': 'state', 'entity_id': ['input_text.hapla_holidays_fixed',
                                                       'input_text.hapla_holidays_easter']},
                    {'trigger': 'homeassistant', 'event': 'start'},
                    {'trigger': 'time_pattern', 'minutes': '/15'},
                ],
                'actions': [
                    {'action': 'modbus.write_register', 'data': {
                        'hub': HUB, 'slave': cfg['bridge']['unit'], 'address': HOLIDAY_FIXED_REG,
                        'value': holiday_regs_template('fixed')}},
                    {'action': 'modbus.write_register', 'data': {
                        'hub': HUB, 'slave': cfg['bridge']['unit'], 'address': HOLIDAY_EASTER_REG,
                        'value': holiday_regs_template('easter')}},
                ],
            },
            {
                'id': 'hapla_write_invoice',
                'alias': 'HAPLA – zapis faktury (depozyt) do BRIDGE',
                'description': 'Data i saldo depozytu z ostatniej faktury → deposit.inv_* (%MW9130). '
                               'W4 kotwiczy nimi księgę z licznika i pokazuje różnicę. Saldo −1 = brak faktury.',
                'mode': 'queued', 'max': 2,
                'triggers': [
                    {'trigger': 'state', 'entity_id': ['input_datetime.hapla_invoice_date',
                                                       'input_number.hapla_invoice_balance']},
                    {'trigger': 'homeassistant', 'event': 'start'},
                    {'trigger': 'time_pattern', 'minutes': '/15'},
                ],
                'actions': [{'action': 'modbus.write_register', 'data': {
                    'hub': HUB, 'slave': cfg['bridge']['unit'], 'address': DEPOSIT_REG,
                    'value': INVOICE_REGS_TEMPLATE}}],
            },
            {
                'id': 'hapla_vacation_return',
                'alias': 'HAPLA – koniec urlopu',
                'description': f"{cfg['vacation_preheat_h']} h przed powrotem wraca program Normalny "
                               '(podłogówka z buforem potrzebuje czasu na nagrzanie). Data powrotu '
                               'z przeszłości = brak automatycznego powrotu.',
                'triggers': [{'trigger': 'template', 'value_template':
                              "{% set ret = states('input_datetime.hapla_vacation_return') | as_datetime %}"
                              "{{ is_state('input_select.hapla_program', 'Urlop') and ret is not none and "
                              f"ret | as_local - timedelta(hours={cfg['vacation_preheat_h']}) <= now() < ret | as_local }}}}"}],
                'actions': [{'action': 'input_select.select_option',
                             'target': {'entity_id': 'input_select.hapla_program'},
                             'data': {'option': 'Normalny'}}],
            },
        ],
    }

# endregion
# region ─── Schemat przepływu (SVG) ─────────────────────────────────────────

VB_W, VB_H = 1200, 720
BOXES = {   # x, y, w, h, tytuł
    'pv':     (40, 30, 200, 90, 'PV 24,4 kWp'),
    'grid':   (40, 300, 200, 90, 'Sieć'),
    'batt':   (40, 570, 200, 110, 'Bateria 40 kWh'),
    'bus':    (330, 280, 160, 130, 'Victron – AC'),
    'house':  (330, 570, 160, 90, 'Dom'),
    'heat':   (560, 40, 170, 90, 'Grzałki bufora'),
    'pumps':  (560, 320, 170, 70, 'Pompy'),
    'hcwu':   (560, 440, 170, 80, 'Grzałka CWU'),
    'manif':  (1010, 200, 170, 110, 'Podłogówka'),
    'rec':    (980, 575, 200, 140, 'Rekuperator'),
}
TANKS = {   # x, y, w, h, tytuł
    'buffer': (790, 30, 130, 300, 'Bufor CO 1000 l'),
    'cwu':    (790, 420, 130, 200, 'CWU'),
}
ELEC, HOT, COLD = '#f2b705', '#e8590c', '#1c7ed6'
# nakładka → [(ścieżka, kolor)]; kierunek ścieżki = kierunek przepływu
PATHS = {
    'pv':       [('M240,75 H285 V300 H330', ELEC)],
    'grid_in':  [('M240,345 H330', ELEC)],
    'grid_out': [('M330,345 H240', ELEC)],
    'batt_chg': [('M330,390 H285 V615 H240', ELEC)],
    'batt_dis': [('M240,615 H285 V390 H330', ELEC)],
    'house':    [('M410,410 V570', ELEC)],
    'heaters':  [('M490,300 H525 V85 H560', ELEC), ('M730,85 H790', HOT)],
    'pumps':    [('M490,355 H560', ELEC)],
    'hcwu':     [('M490,390 H525 V480 H560', ELEC), ('M730,480 H790', HOT)],
    'co':       [('M920,70 H1095 V200', HOT), ('M1010,285 H920', COLD)],
    'cwu_circ': [('M920,450 H1100 V555 H920', HOT)],
}
# etykiety: (encja, x, y)
LABELS = [
    ('sensor.hapla_pv_w', 140, 95),
    ('sensor.hapla_grid_w', 140, 365),
    ('sensor.hapla_soc', 140, 625),
    ('sensor.hapla_batt_w', 140, 655),
    ('sensor.hapla_load_w', 410, 360),
    ('sensor.hapla_energy_mode_txt', 410, 205),
    ('sensor.hapla_house_w', 410, 625),
    ('sensor.hapla_heaters_w', 645, 105),
    ('sensor.hapla_pumps_w', 645, 370),
    ('sensor.hapla_hcwu_w', 645, 500),
    ('sensor.hapla_tb1_c', 855, 85),
    ('sensor.hapla_tb2_c', 855, 180),
    ('sensor.hapla_tb3_c', 855, 280),
    ('sensor.hapla_t_buf_target_c', 855, 398),
    ('sensor.hapla_m1_c', 990, 55),
    ('sensor.hapla_m1_pct', 990, 95),
    ('sensor.hapla_mix_pct', 1140, 120),
    ('sensor.hapla_m4_c', 1140, 170),
    ('sensor.hapla_loops_pct', 1095, 265),
    ('sensor.hapla_m3_c', 1060, 305),
    ('sensor.hapla_m2_c', 960, 305),
    ('sensor.hapla_tcwu_c', 855, 520),
    ('sensor.hapla_m2_pct', 1010, 470),
    ('sensor.hapla_t_out_c', 1080, 400),
    ('sensor.hapla_rec_out_c', 1030, 650),
    ('sensor.hapla_rec_sup_c', 1130, 650),
    ('sensor.hapla_rec_ext_c', 1030, 698),
    ('sensor.hapla_rec_exh_c', 1130, 698),
]
STATIC_TEXT = [   # (x, y, tekst)
    (990, 125, 'pompa CO'), (1140, 145, 'mieszacz'), (1010, 328, 'powrót'),
    (1010, 495, 'cyrkulacja'), (1080, 380, 'na zewnątrz'), (855, 378, 'cel bufora'),
    (410, 180, 'tryb energetyczny (W3)'), (410, 325, 'odbiory AC'),
    (1080, 628, 'zewn. → nawiew'), (1080, 676, 'wywiew → wyrzut'),
]


def _svg(body: str, style: str = '') -> str:
    css = f'<style>{style}</style>' if style else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VB_W} {VB_H}">'
            f'{css}{body}</svg>\n')


def build_svgs() -> dict:
    font = 'font-family="sans-serif" text-anchor="middle"'
    parts = []
    for d in [p for paths in PATHS.values() for p, _ in paths]:
        parts.append(f'<path d="{d}" fill="none" stroke="#868e96" stroke-opacity="0.45" stroke-width="6"/>')
    for x, y, w, h, title in BOXES.values():
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#868e96" '
                     f'fill-opacity="0.12" stroke="#868e96" stroke-width="2"/>')
        parts.append(f'<text x="{x + w / 2:g}" y="{y + 24}" {font} font-size="18" fill="#868e96">{title}</text>')
    for x, y, w, h, title in TANKS.values():
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="40" fill="url(#tank)" '
                     f'stroke="#868e96" stroke-width="2"/>')
        parts.append(f'<text x="{x + w / 2:g}" y="{y + h + 22}" {font} font-size="16" fill="#868e96">{title}</text>')
    for x, y, text in STATIC_TEXT:
        parts.append(f'<text x="{x}" y="{y}" {font} font-size="13" fill="#868e96">{text}</text>')
    grad = ('<defs><linearGradient id="tank" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{HOT}" stop-opacity="0.35"/>'
            f'<stop offset="1" stop-color="{COLD}" stop-opacity="0.35"/></linearGradient></defs>')
    out = {'przeplyw.svg': _svg(grad + ''.join(parts))}
    anim = ('.f{fill:none;stroke-width:6;stroke-linecap:round;stroke-dasharray:14 12;'
            'animation:f 1s linear infinite}@keyframes f{to{stroke-dashoffset:-26}}')
    for name, paths in PATHS.items():
        body = ''.join(f'<path class="f" d="{d}" stroke="{c}"/>' for d, c in paths)
        out[f'flow_{name}.svg'] = _svg(body, anim)
    return out

# endregion
# region ─── Rzut parteru (SVG) ──────────────────────────────────────────────

# Obrysy wg osi ścian z rzutu „0. Parter PAB branż.” (współrzędne w pt PDF), klucz = numer strefy
# na rzucie = rooms[].id. Wartość: (punkt etykiety, wielokąt). Na tym rzucie główne wejście (01) jest
# u góry, więc bez obrotu. Strefy połączone w jedną przestrzeń (02, 03, 13, 19) mają tylko obrys.
PLAN = {
    1:  ((516, 207), [(470, 187), (563, 187), (563, 247), (470, 247)]),
    2:  ((516, 285), [(470, 247), (563, 247), (563, 350), (470, 350)]),
    3:  ((638, 245), [(563, 187), (713.5, 187), (713.5, 350), (563, 350)]),
    4:  ((578, 440), [(444, 350), (713.5, 350), (713.5, 577), (444, 577)]),
    5:  ((862, 368), [(713.5, 350), (1012, 350), (1012, 408), (713.5, 408)]),
    6:  ((962, 207), [(911.5, 187), (1012, 187), (1012, 249), (911.5, 249)]),
    7:  ((851, 245), [(789, 187), (911.5, 187), (911.5, 350), (789, 350)]),
    8:  ((751, 245), [(713.5, 187), (789, 187), (789, 350), (713.5, 350)]),
    9:  ((960, 437), [(909, 408), (1012, 408), (1012, 500), (909, 500)]),
    10: ((840, 437), [(771, 408), (909, 408), (909, 500), (771, 500)]),
    12: ((828, 555), [(713.5, 408), (771, 408), (771, 500), (943, 500), (943, 646), (713.5, 646)]),
    13: ((977, 555), [(943, 500), (1012, 500), (1012, 646), (943, 646)]),
    14: ((302, 425), [(264, 350), (307, 350), (307, 408), (341, 408), (341, 468), (264, 468)]),
    15: ((299, 245), [(225, 187), (373, 187), (373, 350), (225, 350)]),
    16: ((421, 245), [(373, 187), (470, 187), (470, 350), (373, 350)]),
    17: ((334, 505), [(225, 468), (341, 468), (341, 408), (444, 408), (444, 577), (225, 577)]),
    19: ((375, 370), [(307, 350), (444, 350), (444, 408), (307, 408)]),
    20: ((942, 285), [(911.5, 249), (973, 249), (973, 350), (911.5, 350)]),
}
PLAN_K, PLAN_M = 1.6, 8                       # skala pt → SVG, margines na grubość ściany
_xs = [x for _, pts in PLAN.values() for x, _ in pts]
_ys = [y for _, pts in PLAN.values() for _, y in pts]
PLAN_W, PLAN_H = round((max(_xs) - min(_xs)) * PLAN_K + 2 * PLAN_M), round((max(_ys) - min(_ys)) * PLAN_K + 2 * PLAN_M)


def _plan_xy(x: float, y: float) -> tuple[float, float]:
    return (x - min(_xs)) * PLAN_K + PLAN_M, (y - min(_ys)) * PLAN_K + PLAN_M


def _plan_poly(pts: list, attrs: str) -> str:
    d = ' '.join(f'{x:g},{y:g}' for x, y in (_plan_xy(*p) for p in pts))
    return f'<polygon points="{d}" {attrs}/>'


def build_plan_svgs(cfg: dict) -> dict:
    """rzut.svg (ściany + numery) i rzut_swiatlo_NN.svg — podświetlenie pokoju z encją light."""
    svg = lambda body: (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {PLAN_W} {PLAN_H}">'  # noqa: E731
                        f'{body}</svg>\n')
    parts = []
    for num, ((lx, ly), pts) in PLAN.items():
        parts.append(_plan_poly(pts, 'fill="#868e96" fill-opacity="0.08" stroke="#868e96" stroke-width="4"'))
        x, y = _plan_xy(lx, ly)
        parts.append(f'<text x="{x:g}" y="{y:g}" font-family="sans-serif" text-anchor="middle" '
                     f'font-size="22" font-weight="700" fill="#868e96">{num:02d}</text>')
    out = {'rzut.svg': svg(''.join(parts))}
    for room in cfg['rooms']:
        if room.get('light') and room['id'] in PLAN:
            out[f"rzut_swiatlo_{room['id']:02d}.svg"] = svg(
                _plan_poly(PLAN[room['id']][1], f'fill="{ELEC}" fill-opacity="0.35"'))
    return out


def build_plan_view(cfg: dict, files: dict) -> dict:
    url = lambda f: f'/local/hapla/{f}?v={hashlib.sha1(files[f].encode()).hexdigest()[:8]}'  # noqa: E731
    pos = lambda x, y: {'left': f'{x / PLAN_W * 100:.1f}%', 'top': f'{y / PLAN_H * 100:.1f}%'}  # noqa: E731
    elements = []
    for room in cfg['rooms']:
        if room['id'] not in PLAN:
            continue
        if room.get('light'):
            elements.insert(0, {
                'type': 'conditional', 'conditions': [{'entity': room['light'], 'state': 'on'}],
                'elements': [{'type': 'image', 'image': url(f"rzut_swiatlo_{room['id']:02d}.svg"),
                              'style': {'left': '50%', 'top': '50%', 'width': '100%'}}]})
        x, y = _plan_xy(*PLAN[room['id']][0])
        k = room_key(room)
        for sensor, dy, size in (('t', 26, '14px'), ('rh', 46, '12px')):
            elements.append({'type': 'state-label', 'entity': f'sensor.{k}_{sensor}',
                             'style': {**pos(x, y + dy), 'font-size': size, 'font-weight': '600'}})
    return {'title': 'Rzut', 'path': 'rzut', 'type': 'sections', 'max_columns': 2, 'sections': [
        {'type': 'grid', 'column_span': 2, 'cards': [
            {'type': 'heading', 'heading': 'Parter', 'icon': 'mdi:floor-plan'},
            {'type': 'picture-elements', 'image': url('rzut.svg'), 'elements': elements,
             'grid_options': {'columns': 'full'}}]}]}

# endregion
# region ─── Dashboardy ──────────────────────────────────────────────────────


def _pos(x: float, y: float) -> dict:
    return {'left': f'{x / VB_W * 100:.1f}%', 'top': f'{y / VB_H * 100:.1f}%'}


def build_flow_dashboard(cfg: dict, www: dict) -> dict:
    # ?v=hash treści: przeglądarka nie trzyma starej wersji z /local/.
    url = lambda f: f'/local/hapla/{f}?v={hashlib.sha1(www[f].encode()).hexdigest()[:8]}'  # noqa: E731
    elements = []
    for name in PATHS:
        elements.append({
            'type': 'conditional',
            'conditions': [{'entity': f'binary_sensor.hapla_flow_{name}', 'state': 'on'}],
            'elements': [{'type': 'image', 'image': url(f'flow_{name}.svg'),
                          'style': {'left': '50%', 'top': '50%', 'width': '100%'}}],
        })
    for entity, x, y in LABELS:
        elements.append({'type': 'state-label', 'entity': entity,
                         'style': {**_pos(x, y), 'font-size': '15px', 'font-weight': '600'}})
    flow = {'type': 'picture-elements', 'image': url('przeplyw.svg'), 'elements': elements}
    detail = {'type': 'entities', 'title': 'Bilans mocy', 'entities': [
        'sensor.hapla_pv_w', 'sensor.hapla_grid_w', 'sensor.hapla_batt_w', 'sensor.hapla_soc',
        'sensor.hapla_load_w', 'sensor.hapla_heaters_w', 'sensor.hapla_hcwu_w',
        'sensor.hapla_pumps_w', 'sensor.hapla_house_w', 'binary_sensor.hapla_ess_ok']}
    return {'title': 'HAPLA – przepływy', 'views': [{
        'title': 'Przepływ prądu i ciepła', 'path': 'przeplyw', 'type': 'panel',
        'cards': [{'type': 'vertical-stack', 'cards': [flow, detail]}],
    }]}


def _rooms_markdown(cfg: dict) -> str:
    lines = ['| Pokój | Temp. | Nastawa | Pętla | RH |', '|---|---:|---:|---:|---:|']
    for room in cfg['rooms']:
        k = room_key(room)
        lines.append(
            f"| {room['name']}{{{{ ' ⚠' if is_state('sensor.{k}_dew', '1') }}}} "
            f"| {{{{ states('sensor.{k}_t') }}}} °C "
            f"| {{{{ states('sensor.{k}_sp') if states('sensor.{k}_sp') | float(0) > 0 "
            f"else states('sensor.{k}_tz') }}}} °C "
            f"| {{{{ states('sensor.{k}_pwm') }}}} % | {{{{ states('sensor.{k}_rh') }}}} % |")
    return '\n'.join(lines)


def build_control_dashboard(cfg: dict, plan: dict) -> dict:
    tile = lambda e, **kw: {'type': 'tile', 'entity': e, **kw}  # noqa: E731
    button = lambda name, icon, script: {  # noqa: E731
        'type': 'button', 'name': name, 'icon': icon, 'show_state': False,
        'tap_action': {'action': 'perform-action', 'perform_action': script}}
    program = {'type': 'grid', 'cards': [
        {'type': 'heading', 'heading': 'Program', 'icon': 'mdi:home-switch'},
        tile('input_select.hapla_program', name='Program domu',
             features=[{'type': 'select-options'}], grid_options={'columns': 'full'}),
        button('Chłodniej', 'mdi:thermometer-minus', 'script.hapla_cooler'),
        tile('input_number.hapla_offset', name='Przesunięcie'),
        button('Cieplej', 'mdi:thermometer-plus', 'script.hapla_warmer'),
        {'type': 'conditional',
         'conditions': [{'condition': 'state', 'entity': 'input_select.hapla_program', 'state': 'Urlop'}],
         'card': {'type': 'entities', 'entities': ['input_datetime.hapla_vacation_return']}},
    ]}
    energy = {'type': 'grid', 'cards': [
        {'type': 'heading', 'heading': 'Energia', 'icon': 'mdi:lightning-bolt'},
        tile('sensor.hapla_energy_mode_txt', name='Tryb'),
        tile('sensor.hapla_reserve_h', name='Rezerwa'),
        tile('sensor.hapla_pv_w', name='PV'),
        tile('sensor.hapla_soc', name='Bateria'),
        tile('sensor.hapla_grid_w', name='Sieć'),
        tile('sensor.hapla_tb1_c', name='Bufor – góra'),
        tile('sensor.hapla_tcwu_c', name='CWU'),
        tile('sensor.hapla_t_out_c', name='Na zewnątrz'),
        tile('binary_sensor.hapla_ess_ok', name='Victron'),
        tile('binary_sensor.hapla_alarm', name='Alarmy'),
    ]}
    rooms = {'type': 'grid', 'cards': [
        {'type': 'heading', 'heading': 'Pokoje', 'icon': 'mdi:floor-plan'},
        {'type': 'markdown', 'content': _rooms_markdown(cfg), 'grid_options': {'columns': 'full'}},
    ]}
    calendar = {'type': 'grid', 'cards': [
        {'type': 'heading', 'heading': 'Święta', 'icon': 'mdi:calendar-star'},
        {'type': 'entities', 'entities': ['input_text.hapla_holidays_fixed', 'input_text.hapla_holidays_easter',
                                          'sensor.hapla_holidays'], 'grid_options': {'columns': 'full'}},
        {'type': 'markdown', 'grid_options': {'columns': 'full'}, 'content':
         "Puste pole = lista domyślna. Dni wolne taryfy i kalendarza sterownika:\n\n"
         "{{ state_attr('sensor.hapla_holidays', 'daty') | join(', ') }}"},
    ]}
    deposit = {'type': 'grid', 'cards': [
        {'type': 'heading', 'heading': 'Depozyt prosumencki', 'icon': 'mdi:cash-multiple'},
        {'type': 'entities', 'grid_options': {'columns': 'full'}, 'entities': [
            'input_datetime.hapla_invoice_date', 'input_number.hapla_invoice_balance']},
        tile('sensor.hapla_deposit_balance_zl', name='Saldo'),
        tile('sensor.hapla_deposit_refundable_zl', name='Zwrot'),
        tile('sensor.hapla_deposit_budget_zl', name='Prąd bez utraty zwrotu'),
        tile('sensor.hapla_deposit_inv_diff_zl', name='Licznik − faktura'),
        tile('sensor.hapla_deposit_meter_age_h', name='Licznik – wiek odczytu'),
        tile('sensor.hapla_deposit_hist_days', name='Historia licznika'),
        {'type': 'markdown', 'grid_options': {'columns': 'full'}, 'content':
         "Saldo z faktury na dzień, którego dotyczy (nie dzień wystawienia). Bez faktury "
         "księga liczy się z licznika sieci i zasad z contract.yaml; zużywa się najstarsze "
         "transze. Z sieci wolno pobrać prąd za saldo minus zwrot — tyle i tak przepadnie."},
    ]}
    sections = [program, energy, rooms, calendar, deposit]
    if cfg.get('cameras'):
        sections.append({'type': 'grid', 'cards': [
            {'type': 'heading', 'heading': 'Kamery', 'icon': 'mdi:cctv'},
            *({'type': 'picture-entity', 'entity': c['entity'], 'name': c['name'],
               'camera_view': 'auto', 'grid_options': {'columns': 'full'}} for c in cfg['cameras'])]})

    # Nastawy bazowe są w hapla_ha.yaml; tu tylko podgląd tego, co poszło do BRIDGE.
    detail = []
    for title, keys in (
        ('Obieg CO', ['tb1_c', 'tb2_c', 'tb3_c', 't_buf_target_c', 'm1_c', 'm4_c', 'm3_c', 'm2_c',
                      'm1_pct', 'mix_pct', 'h1_pct', 'h2_pct', 'h3_pct', 'm1p_w']),
        ('CWU / ZWU', ['tcwu_c', 'hcwu_pct', 'm2_pct', 'm2p_w', 'm3_pct', 'm3p_w', 'water_m3',
                       'circulation_status', 'flush_status', 'legionella_alarm']),
        ('Victron ESS', ['soc', 'batt_w', 'pv_w', 'ac_l1_w', 'ac_l2_w', 'ac_l3_w', 'grid_l1_w',
                         'grid_l2_w', 'grid_l3_w', 'ess_age_s', 'soc_min_cmd', 'grid_sp_w']),
        ('Plan W3', ['energy_mode', 'plan_valid', 'reserve_h', 'soc_target_pct', 'heat_demand_kwh',
                     'pv_d0_kwh', 'pv_d1_kwh', 'pv_d2_kwh', 'forecast_alarm']),
        ('Rekuperator', ['rec_out_c', 'rec_sup_c', 'rec_ext_c', 'rec_exh_c', 'rec_eff_pct',
                         'recuperator_alarm']),
        ('Woda / rolety', ['wm_alarm', 'vacation_mode']),
    ):
        detail.append({'type': 'entities', 'title': title, 'entities': [f'sensor.hapla_{k}' for k in keys]})
    for room in cfg['rooms']:
        k = room_key(room)
        detail.append({'type': 'entities', 'title': room['name'],
                       'entities': [f'sensor.{k}_{s}' for s, _, _ in ROOM_SENSORS]})
    detail.append({'type': 'entities', 'title': 'Magistrala CAN', 'entities': [
        'binary_sensor.hapla_can_a', 'binary_sensor.hapla_can_b', 'sensor.hapla_can_online',
        'sensor.hapla_can_time_source_txt', 'binary_sensor.hapla_can_time_ok', 'sensor.hapla_can_phase_us',
        *(f'sensor.hapla_{k}' for k in ('can_polls_ok', 'can_polls_fail', 'can_writes_fail',
                                        'can_went_offline', 'can_phase_steps'))]})

    return {'title': 'HAPLA', 'views': [
        {'title': 'Dom', 'path': 'dom', 'type': 'sections', 'max_columns': 3, 'sections': sections},
        build_plan_view(cfg, plan),
        {'title': 'Szczegóły', 'path': 'szczegoly', 'cards': detail},
    ]}

# endregion
# region ─── Składanie i zapis ───────────────────────────────────────────────


def build_all(cfg: dict, regs: dict, bregs: dict) -> dict:
    www = build_svgs()
    plan = build_plan_svgs(cfg)
    return {
        'packages': {
            'hapla_modbus': build_modbus(cfg, regs, bregs),
            'hapla_rooms': build_rooms(cfg),
            'hapla_control': build_control(cfg),
            'hapla_automation': build_automation(cfg),
        },
        'dashboards': {
            'hapla_sterowanie': build_control_dashboard(cfg, plan),
            'hapla_przeplyw': build_flow_dashboard(cfg, www),
        },
        'www': www,
        'plan': plan,
    }


HEADER = '# AUTO-GENERATED: import/hapla-ha-addons/config/gen_ha.py z import/hapla-ha-addons/config/hapla_ha.yaml + hapla-regs.csv + bridge regs.json — nie edytuj.\n'


def write_all(out: dict, out_dir: Path) -> None:
    dump = lambda o: HEADER + yaml.safe_dump(o, allow_unicode=True, sort_keys=False, width=1000)  # noqa: E731
    for kind in ('packages', 'dashboards'):
        for name, obj in out[kind].items():
            p = out_dir / kind / f'{name}.yaml'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dump(obj), encoding='utf-8')
    for name, svg in {**out['www'], **out['plan']}.items():
        p = out_dir / 'www' / 'hapla' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(svg, encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cfg', type=Path, default=HERE / 'hapla_ha.yaml')
    ap.add_argument('--regs', type=Path, default=REGS_CSV)
    ap.add_argument('--bridge-regs', type=Path, default=BRIDGE_REGS_JSON)
    ap.add_argument('--out-dir', type=Path, default=HERE / 'build')
    ap.add_argument('--bridge', help='HOST:PORT zamiast bridge.host/port z konfiguracji (np. symulator)')
    args = ap.parse_args(argv)

    cfg = load_cfg(args.cfg)
    if args.bridge:
        cfg = copy.deepcopy(cfg)
        host, _, port = args.bridge.rpartition(':')
        cfg['bridge'].update(host=host, port=int(port))
    out = build_all(cfg, load_regs(args.regs), load_bridge_regs(args.bridge_regs))
    write_all(out, args.out_dir)
    n = len(out['packages']['hapla_modbus']['modbus'][0]['sensors'])
    print(f"[gen_ha] {args.out_dir}: {n} sensorów Modbus, {len(cfg['rooms'])} pokojów, "
          f"{len(out['www'])} SVG, BRIDGE {cfg['bridge']['host']}:{cfg['bridge']['port']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

# endregion
