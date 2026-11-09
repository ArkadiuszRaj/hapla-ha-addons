# PC-View agent

Agent monitora systemu z projektu PC-View: raz na sekundę czyta `/proc` i `/sys` hosta
i podaje ostatni odczyt jako JSON na `http://<HA>:9101/metrics` (CPU, rdzenie,
temperatury, RAM, swap, dysk, sieć, opis maszyny).

## Opcje

- `port` — port HTTP (domyślnie 9101).
- `token` — jeśli ustawiony, każde zapytanie musi mieć nagłówek `Authorization: Bearer <token>`.

Dodatek działa na sieci hosta i w przestrzeni PID hosta, dlatego HA pokazuje dla niego
obniżoną ocenę bezpieczeństwa.

## Aktualizacja binarki

Binarka `pc-view-agent` (aarch64, statyczna) pochodzi z repo PC-View, commit `6182ea7`.
Po zmianie agenta: w PC-View zbuduj wersję arm64, skopiuj
`agent/build-arm64/pc-view-agent` tutaj i podnieś `version` w `config.yaml`.
