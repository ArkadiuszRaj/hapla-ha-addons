# HAPLA PLC

Panel w HA do programów ST dla BRIDGE: podgląd i edycja `tools/plc/ctrl/*.st` (i
`hapla-regs.json`), kompilacja `stxc` + pełna suita testów `test_all.sh`, commit i push do
repozytorium HAPLA oraz wgranie `programs.ilb` do BRIDGE (FC 0x4A + TFTP).

## Pierwsze uruchomienie

1. Uruchom dodatek, otwórz panel **HAPLA PLC** → **Klucze**.
2. Dodaj dwa klucze wdrożeniowe na GitHubie (Settings → Deploy keys):
   - **HAPLA**: z zaznaczonym *Allow write access* (commit i push z panelu),
   - **STXC**: wystarczy odczyt (kompilator budowany z submodułu `import/stxc`).
3. **Pobierz z git** — sparse clone (`tools/plc`, `scripts`, `import/stxc`) w `/data/repo`.
4. **Kompiluj i testuj** — za pierwszym razem buduje też `stxc` (kilka minut na Odroidzie).

## Wgrywanie do BRIDGE

1. **Klucze** → **Utwórz klucz** (ECDSA P-384, zaszyfrowany hasłem; hasło nie jest zapisywane).
2. Z PC, kluczem który już jest w whiteliście BRIDGE, wpisz klucz HA do slotu `key_id`
   (polecenie z gotowymi parametrami jest w oknie **Klucze**).
3. **Wgraj do BRIDGE** jest aktywne tylko dla kompilacji, która przeszła testy i odpowiada
   bieżącym plikom. Po wgraniu BRIDGE przełącza slot programs.ilb (A/B na W25Q).

Dodatek używa sieci hosta: serwer TFTP w BRIDGE odpowiada z nowego portu, czego NAT Dockera
nie przepuszcza. Panel przyjmuje połączenia wyłącznie z proxy ingress Supervisora.

## Opcje

| Opcja | Znaczenie |
|---|---|
| `repo_url`, `stxc_url`, `branch` | repozytoria i gałąź |
| `git_name`, `git_email` | autor commitów z panelu |
| `bridge_host`, `bridge_port`, `bridge_unit` | Modbus TCP BRIDGE |
| `key_id` | slot whitelisty PKEY z kluczem HA |
