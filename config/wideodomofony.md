# Wideodomofon dla HAPLA — opcje bez chmury

Założenie: wszystko działa lokalnie, bez chmury producenta. Obraz idzie do HA przez RTSP/ONVIF
albo przez natywną integrację HA. Drzwi otwiera przekaźnik w samym domofonie, więc działają też
przy awarii HA.

Stan wiedzy: 2026-10-25. Opis pochodzi z ogólnej wiedzy o produktach; cen i dostępności nie
sprawdzano.

| Urządzenie | Integracja z HA | Klawiatura / RFID / odcisk palca | Przekaźnik zamka | Uwagi |
|---|---|---|---|---|
| Reolink Video Doorbell PoE | Reolink (lokalnie), RTSP/ONVIF | nie | nie | Najtańszy. Daje obraz, dzwonek i detekcję ruchu. Kod trzeba dołożyć osobno. |
| Akuvox E12 / R20 | RTSP + HTTP API / SIP | R20: klawiatura + RFID | tak | Pełny zamiennik zamka szyfrowego z kamerą. |
| DoorBird D11xx / D21xx | DoorBird (oficjalna, lokalnie) | opcjonalne moduły | tak | Droższy, dojrzała integracja. |
| 2N IP Verso / IP Solo | 2N Telekomunikace (oficjalna) | Verso: moduły, w tym odcisk palca | tak | Najwyższa półka. |

Odrzucony: ATLO-KRMFW-VD-856-TUYA. Działa tylko przez chmurę Tuya, nie ma RTSP/ONVIF,
a integracja Tuya w HA zwykle nie pokazuje obrazu z zamków wideo i nie pozwala ich otwierać.

## Zamek w drzwiach

- **Elektrozaczep albo zamek elektromechaniczny** sterowany przekaźnikiem domofonu. Kod i
  breloki działają wtedy bez HA.
- **Przekaźnik sterowany przez HA** (np. Shelly z lokalnym API) to wariant dodatkowy, na
  przykład do otwierania z dashboardu.

## Co trafia do HA

Po podłączeniu:

1. Wpisz encję `camera.*` do `cameras:` w `import/hapla-ha-addons/config/hapla_ha.yaml`.
2. Uruchom `import/hapla-ha-addons/config/deploy.sh`.

Obraz pojawi się w sekcji „Kamery” dashboardu HAPLA. Dzwonek (`binary_sensor` / `event`) można
podpiąć do powiadomień w telefonie.
