#!/bin/bash

# Launcher script for tarpn-node. Launches some number of socat processes before running tarpn-node.
# The arguments for the socat commands are given as SOCAT_ARGS delimited by a pipe (|).

IFS="|"
i=0
for SOCAT_ARG in $SOCAT_ARGS
do
    IFS=" "
    socat -x -d -d $SOCAT_ARG > /var/log/socat-${i}.log 2>&1 &
    IFS="|"
    i=$((i+1))
done

if [[ -v SLEEP ]];
then
    sleep $SLEEP
fi

cleanup() {
    killall socat
}

trap 'cleanup' SIGTERM

if [[ -v DEBUG ]];
then
  exec env PYTHONFAULTHANDLER=true /opt/tarpn/bin/tarpn-node --verbose
else
  exec /opt/tarpn/bin/tarpn-node
fi
