#!/usr/bin/env python3
"""HAPLA Modbus — serwer Modbus TCP z czujnikami HA dla BRIDGE.

BRIDGE odpytuje HA (POLL_TCP w hapla-regs.json, D12). Integracja Modbus w HA
jest tylko klientem, więc ten dodatek wystawia stany encji jako rejestry:

  100 + 3i   rooms[i].to_c    °C × 100 (INT16)   i = 0..15
  101 + 3i   rooms[i].rh_pct  %  × 100
  102 + 3i   rooms[i].td_c    °C × 100, punkt rosy liczony z T i RH (Magnus)
  150 + j    soil_moist[j]    %  × 10            j = 0..1

Te same wartości są pod FC3 (holding) i FC4 (input): BRIDGE wybiera funkcję
z klasy rejestru lokalnego (rooms HOLDING, soil_moist INPUT).

0 znaczy „brak czujnika” (BRIDGE: pokój idzie za krzywą, gleba = harmonogram
awaryjny). Zero dostaje encja nieskonfigurowana, niedostępna, nienumeryczna
albo starsza niż max_age_s, a po utracie API HA na 60 s — wszystkie rejestry.
"""
import argparse
import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone

import aiohttp
from pymodbus.datastore import (ModbusSequentialDataBlock, ModbusServerContext,
                                ModbusSlaveContext)
from pymodbus.server import StartAsyncTcpServer

ROOMS_BASE, ROOM_STRIDE, ROOMS_N = 100, 3, 16
SOIL_BASE, SOIL_N = 150, 2
REG_COUNT = 200
API_LOST_S = 60
STATES_URL = 'http://supervisor/core/api/states'

log = logging.getLogger('hapla_modbus')


def dew_point_c(t_c, rh_pct):
    """Punkt rosy (Magnus, a = 17,62, b = 243,12 °C)."""
    g = math.log(rh_pct / 100.0) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g)


def _value(states, entity, now, max_age_s):
    """Liczba ze stanu encji albo None (brak, niedostępna, nienumeryczna, stara)."""
    st = states.get(entity) if entity else None
    if st is None:
        return None
    try:
        v = float(st['state'])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if max_age_s > 0:
        ts = st.get('last_reported') or st.get('last_updated')
        try:
            age = (now - datetime.fromisoformat(ts)).total_seconds()
        except (TypeError, ValueError):
            return None
        if age > max_age_s:
            return None
    return v


def _i16(v):
    return max(-32768, min(32767, int(round(v)))) & 0xFFFF


def build_registers(states, opts, now):
    """{adres: wartość u16} dla wszystkich rejestrów pokojów i gleby."""
    max_age = opts.get('max_age_s', 0)
    regs = {a: 0 for a in range(ROOMS_BASE, ROOMS_BASE + ROOM_STRIDE * ROOMS_N)}
    regs.update({SOIL_BASE + j: 0 for j in range(SOIL_N)})
    for room in opts.get('rooms', []):
        base = ROOMS_BASE + ROOM_STRIDE * room['index']
        t = _value(states, room.get('temperature'), now, max_age)
        if t is None or t == 0.0:
            continue                      # 0 = brak czujnika, nie mylić z 0 °C
        rh = _value(states, room.get('humidity'), now, max_age)
        regs[base] = _i16(t * 100)
        if rh is not None and 0 < rh <= 100:
            regs[base + 1] = _i16(rh * 100)
            regs[base + 2] = _i16(dew_point_c(t, rh) * 100)
    for s in opts.get('soil', []):
        m = _value(states, s.get('moisture'), now, max_age)
        if m is not None and m > 0:
            regs[SOIL_BASE + s['index']] = _i16(min(m, 100.0) * 10)
    return regs


async def fetch_states(session, token):
    async with session.get(STATES_URL, headers={'Authorization': f'Bearer {token}'},
                           timeout=aiohttp.ClientTimeout(total=10)) as r:
        r.raise_for_status()
        return {s['entity_id']: s for s in await r.json()}


async def updater(block, opts, token):
    last_ok = None
    async with aiohttp.ClientSession() as session:
        while True:
            now = datetime.now(timezone.utc)
            try:
                states = await fetch_states(session, token)
                last_ok = now
            except Exception as e:  # noqa: BLE001 — każdy błąd API = brak danych
                log.warning('API HA: %s', e)
                states = None
            if states is not None:
                regs = build_registers(states, opts, now)
            elif last_ok is None or (now - last_ok).total_seconds() > API_LOST_S:
                regs = build_registers({}, opts, now)
            else:
                regs = None                   # krótka przerwa: zostają ostatnie wartości
            if regs is not None:
                for addr, v in regs.items():
                    block.setValues(addr, [v])
            await asyncio.sleep(opts['poll_s'])


def validate(opts):
    for key, n in (('rooms', ROOMS_N), ('soil', SOIL_N)):
        idx = [e['index'] for e in opts.get(key, [])]
        if len(idx) != len(set(idx)) or any(not 0 <= i < n for i in idx):
            raise SystemExit(f'{key}: indeksy muszą być różne i w zakresie 0..{n - 1}: {idx}')


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--options', default='/data/options.json')
    ap.add_argument('--port', type=int, default=502)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    opts = json.load(open(args.options, encoding='utf-8'))
    validate(opts)
    log.info('pokoje: %d, czujniki gleby: %d, unit %d',
             len(opts.get('rooms', [])), len(opts.get('soil', [])), opts['unit'])

    block = ModbusSequentialDataBlock(0, [0] * REG_COUNT)
    slave = ModbusSlaveContext(hr=block, ir=block, zero_mode=True)
    context = ModbusServerContext(slaves={opts['unit']: slave}, single=False)
    task = asyncio.create_task(updater(block, opts, os.environ['SUPERVISOR_TOKEN']))
    try:
        await StartAsyncTcpServer(context=context, address=('0.0.0.0', args.port))
    finally:
        task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
