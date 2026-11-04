#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

exec python3 /app/server.py --options /data/options.json
