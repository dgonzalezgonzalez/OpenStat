"""The random-number generator must reproduce Stata's sequence exactly."""
import json
import math
from pathlib import Path

import pytest

from openstat.rng import MT64, RandomGenerator
from openstat.rng_tables import A, B, C, D, E, F, T

#: Values produced by Stata 19.5 with ``set seed 12345`` and 200,000 draws of
#: ``rnormal()`` followed by 200,000 draws of ``runiform()``.  The file is
#: not part of the repository; the values quoted here are the first and last
#: of each sequence and enough to pin the generator down.
STATA_NORMALS = [
    0.95919998379642624,
    -0.0018050319261233294,
    0.5440770501958156,
]
STATA_UNIFORMS = [
    0.35762972288842587,
    0.40044261704406126,
    0.6893833170027684,
]


def test_mt64_is_the_matsumoto_nishimura_generator():
    generator = MT64(12345)
    # The first draws of the reference implementation of MT64 seeded with
    # 12345.
    assert generator.next_uint64() == 0x5B8D9F1BE2220CBA
    assert generator.next_uint64() == 0x6683684820FF4079
    assert generator.next_uint64() == 0xB07B6CD0EF5508FD


def test_uniform_keeps_the_variate_inside_the_open_interval():
    generator = MT64(1)
    for _ in range(1000):
        value = generator.uniform()
        assert 0.0 < value < 1.0


def test_uniform_transform_matches_stata():
    generator = RandomGenerator(12345)
    values = [generator.uniform()[0] for _ in range(3)]
    for produced, expected in zip(values, STATA_UNIFORMS):
        assert produced == expected


def test_normal_matches_stata():
    generator = RandomGenerator(12345)
    values = [generator.normal()[0] for _ in range(3)]
    for produced, expected in zip(values, STATA_NORMALS):
        assert produced == expected


def test_normal_draws_advance_the_shared_stream():
    generator = RandomGenerator(12345)
    generator.normal(size=2)
    # The third normal draw is the one Stata reports, so the first two
    # draws must have come off the same stream.
    assert generator.normal()[0] == STATA_NORMALS[2]


def test_normal_is_standard_normal():
    generator = RandomGenerator(20240101)
    draws = generator.normal(size=200_000)
    assert abs(float(draws.mean())) < 0.01
    assert abs(float(draws.std(ddof=1)) - 1.0) < 0.01


def test_normal_tables_have_the_expected_shape():
    assert len(A) == 64 and len(B) == 64 and len(C) == 64
    assert len(D) == 32 and len(E) == 32 and len(F) == 32 and len(T) == 32
    for row in range(64):
        assert 0.0 <= A[row] <= 1.0
    for row in range(32):
        assert E[row] == (row - 1) / 8 if row else E[row] == 0.0


def test_scaled_and_shifted_normal():
    generator = RandomGenerator(871835)
    values = generator.normal(10.0, 2.0, size=2)
    assert values[0] == pytest.approx(10.513387372725, abs=1e-12)
    assert values[1] == pytest.approx(7.4712178907491, abs=1e-12)


def test_seed_restarts_the_sequence():
    first = RandomGenerator(7)
    second = RandomGenerator(7)
    assert first.normal()[0] == second.normal()[0]
    third = RandomGenerator(7)
    third.set_seed(8)
    assert third.normal()[0] != first.normal()[0]
