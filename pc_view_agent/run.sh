#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

args=(--port "$(bashio::config 'port')")
if bashio::config.has_value 'token'; then
    args+=(--token "$(bashio::config 'token')")
fi
exec /usr/local/bin/pc-view-agent "${args[@]}"
