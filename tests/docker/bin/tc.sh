#!/bin/bash

OPTS=""

if [[ -z DURATION ]];
then
  echo "Missing DURATION"
  exit 1
fi

if [[ -v LOSS ]];
then
  OPTS="$OPTS loss $LOSS"
fi

if [[ -v DELAY ]];
then
  OPTS="$OPTS delay ${DELAY}ms ${DELAY_JITTER:-10}ms ${DELAY_CORRELATION:-20}.00"
fi

OPTS="$OPTS rate ${RATE:-1}kbit"

docker ps

for CONTAINER in $CONTAINERS
do
  echo "Running on $CONTAINER: tc qdisc add dev eth0 root netem $OPTS "
  docker exec $CONTAINER tc qdisc add dev eth0 root netem $OPTS
done

cleanup() {
  echo "cleanup!"
  for CONTAINER in $CONTAINERS
  do
    echo "Running on $CONTAINER: tc qdisc del dev eth0 root netem"
    docker exec $CONTAINER tc qdisc del dev eth0 root netem
  done
}

trap 'cleanup' EXIT

sleep $DURATION