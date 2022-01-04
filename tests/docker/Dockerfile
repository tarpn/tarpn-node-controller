FROM tarpn/tarpn-core:latest

RUN apt-get update; apt-get install -y socat netcat iproute2

COPY tests/docker/bin /opt/tarpn/bin

WORKDIR /opt/tarpn

CMD [ "./bin/tarpn-node" ]
