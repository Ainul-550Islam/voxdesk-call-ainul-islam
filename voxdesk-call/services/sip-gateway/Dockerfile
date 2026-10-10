FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    kamailio \
    kamailio-tls-modules \
    kamailio-json-modules \
    kamailio-http-async-client-modules \
    kamailio-websocket-modules \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY kamailio.cfg /etc/kamailio/kamailio.cfg
COPY rtpengine.conf /etc/rtpengine/rtpengine.conf

EXPOSE 5060/udp 5060/tcp 5061/tcp

CMD ["kamailio", "-DD", "-E", "-f", "/etc/kamailio/kamailio.cfg"]
