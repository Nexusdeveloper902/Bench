#!/bin/bash
# Reproduce all Sim prototype experiments. Run from repo root.
# Requires: python3, node, npm deps, ngspice 47 + libngspice, openvaf (for e09; set OPENVAf env).
set -u
cd "$(dirname "$0")/.."
mkdir -p results
echo "=== npm deps ==="
npm install --no-audit --no-fund 2>&1 | tail -1
echo "=== e01 embed LED ==="
python3 experiments/e01_embed_led.py 2>&1 | tail -3
echo "=== e02 AVR firmware (node) ==="
node firmware/e02_blink.js > results/e02.json 2>&1; head -c 300 results/e02.json; echo
echo "=== e03 gpio sync sweep ==="
python3 experiments/e03_gpio_sync.py 2>&1 | tail -6
echo "=== e04 adc sync ==="
python3 experiments/e04_adc_sync.py 2>&1 | tail -4
echo "=== e05 threshold ==="
python3 experiments/e05_threshold.py 2>&1 | tail -4
python3 experiments/e05b_miss.py 2>&1 | tail -4
echo "=== e06 multi-instance ==="
python3 experiments/e06_multi.py 2>&1 | tail -4
echo "=== e07 models + edit loop ==="
python3 experiments/e07_models.py 2>&1 | tail -4
echo "=== e08 closed loop (real AVR events) ==="
python3 experiments/e08_closed_loop.py 2>&1 | tail -4
echo "=== e09 OSDI (needs OPENVAf, default /tmp/opencode/openvaf) ==="
python3 experiments/e09_osdi.py 2>&1 | tail -4
echo "=== e10 sync clamp ==="
python3 experiments/e10_sync_clamp.py 2>&1 | tail -4
echo "=== e11 bg mode ==="
python3 experiments/e11_bg.py 2>&1 | tail -3
echo "=== ALL DONE: results/ ==="
ls -la results/
