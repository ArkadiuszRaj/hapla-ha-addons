ARG BUILD_FROM
FROM ${BUILD_FROM}

# Statyczna binarka arm64 z PC-View (agent/build-arm64, `make agent-arm64`) — bez zależności.
COPY pc-view-agent /usr/local/bin/
COPY run.sh /
RUN chmod a+x /run.sh /usr/local/bin/pc-view-agent

CMD ["/run.sh"]
