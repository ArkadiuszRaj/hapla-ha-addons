ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN pip install --no-cache-dir aiohttp==3.11.18 pymodbus==3.6.9

COPY server.py /app/
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
