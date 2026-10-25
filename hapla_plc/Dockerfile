ARG BUILD_FROM
FROM ${BUILD_FROM}

# Kompilator stxc (C++23) budowany w dodatku z klonu repo; testy PLC to Python.
RUN apk add --no-cache g++ make flex bison git openssh-client bash coreutils \
 && pip install --no-cache-dir aiohttp==3.11.18 cryptography==44.0.3 \
        requests==2.32.3 astral==3.2 pymodbus==3.6.9

# CodeMirror 5 w obrazie — panel działa bez internetu.
ARG CM=https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16
RUN mkdir -p /app/www/vendor && cd /app/www/vendor \
 && for f in codemirror.min.js codemirror.min.css addon/mode/simple.min.js \
              addon/search/searchcursor.min.js addon/dialog/dialog.min.js \
              addon/dialog/dialog.min.css addon/search/search.min.js \
              theme/material-darker.min.css; do \
        wget -q "$CM/$f" -O "$(basename $f)" || exit 1; done

# Klucz hosta GitHub przypięty w obrazie (StrictHostKeyChecking).
RUN echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" \
        > /etc/ssh/ssh_known_hosts

COPY server.py /app/
COPY www/ /app/www/
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
