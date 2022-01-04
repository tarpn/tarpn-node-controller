# Testing with Docker

Build the test image from the project root directory:

> docker build -f tests/docker/Dockerfile -t tarpn/tarpn-test:latest .

Define a network as done in docker-compose.yml, run it

> docker-compose -f tests/docker/docker-compose.yml up

Connect to one of the nodes using a domain socket

> docker run -i -t -v /tmp/socks/:/tmp/socks/ tarpn/tarpn-core:latest nc -U /tmp/socks/tarpn-shell-david.sock 

Inject some network errors using Pumba

> docker run -it --rm  -v /var/run/docker.sock:/var/run/docker.sock gaiaadm/pumba --interval=5m --log-level=info netem --duration 100s loss -p 100 tarpn-node-controller_david_1