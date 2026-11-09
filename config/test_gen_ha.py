"""Testy generatora pakietów Home Assistant (gen_ha.py).

Uruchomienie: python3 -m unittest import/hapla-ha-addons/config/test_gen_ha.py
"""
import importlib.util
import itertools
import re
import tempfile
import unittest
from pathlib import Path

import jinja2
import yaml

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('gen_ha', HERE / 'gen_ha.py')
gen_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_ha)

CFG = gen_ha.load_cfg(HERE / 'hapla_ha.yaml')
REGS = gen_ha.load_regs(gen_ha.REGS_CSV)
BREGS = gen_ha.load_bridge_regs(gen_ha.BRIDGE_REGS_JSON)


class Scale(unittest.TestCase):
    def test_unit_with_multiplier(self):
        self.assertEqual(gen_ha.parse_scale('°C ×10'), ('°C', 0.1))
        self.assertEqual(gen_ha.parse_scale('% ×100'), ('%', 0.01))

    def test_plain_unit_and_none(self):
        self.assertEqual(gen_ha.parse_scale('°C'), ('°C', 1))
        self.assertEqual(gen_ha.parse_scale('W'), ('W', 1))
        self.assertEqual(gen_ha.parse_scale('-'), (None, 1))


class Registers(unittest.TestCase):
    def test_room_ambient_is_holding_register_at_stride_10(self):
        r = REGS['rooms[3].to_c']
        self.assertEqual((r['addr'], r['input_type'], r['scale'], r['unit']), (2032, 'holding', 0.01, '°C'))

    def test_marker_and_output_words_are_holding(self):
        self.assertEqual(REGS['vacation_mode']['input_type'], 'holding')
        self.assertEqual(REGS['bridge_ctrl.h1_pct']['input_type'], 'holding')


def modbus_sensors(pkg):
    return {s['name']: s for s in pkg['modbus'][0]['sensors']}


class Rooms(unittest.TestCase):
    def test_order_matches_loop_room_table(self):
        """Pozycja = rooms[i] BRIDGE; kolejność z komentarza _loop_room w hapla-io-map.st."""
        self.assertEqual([r['id'] for r in CFG['rooms']],
                         [1, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 16, 17, 20])


class ModbusPackage(unittest.TestCase):
    pkg = gen_ha.build_modbus(CFG, REGS, BREGS)

    def test_hub_from_config(self):
        hub = self.pkg['modbus'][0]
        self.assertEqual((hub['name'], hub['host'], hub['port']), ('hapla', '192.168.3.2', 502))

    def test_buffer_top_sensor(self):
        s = modbus_sensors(self.pkg)['hapla_tb1_c']
        self.assertEqual((s['address'], s['input_type'], s['scale'], s['precision']), (1004, 'holding', 0.1, 1))
        self.assertEqual((s['unit_of_measurement'], s['device_class']), ('°C', 'temperature'))

    def test_every_room_gets_temperature_and_setpoint(self):
        names = modbus_sensors(self.pkg)
        for i, room in enumerate(CFG['rooms']):
            key = gen_ha.room_key(room)
            self.assertEqual(names[f'{key}_t']['address'], 2000 + 10 * i + 2)
            self.assertEqual(names[f'{key}_tz']['address'], 2000 + 10 * i)

    def test_can_bus_registers(self):
        names = modbus_sensors(self.pkg)
        self.assertEqual((names['hapla_can_status']['address'], names['hapla_can_status']['input_type']),
                         (400, 'input'))
        s = names['hapla_can_phase_us']
        self.assertEqual((s['address'], s['data_type']), (403, 'int32'))
        s = names['hapla_can_polls_fail']
        self.assertEqual((s['address'], s['data_type'], s['state_class']), (407, 'uint32', 'total_increasing'))

    def test_friendly_names_through_customize(self):
        cust = self.pkg['homeassistant']['customize']
        self.assertEqual(cust['sensor.hapla_tb1_c']['friendly_name'], 'Bufor CO – góra')
        self.assertEqual(cust['sensor.hapla_room04_t']['friendly_name'], 'Salon – temperatura')


def render(template, **ctx):
    return jinja2.Environment().from_string(template).render(**ctx).strip()


class Setpoints(unittest.TestCase):
    """Szablon Jinja w skrypcie HA ma liczyć to samo co referencja w Pythonie."""

    def test_reference_values(self):
        salon = next(r for r in CFG['rooms'] if r['id'] == 4)
        f = lambda p, o: gen_ha.setpoint(CFG, salon, p, o)
        self.assertEqual(f('Normalny', 0), 23)
        self.assertEqual(f('Normalny', 2), 25)
        self.assertEqual(f('Normalny', 3), 26)          # tz_max
        self.assertEqual(f('Impreza', 0), 22)           # impreza: -1
        self.assertEqual(f('Nieobecność', 0), 20)       # 23 - 3
        self.assertEqual(f('Urlop', 3), 14)             # urlop ignoruje przesunięcie
        warsztat = next(r for r in CFG['rooms'] if r['id'] == 7)                    # dzien 18
        self.assertEqual(gen_ha.setpoint(CFG, warsztat, 'Nieobecność', 0), 16)     # absence_floor
        # Nieobecność nigdy nie grzeje mocniej niż Normalny z tym samym przesunięciem.
        self.assertEqual(gen_ha.setpoint(CFG, warsztat, 'Nieobecność', -3), 15)

    def test_template_matches_reference(self):
        tpl = gen_ha.setpoint_template(CFG)
        for room, program, offset in itertools.product(
                gen_ha.room_items(CFG), gen_ha.PROGRAMS, range(CFG['offset_min'], CFG['offset_max'] + 1)):
            cfg_room = CFG['rooms'][room['idx']]
            got = render(tpl, program=program, offset=offset, r=room)
            self.assertEqual(int(got), gen_ha.setpoint(CFG, cfg_room, program, offset), (room, program, offset))

    def test_script_writes_each_room_and_vacation_flag(self):
        script = gen_ha.build_rooms(CFG)['script']['hapla_apply_setpoints']
        rep = script['sequence'][0]['repeat']
        self.assertEqual([r['addr'] for r in rep['for_each']], [2000 + 10 * i for i in range(len(CFG['rooms']))])
        vac = script['sequence'][1]['data']
        self.assertEqual(vac['address'], 3016)
        self.assertEqual(render(vac['value'], program='Urlop'), '1')
        self.assertEqual(render(vac['value'], program='Impreza'), '0')


def entity_ids(obj):
    """Wszystkie identyfikatory encji użyte w strukturze dashboardu/pakietu."""
    found = set()
    text = yaml.safe_dump(obj, allow_unicode=True)
    for m in re.finditer(r"\b(sensor|binary_sensor|input_number|input_select|input_datetime|input_text|script|camera)\.([a-z0-9_]+)", text):
        found.add(m.group(0))
    return found


class Holidays(unittest.TestCase):
    """Kalendarz świąt w HA: te same daty co W4/ST, poprawny zapis do rejestrów."""

    @classmethod
    def setUpClass(cls):
        import sys
        ctrl = gen_ha.REPO / 'tools' / 'plc' / 'ctrl'
        sys.path.insert(0, str(ctrl))
        s = importlib.util.spec_from_file_location('w4', ctrl / 'hapla-layer4-forecast.py')
        cls.w4 = importlib.util.module_from_spec(s)
        s.loader.exec_module(cls.w4)

    @staticmethod
    def run_tpl(tpl, fixed='', easter='', today=None):
        import ast
        from datetime import date
        today = today or date(2026, 11, 8)
        texts = {'input_text.hapla_holidays_fixed': fixed, 'input_text.hapla_holidays_easter': easter}
        out = render(tpl, states=lambda e: texts[e], now=lambda: today)
        return ast.literal_eval(out) if out.startswith('[') else out

    def test_defaults_mirror_w4(self):
        self.assertEqual(gen_ha.HOL_FIXED_DEF, self.w4.HOLIDAYS_FIXED_DEFAULT)
        self.assertEqual(gen_ha.HOL_EASTER_DEF, self.w4.HOLIDAYS_EASTER_DEFAULT)
        self.assertEqual((gen_ha.HOLIDAY_FIXED_REG, gen_ha.HOLIDAY_EASTER_REG),
                         (self.w4.HOLIDAY_FIXED_REG, self.w4.HOLIDAY_EASTER_REG))

    def test_dates_match_w4_for_a_century(self):
        from datetime import date, timedelta
        self.w4.set_holidays([], [])
        tpl = gen_ha.holidays_template('list')
        for year in range(2000, 2100):
            got = self.run_tpl(tpl, today=date(year, 6, 1))
            want = [d.strftime('%d.%m.%Y') for y in (year, year + 1)
                    for d in (date(y, 1, 1) + timedelta(n) for n in range(366)) if d.year == y and self.w4.is_holiday(d)]
            self.assertEqual(got, want, year)

    def test_next_holiday_and_custom_lists(self):
        tpl = gen_ha.holidays_template('next')
        from datetime import date
        self.assertEqual(self.run_tpl(tpl, today=date(2026, 11, 8)), '11.11.2026')
        self.assertEqual(self.run_tpl(tpl, fixed='2.05', easter='1', today=date(2027, 3, 1)), '29.03.2027')

    def test_register_values(self):
        fixed = self.run_tpl(gen_ha.holiday_regs_template('fixed'), fixed='1.01, 11.11,  zła, 31.13, 24.12')
        self.assertEqual(fixed, [101, 1111, 1224] + [0] * 13)
        easter = self.run_tpl(gen_ha.holiday_regs_template('easter'), easter='1, 49, x, 60')
        self.assertEqual(easter, [1, 49, 60, 0, 0, 0, 0, 0])
        self.assertEqual(self.run_tpl(gen_ha.holiday_regs_template('fixed')), [0] * 16)   # puste = domyślna


class Invoice(unittest.TestCase):
    def test_invoice_registers(self):
        import ast
        vals = {'input_datetime.hapla_invoice_date': '2026-10-17', 'input_number.hapla_invoice_balance': '1234.0'}
        out = render(gen_ha.INVOICE_REGS_TEMPLATE, states=lambda e: vals[e])
        self.assertEqual(ast.literal_eval(out), [2026, 10, 17, 1234])
        vals['input_number.hapla_invoice_balance'] = '0.0'      # depozyt wyczerpany — prawdziwa faktura
        self.assertEqual(ast.literal_eval(render(gen_ha.INVOICE_REGS_TEMPLATE, states=lambda e: vals[e])), [2026, 10, 17, 0])
        # Nowe pole: HA wstawia dzisiejszą datę, saldo = min (−1) → brak faktury, nie „0 zł dziś”
        vals['input_number.hapla_invoice_balance'] = '-1.0'
        self.assertEqual(ast.literal_eval(render(gen_ha.INVOICE_REGS_TEMPLATE, states=lambda e: vals[e])), [0, 0, 0, 0])
        vals['input_datetime.hapla_invoice_date'] = 'unknown'
        vals['input_number.hapla_invoice_balance'] = '1234.0'
        self.assertEqual(ast.literal_eval(render(gen_ha.INVOICE_REGS_TEMPLATE, states=lambda e: vals[e])), [0, 0, 0, 0])
        self.assertEqual(gen_ha.build_all(CFG, REGS, BREGS)['packages']['hapla_control']
                         ['input_number']['hapla_invoice_balance']['min'], -1)


class Dashboards(unittest.TestCase):
    out = gen_ha.build_all(CFG, REGS, BREGS)

    def defined(self):
        ids = set()
        for name, pkg in self.out['packages'].items():
            for s in pkg.get('modbus', [{}])[0].get('sensors', []):
                ids.add(f"sensor.{s['name']}")
            for block in pkg.get('template', []):
                for kind in ('sensor', 'binary_sensor'):
                    for s in block.get(kind, []):
                        ids.add(f"{kind}.{s['unique_id']}")
            for kind in ('input_number', 'input_select', 'input_datetime', 'input_text', 'script'):
                ids.update(f'{kind}.{k}' for k in pkg.get(kind, {}))
        return ids

    def test_every_used_entity_is_defined(self):
        defined = self.defined()
        for name in ('hapla_sterowanie', 'hapla_przeplyw', 'hapla_sciana'):
            used = entity_ids(self.out['dashboards'][name])
            self.assertFalse(used - defined, f'{name}: niezdefiniowane encje')

    def test_alarm_on_aborted_legionella_not_running(self):
        tpl = next(s['state'] for block in self.out['packages']['hapla_control']['template']
                   for s in block.get('binary_sensor', []) if s['unique_id'] == 'hapla_alarm')
        for code, want in ((0, 'False'), (99, 'False'), (98, 'True')):   # 99 = pasteryzacja w toku
            states = lambda e, c=code: str(c) if e == 'sensor.hapla_legionella_alarm' else '0'  # noqa: E731
            self.assertEqual(render(tpl, states=states), want, code)

    def test_template_entities_reference_defined_entities(self):
        defined = self.defined()
        used = entity_ids(self.out['packages']['hapla_control'])
        self.assertFalse(used - defined)

    def test_each_flow_overlay_has_svg(self):
        dash = yaml.safe_dump(self.out['dashboards']['hapla_przeplyw'])
        images = set(re.findall(r'/local/hapla/([a-z0-9_]+\.svg)', dash))
        self.assertIn('przeplyw.svg', images)
        self.assertTrue(images - {'przeplyw.svg'})
        self.assertEqual(images, set(self.out['www']))

    def test_overlays_are_animated_and_share_viewbox(self):
        base_vb = re.search(r'viewBox="([^"]+)"', self.out['www']['przeplyw.svg']).group(1)
        for name, svg in self.out['www'].items():
            self.assertEqual(re.search(r'viewBox="([^"]+)"', svg).group(1), base_vb, name)
            if name != 'przeplyw.svg':
                self.assertIn('@keyframes', svg, name)

    def test_write_creates_files(self):
        with tempfile.TemporaryDirectory() as d:
            gen_ha.write_all(self.out, Path(d))
            files = sorted(str(p.relative_to(d)) for p in Path(d).rglob('*') if p.is_file())
        self.assertIn('packages/hapla_modbus.yaml', files)
        self.assertIn('dashboards/hapla_przeplyw.yaml', files)
        self.assertIn('dashboards/hapla_sciana.yaml', files)
        self.assertIn('www/hapla/przeplyw.svg', files)


def _view(dashboard):
    return dashboard['views'][0]


class WallDashboard(unittest.TestCase):
    plan = gen_ha.build_plan_svgs(CFG)

    def _cfg(self, **wall):
        c = {**CFG, 'wall_panel': {**(CFG.get('wall_panel') or {}), **wall}}
        return c

    def test_panel_view_single_card(self):
        v = _view(gen_ha.build_wall_dashboard(CFG, self.plan))
        self.assertEqual(v['type'], 'panel')
        self.assertEqual(len(v['cards']), 1)

    def test_landscape_uses_horizontal_stack(self):
        v = _view(gen_ha.build_wall_dashboard(self._cfg(orientation='landscape'), self.plan))
        self.assertEqual(v['cards'][0]['type'], 'horizontal-stack')

    def test_portrait_uses_vertical_stack(self):
        v = _view(gen_ha.build_wall_dashboard(self._cfg(orientation='portrait'), self.plan))
        self.assertEqual(v['cards'][0]['type'], 'vertical-stack')

    def test_hero_without_camera_is_floor_plan(self):
        v = _view(gen_ha.build_wall_dashboard(self._cfg(hero={'camera': None}), self.plan))
        hero = v['cards'][0]['cards'][0]
        self.assertEqual(hero['type'], 'picture-elements')
        self.assertIn('rzut.svg', hero['image'])

    def test_hero_with_camera_is_picture_entity(self):
        v = _view(gen_ha.build_wall_dashboard(
            self._cfg(hero={'camera': 'camera.ogrod_na_dom'}), self.plan))
        hero = v['cards'][0]['cards'][0]
        self.assertEqual(hero['type'], 'picture-entity')
        self.assertEqual(hero['entity'], 'camera.ogrod_na_dom')

    def test_favorites_appear_as_tiles(self):
        v = _view(gen_ha.build_wall_dashboard(self._cfg(favorites=[4, 12, 15]), self.plan))
        used = entity_ids(v)
        self.assertIn('sensor.hapla_room04_t', used)   # Salon
        self.assertIn('sensor.hapla_room12_t', used)   # Sypialnia
        self.assertIn('sensor.hapla_room15_t', used)   # Pracownia


class ClockSensors(unittest.TestCase):
    pkg = gen_ha.build_control(CFG)

    def test_clock_and_date_are_trigger_template(self):
        trig = next(b for b in self.pkg['template'] if 'trigger' in b)
        names = {s['name'] for s in trig['sensor']}
        self.assertEqual(names, {'hapla_clock', 'hapla_date'})
        self.assertEqual(trig['trigger'][0]['trigger'], 'time_pattern')


class TabletCharging(unittest.TestCase):
    def _auto_ids(self, cfg):
        return [a['id'] for a in gen_ha.build_automation(cfg)['automation']]

    def test_absent_when_sensor_or_switch_missing(self):
        self.assertNotIn('hapla_tablet_charge_on', self._auto_ids(CFG))
        cfg = {**CFG, 'wall_panel': {'tablet': {'battery_sensor': 'sensor.x', 'power_switch': None}}}
        self.assertNotIn('hapla_tablet_charge_on', self._auto_ids(cfg))

    def test_present_when_both_set(self):
        cfg = {**CFG, 'wall_panel': {'tablet': {
            'battery_sensor': 'sensor.korytarz_tablet_battery',
            'power_switch': 'switch.eve_energy_korytarz',
            'charge_below': 30, 'charge_above': 80}}}
        autos = gen_ha.build_automation(cfg)['automation']
        on = next(a for a in autos if a['id'] == 'hapla_tablet_charge_on')
        off = next(a for a in autos if a['id'] == 'hapla_tablet_charge_off')
        self.assertEqual(on['triggers'][0]['below'], 30)
        self.assertEqual(off['triggers'][0]['above'], 80)
        self.assertEqual(on['actions'][0]['action'], 'switch.turn_on')
        self.assertEqual(off['actions'][0]['action'], 'switch.turn_off')


if __name__ == '__main__':
    unittest.main()
