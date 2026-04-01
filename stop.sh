#!/bin/bash

for pid in $(pidof python3); do
    kill -9 $pid
done
