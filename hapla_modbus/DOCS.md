# HAPLA Modbus

Serwer Modbus TCP, z którego BRIDGE pobiera czujniki widziane przez Home
Assistant: temperaturę i wilgotność pomieszczeń (TIMMERFLOTTE, Matter) oraz
wilgotność gleby (dwa czujniki Matter w ogrodzie). BRIDGE odpytuje HA co 30 s
pod `192.168.3.20:502`, unit 1 (`tools/plc/ctrl/hapla-regs.json` w repo HAPLA,
`doc/ip_map.md`). Integracja Modbus w HA jest tylko klientem — stąd dodatek.

## Rejestry

| Adres | Wartość | Skala |
|---|---|---|
| `100 + 3i` | `rooms[i].to_c` — temperatura pokoju `i` (0..15) | °C × 100 |
| `101 + 3i` | `rooms[i].rh_pct` — wilgotność | % × 100 |
| `102 + 3i` | `rooms[i].td_c` — punkt rosy, liczony z temperatury i wilgotności | °C × 100 |
| `150 + j` | `soil_moist[j]` — wilgotność gleby (j = 0..1) | % × 10 |

Wartości są INT16 ze znakiem, te same pod FC3 i FC4. **0 = brak czujnika**:
pokój bez odczytu idzie za krzywą grzewczą, brak gleby włącza harmonogram
awaryjny podlewania. Zero dostaje encja nieskonfigurowana, niedostępna,
nienumeryczna albo nieodświeżona dłużej niż `max_age_s`; gdy API HA nie
odpowiada przez 60 s — wszystkie rejestry.

Indeks pokoju `i` to pozycja w `rooms[]` BRIDGE — ta sama, której używa
`tools/ha/hapla_ha.yaml` (nastawy `rooms[i].tz`). Przypisanie pokojów do
indeksów nie jest jeszcze ustalone (OQ-2 w `tools/plc/ctrl/doc/architecture.md`).

## Konfiguracja

```yaml
unit: 1
poll_s: 10          # co ile sekund odczyt stanów z HA
max_age_s: 7200     # starszy odczyt = brak czujnika; 0 = bez limitu
rooms:
  - index: 3
    name: Salon
    temperature: sensor.salon_temperatura
    humidity: sensor.salon_wilgotnosc
soil:
  - index: 0
    name: Trawnik
    moisture: sensor.ogrod_wilgotnosc_gleby
```

Wiek odczytu liczy się z `last_reported` stanu encji (HA 2024.3+), więc czujnik,
który co jakiś czas wysyła tę samą wartość, nie starzeje się.

## Sieć

HA musi mieć adres `192.168.3.20` w podsieci automatyki (rezerwacja DHCP albo
statyczny w Ustawienia → System → Sieć), a port 502 nie może być zajęty przez
inny dodatek. Sieć domowa nie ma dostępu do portu 502 (zapora, `doc/ip_map.md`).

## Testy

`python3 test_server.py` (aiohttp, pymodbus 3.6.9): skale, zera przy braku
danych, odczyt FC3/FC4 przez prawdziwy klient Modbus TCP.
