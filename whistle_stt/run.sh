#!/usr/bin/with-contenv bashio
# shellcheck shell=bash

(
    bashio::net.wait_for 10300
    bashio::discovery "wyoming" "$(bashio::var.json uri "tcp://$(hostname):10300")" > /dev/null \
        && bashio::log.info "Wyoming discovery sent"
) &

exec python3 /opt/whistle/whistle_wyoming.py --uri tcp://0.0.0.0:10300 --options /data/options.json
