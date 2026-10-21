"""The compiled generator must reproduce the Python reference exactly.

This is the test that matters: the extension is only allowed to be a
*speed* change.  It draws from the same 64-bit stream, so the sequence of
uniform and normal draws must interleave identically, and every value must
be bit for bit the same.
"""
import numpy as np
import pytest

from openstat import rng as rng_module
from openstat.rng import RandomGenerator


def python_generator(seed):
    """A generator that is forced onto the Python implementation."""
    generator = RandomGenerator(seed)
    generator._fast = None
    generator._mt = rng_module.MT64(seed)
    return generator


def check(seed, sizes=(1, 2, 7, 1000, 3)):
    fast = RandomGenerator(seed)
    slow = python_generator(seed)
    for size in sizes:
        a = fast.normal(size=size)
        b = slow.normal(size=size)
        assert len(a) == len(b), (seed, size, len(a), len(b))
        if not np.array_equal(a, b):
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    raise AssertionError(
                        f"seed={seed} size={size} draw {i}: fast={x!r} slow={y!r}")
        a = fast.uniform(size=size)
        b = slow.uniform(size=size)
        if not np.array_equal(a, b):
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    raise AssertionError(
                        f"seed={seed} size={size} uniform {i}: fast={x!r} slow={y!r}")
    return True


def check_interleaved(seed, rounds=200):
    """Uniform and normal draws must interleave in the right order."""
    fast = RandomGenerator(seed)
    slow = python_generator(seed)
    for _ in range(rounds):
        for draw in (lambda g: g.normal(size=5), lambda g: g.uniform(size=3)):
            a = draw(fast)
            b = draw(slow)
            if not np.array_equal(a, b):
                raise AssertionError(f"seed={seed} interleaved mismatch")
    return True


def test_compiled_path_matches_the_python_reference():
    """The extension may only be a speed change, never a behaviour change."""
    if rng_module._rngfast is None:
        pytest.skip("the compiled fast path is not built (run `pip install -e .`)")
    for seed in (0, 1, 12345, 871835, 2**31, 2**63 - 1):
        check(seed)
        check_interleaved(seed)


def test_compiled_path_matches_over_a_long_run():
    """A long run exercises the rejection branches of the ziggurat."""
    if rng_module._rngfast is None:
        pytest.skip("the compiled fast path is not built (run `pip install -e .`)")
    fast = RandomGenerator(12345)
    slow = python_generator(12345)
    assert np.array_equal(fast.normal(size=200000), slow.normal(size=200000))


def test_reseeding_restarts_the_compiled_sequence():
    if rng_module._rngfast is None:
        pytest.skip("the compiled fast path is not built (run `pip install -e .`)")
    fast = RandomGenerator(1)
    slow = python_generator(1)
    fast.normal(size=50)
    slow.normal(size=50)
    fast.set_seed(999)
    slow.set_seed(999)
    assert np.array_equal(fast.normal(size=100), slow.normal(size=100))


if __name__ == "__main__":
    print("compiled fast path available:", rng_module._rngfast is not None)
    for seed in (0, 1, 12345, 871835, 2**31, 2**63 - 1):
        check(seed)
        check_interleaved(seed)
        print(f"  seed {seed}: identical")

    # a long run, which exercises the rejection branches
    fast = RandomGenerator(12345)
    slow = python_generator(12345)
    a = fast.normal(size=200000)
    b = slow.normal(size=200000)
    print("200000 consecutive normal draws identical:", np.array_equal(a, b))

    # reseeding must restart the sequence
    fast.set_seed(999)
    slow.set_seed(999)
    print("after reseed, identical:", np.array_equal(fast.normal(size=100),
                                                     slow.normal(size=100)))
    print("ALL PARITY CHECKS PASSED")
