#!/bin/sh
# Desktop simulation of the firmware on the 60 x 60 cm double-loop table (logic check, not physics).
# Needs g++ (Linux/macOS, or WSL on Windows). VSCALE=0.8 ./run_sim.sh simulates a 20% speed error.
set -e
cd "$(dirname "$0")"
cp ../ojanbot_synapse/ojanbot_synapse.ino main.cpp
cp ../ojanbot_synapse/config.h config.h
g++ -std=c++17 -O1 -w -I. sim.cpp -o sim_bin
./sim_bin | grep -v "task=" | grep -v "^\[state\]"
