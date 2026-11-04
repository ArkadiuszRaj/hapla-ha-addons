#!/usr/bin/env python3
"""Testy hapla_modbus: python3 test_server.py (wymaga aiohttp i pymodbus)."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from pymodbus.client import AsyncModbusTcpClient

import server

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def st(value, age_s=10):
    return {'state': str(value), 'last_reported': (NOW - timedelta(seconds=age_s)).isoformat()}


OPTS = {'unit': 1, 'poll_s': 1, 'max_age_s': 3600,
        'rooms': [{'index': 0, 'temperature': 'sensor.t0', 'humidity': 'sensor.h0'},
                  {'index': 3, 'temperature': 'sensor.t3'}],
        'soil': [{'index': 1, 'moisture': 'sensor.soil1'}]}


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


class BuildRegisters(unittest.TestCase):
    def test_room_and_soil_scales(self):
        regs = server.build_registers({'sensor.t0': st(21.5), 'sensor.h0': st(45),
                                       'sensor.soil1': st(31.2)}, OPTS, NOW)
        self.assertEqual(regs[100], 2150)
        self.assertEqual(regs[101], 4500)
        self.assertAlmostEqual(regs[102] / 100, 9.1, delta=0.1)   # punkt rosy 21,5 °C / 45 % ≈ 9,1 °C
        self.assertEqual(regs[151], 312)
        self.assertEqual(regs[150], 0)
        self.assertEqual(len(regs), 48 + 2)

    def test_negative_temperature_and_no_humidity(self):
        regs = server.build_registers({'sensor.t3': st(-1.25)}, OPTS, NOW)
        self.assertEqual(s16(regs[109]), -125)
        self.assertEqual((regs[110], regs[111]), (0, 0))

    def test_missing_unavailable_stale_give_zero(self):
        for states in ({}, {'sensor.t0': {'state': 'unavailable'}},
                       {'sensor.t0': st(21.0, age_s=7200)}):
            regs = server.build_registers(states, OPTS, NOW)
            self.assertEqual((regs[100], regs[101], regs[102]), (0, 0, 0), states)

    def test_validate_rejects_duplicate_index(self):
        with self.assertRaises(SystemExit):
            server.validate({'rooms': [{'index': 2}, {'index': 2}], 'soil': []})


class ServeOverTcp(unittest.TestCase):
    def test_fc3_and_fc4_serve_same_values(self):
        async def run():
            async def fake_fetch(_session, _token):
                return {'sensor.t0': st(21.5), 'sensor.h0': st(45), 'sensor.soil1': st(31.2)}
            server.fetch_states = fake_fetch
            server.datetime = type('D', (), {'now': staticmethod(lambda tz=None: NOW),
                                             'fromisoformat': staticmethod(datetime.fromisoformat)})
            block = server.ModbusSequentialDataBlock(0, [0] * server.REG_COUNT)
            ctx = server.ModbusServerContext(
                slaves={1: server.ModbusSlaveContext(hr=block, ir=block, zero_mode=True)}, single=False)
            upd = asyncio.create_task(server.updater(block, OPTS, 'x'))
            srv = asyncio.create_task(server.StartAsyncTcpServer(context=ctx, address=('127.0.0.1', 15020)))
            await asyncio.sleep(0.5)
            c = AsyncModbusTcpClient('127.0.0.1', port=15020)
            await c.connect()
            hr = await c.read_holding_registers(100, 3, slave=1)
            ir = await c.read_input_registers(150, 2, slave=1)
            c.close()
            upd.cancel(); srv.cancel()
            return hr.registers, ir.registers
        hr, ir = asyncio.run(run())
        self.assertEqual(hr[:2], [2150, 4500])
        self.assertEqual(ir, [0, 312])


if __name__ == '__main__':
    unittest.main()
