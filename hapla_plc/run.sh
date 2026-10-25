#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

export HOME=/data
exec python3 /app/server.py --options /data/options.json --port "$(bashio::addon.ingress_port)"
