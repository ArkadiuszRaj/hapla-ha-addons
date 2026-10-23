ARG BUILD_FROM
FROM ${BUILD_FROM}

# Telemetria cactus-needle wyłączona; model i silnik pobierane przy budowie obrazu,
# w trakcie pracy dodatek nie łączy się z internetem.
ENV NEEDLE_TELEMETRY=0 \
    DO_NOT_TRACK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_OFFLINE=0 \
    HOME=/opt/whistle

RUN pip install --no-cache-dir cactus-needle==3.2.8 wyoming==1.10.2 \
 && python3 -c "import needle; needle.Whistle()" \
 && rm -rf /opt/whistle/.cache/huggingface

ENV HF_HUB_OFFLINE=1

COPY whistle_wyoming.py /opt/whistle/
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
