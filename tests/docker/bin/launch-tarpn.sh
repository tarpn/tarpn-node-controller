#!/bin/bash

# Launcher script for tarpn-node. Launches some number of socat processes before running tarpn-node.
# The arguments for the socat commands are given as SOCAT_ARGS delimited by a pipe (|).

IFS="|"
for SOCAT_ARG in $SOCAT_ARGS
do
    IFS=" "
    socat $SOCAT_ARG &
    IFS="|"
done

/opt/tarpn/bin/tarpn-node
