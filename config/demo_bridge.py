#!/usr/bin/env python3
"""
demo_bridge.py — symulator BRIDGE dla trybu demo dashboardów HAPLA w HA.

Serwer Modbus TCP (unit 1) z jedną przestrzenią adresów dla FC03 i FC04,
jak zunifikowana przestrzeń BRIDGE. Co 5 s przelicza prosty model: PV wg pory
dnia, bilans Victron (bateria, sieć, odbiory na 3 fazach), grzałki bufora przy
nadwyżce, temperatury bufora/CWU, pokoje dążące do rooms[i].tz zapisanego
przez HA. To nie jest W2/W3 — tylko wiarygodne liczby do oglądania widoków.

Użycie:
    python3 import/hapla-ha-addons/config/demo_bridge.py [--port 5020]
    python3 import/hapla-ha-addons/config/gen_ha.py --bridge <ip-tego-hosta>:5020   # HA → symulator
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import math
import random
import time
from pathlib import Path

from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('gen_ha', HERE / 'gen_ha.py')
gen_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_ha)

STEP_S = 5


class Model:
    def __init__(self, regs: dict, cfg: dict, block: ModbusSequentialDataBlock):
        self.regs, self.cfg, self.block = regs, cfg, block
        self.soc, self.tb, self.tcwu = 62.0, [58.0, 52.0, 41.0], 51.0
        self.rooms = [r['dzien'] - 0.6 for r in cfg['rooms']]
        self.heaters = 0

    def put(self, name: str, value: float) -> None:
        r = self.regs[name]
        raw = int(round(value / r['scale'])) & 0xFFFF
        self.block.setValues(r['addr'] + 1, [raw])      # +1: adresacja bloku pymodbus

    def get(self, name: str) -> int:
        raw = self.block.getValues(self.regs[name]['addr'] + 1, 1)[0]
        return raw - 0x10000 if raw & 0x8000 else raw

    def step(self) -> None:
        h = time.localtime().tm_hour + time.localtime().tm_min / 60
        pv = max(0.0, math.sin(math.pi * (h - 7.5) / 10)) * 11000 * random.uniform(0.85, 1.0)
        base = 450 + 250 * (18 <= h < 22) + random.uniform(-60, 60)
        pumps = [38.0, 22.0 if 6 <= h < 23 else 0.0, 0.0]

        # Grzałki bufora: nadwyżka PV ponad 7 kW → kolejne 6 kW, do 80 °C na górze.
        surplus = pv - base - sum(pumps)
        want = min(3, max(0, int((surplus - 1000) // 6000))) if self.tb[0] < 80 else 0
        self.heaters = want
        hcwu = 100.0 if self.tcwu < 50 else (0.0 if self.tcwu > 55 else (100.0 if self.tcwu < 52 else 0.0))
        load = base + sum(pumps) + self.heaters * 6000 + hcwu / 100 * self.cfg['hcwu_kw'] * 1000

        batt = pv - load
        batt = min(batt, 5000.0) if self.soc < 100 else min(batt, 0.0)
        batt = max(batt, -5000.0) if self.soc > 20 else max(batt, 0.0)
        grid = load - pv + batt
        self.soc = min(100.0, max(0.0, self.soc + batt * STEP_S / 3600 / 40000 * 100))

        # Bufor 1000 l: +grzałki, −pobór podłogówki; CWU 300 l: +grzałka, −straty.
        draw = 2500 if (h < 7 or h >= 17) else 900
        dt_buf = (self.heaters * 6000 - draw) * STEP_S / (1000 * 4186) * 60     # przyspieszone ×60
        self.tb = [min(85.0, max(25.0, t + dt_buf * k)) for t, k in zip(self.tb, (1.0, 0.8, 0.5))]
        self.tcwu = min(65.0, max(35.0, self.tcwu + (hcwu / 100 * 3000 - 300) * STEP_S / (300 * 4186) * 60))

        for p in ('l1', 'l2', 'l3'):
            self.put(f'ess.ac_{p}_w', load / 3 + random.uniform(-40, 40))
            self.put(f'ess.grid_{p}_w', grid / 3)
        self.put('ess.batt_soc_pct', round(self.soc))
        self.put('ess.batt_power_w', batt)
        self.put('ess.pv_power_w', pv)
        self.put('ess.age_s', random.randint(0, 5))
        for n, t in zip((1, 2, 3), self.tb):
            self.put(f'bridge_sens.tb{n}_c', t)
        self.put('bridge_sens.m1_c', self.tb[0] - 1.5)
        self.put('bridge_sens.m4_c', 31.5)
        self.put('bridge_sens.m3_c', 26.8)
        self.put('bridge_sens.m2_c', 27.4)
        self.put('bridge_sens.m1p_w', pumps[0])
        self.put('bridge_sens.m2p_w', pumps[1])
        self.put('bridge_ctrl.tcwu_c', self.tcwu)
        self.put('bridge_ctrl.m1_pct', 45.0)
        self.put('bridge_ctrl.m2_pct', 30.0 if pumps[1] else 0.0)
        self.put('bridge_ctrl.mix_pct', 38.0)
        for n in (1, 2, 3):
            self.put(f'bridge_ctrl.h{n}_pct', 100.0 if self.heaters >= n else 0.0)
        self.put('bridge_ctrl.hcwu_pct', hcwu)

        for i, room in enumerate(self.cfg['rooms']):
            tz = self.get(f'rooms[{i}].tz') or room['dzien']
            self.rooms[i] += (tz - self.rooms[i]) * 0.02 + random.uniform(-0.02, 0.02)
            err = tz - self.rooms[i]
            self.put(f'rooms[{i}].to_c', self.rooms[i])
            self.put(f'rooms[{i}].rh_pct', 42 + random.uniform(-1, 1))
            self.put(f'room_cmd[{i}].loop_pwm', min(100.0, max(0.0, 30 + 60 * err)))

        self.put('forecast[0].t_c', 6 + 5 * math.sin(math.pi * (h - 9) / 12))
        self.put('recuperator.t_outdoor_c', 7.5)
        self.put('recuperator.t_supply_c', 19.8)
        self.put('recuperator.t_extract_c', 21.6)
        self.put('recuperator.t_exhaust_c', 9.1)
        self.put('w3_diag.rec_efficiency_pct', 87.0)
        self.put('w3_plan.energy_mode', 0 if self.soc > 80 else 1)
        self.put('w3_plan.t_buf_target_c', 60.0)
        self.put('w3_plan.soc_target_pct', 75.0)
        self.put('w3_plan.heat_demand_kwh', 38.5)
        self.put('w3_plan.reserve_h', 31.0 + self.soc / 5)
        self.put('w3_plan.valid', 1)
        self.put('w3_diag.pred_pv_d0_kwh', 41.2)
        self.put('w3_diag.pred_pv_d1_kwh', 18.7)
        self.put('w3_diag.pred_pv_d2_kwh', 55.0)


async def run(port: int) -> None:
    regs = gen_ha.load_regs(gen_ha.REGS_CSV)
    cfg = gen_ha.load_cfg(HERE / 'hapla_ha.yaml')
    block = ModbusSequentialDataBlock(0, [0] * 10000)
    slave = ModbusSlaveContext(hr=block, ir=block, zero_mode=False)
    ctx = ModbusServerContext(slaves={cfg['bridge']['unit']: slave}, single=False)
    model = Model(regs, cfg, block)

    async def tick():
        while True:
            model.step()
            await asyncio.sleep(STEP_S)

    asyncio.get_running_loop().create_task(tick())
    print(f'[demo_bridge] Modbus TCP :{port}, unit {cfg["bridge"]["unit"]}')
    await StartAsyncTcpServer(context=ctx, address=('0.0.0.0', port))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=5020)
    asyncio.run(run(ap.parse_args().port))


if __name__ == '__main__':
    main()
