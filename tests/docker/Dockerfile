FROM python:3.7-slim-bullseye

RUN apt-get update; apt-get install -y socat netcat iproute2

RUN pip install virtualenv

RUN python3 -m virtualenv /opt/tarpn

COPY dist /dist

COPY tests/docker/bin /opt/tarpn/bin

WORKDIR /opt/tarpn

RUN ./bin/pip install /dist/*.whl

CMD [ "./bin/tarpn-node" ]
