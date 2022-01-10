FROM python:3.7-slim-bullseye

RUN apt-get update; apt-get install -y socat netcat iproute2 dnsutils curl jq procps vim

RUN curl -sSL https://get.docker.com/ | sh

RUN pip install virtualenv

RUN python3 -m virtualenv /opt/tarpn

WORKDIR /opt/tarpn

RUN ./bin/pip install pytest pytest-timeout

COPY dist /dist

COPY tests/docker/bin ./bin

COPY tests/docker/test_curl.py ./tests/test_curl.py

RUN ./bin/pip install /dist/*.whl

CMD [ "./bin/tarpn-node" ]
