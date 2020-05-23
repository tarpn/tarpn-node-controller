FROM python:3

ADD req.txt /

RUN pip install -r req.txt

CMD [ "python", "--version" ]
