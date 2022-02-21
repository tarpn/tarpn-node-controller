FROM python:3.7-slim-bullseye

RUN pip install virtualenv

RUN python3 -m virtualenv /opt/tarpn

WORKDIR /opt/tarpn

RUN ./bin/pip install https://github.com/tarpn/tarpn-node-controller/releases/download/v0.1.4/tarpn-core-0.1.4.tar.gz

CMD [ "./bin/tarpn-node" ]
