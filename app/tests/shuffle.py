"""Seeded shuffle: modules, then tests within each module. Off unless a seed is given.

    pytest -p app.tests.shuffle --shuffle-seed 123      (or env BENCH_SHUFFLE_SEED=123)

Every xdist worker collects the same list, so the same seed gives the same order on all of them (xdist itself
aborts if worker collections differ).
"""
import os
import random


def pytest_addoption(parser):
    parser.addoption("--shuffle-seed", default=os.environ.get("BENCH_SHUFFLE_SEED"),
                     help="shuffle test order with this seed (default: env BENCH_SHUFFLE_SEED; unset = no shuffle)")


def pytest_report_header(config):
    seed = config.getoption("--shuffle-seed")
    if seed is not None:
        return f"shuffle seed: {seed}"


def pytest_collection_modifyitems(config, items):
    seed = config.getoption("--shuffle-seed")
    if seed is None:
        return
    rng = random.Random(str(seed))
    mods = {}
    for it in items:
        mods.setdefault(it.nodeid.split("::")[0], []).append(it)
    names = list(mods)
    rng.shuffle(names)
    items[:] = []
    for n in names:
        rng.shuffle(mods[n])
        items.extend(mods[n])
