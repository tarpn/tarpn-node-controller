# Testing with Docker

Build the test image from the project root directory:

> docker build -f tests/docker/Dockerfile -t tarpn/tarpn-test:latest .

Define a network as done in docker-compose.yml, run it

> docker-compose -f tests/docker/docker-compose.yml up

In a separate shell, run this docker-compose to simulate slow links 

> docker-compose -f tests/docker/docker-compose-add-latency.yml up

Now, connect to the unix socket with curl from (yet another) docker container to inspect a node

> docker run -v /tmp/socks:/tmp/socks tarpn/tarpn-test:latest curl --unix-socket /tmp/socks/tarpn-shell-alice.sock "http://dummy/metrics"

The reason for running curl within a container is that there are some issues with a Mac host and linux Docker container
sharing a domain socket. Not sure if it's curl or the socket itself.
