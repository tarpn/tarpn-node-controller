FROM python:3.7-slim-bullseye

RUN pip install virtualenv

RUN python3 -m virtualenv /opt/tarpn

ADD dist /dist

WORKDIR /opt/tarpn

RUN ./bin/pip install /dist/*.whl

CMD [ "./bin/tarpn-node" ]
