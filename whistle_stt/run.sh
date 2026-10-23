#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

args=(--uri tcp://0.0.0.0:10300 --language "$(bashio::config 'language')")
while IFS= read -r kw; do
    [ -n "${kw}" ] && args+=(--keyword "${kw}")
done < <(bashio::config 'keywords')
bashio::config.true 'debug' && args+=(--debug)

(
    bashio::net.wait_for 10300
    bashio::discovery "wyoming" "$(bashio::var.json uri "tcp://$(hostname):10300")" > /dev/null \
        && bashio::log.info "Wyoming discovery sent"
) &

exec python3 /opt/whistle/whistle_wyoming.py "${args[@]}"
