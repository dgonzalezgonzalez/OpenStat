"""Stata's random-number generators.

Stata 14 and later use the 64-bit Mersenne Twister (MT64) of Matsumoto and
Nishimura (1998) as the default generator; earlier versions used the 32-bit
KISS generator.  Reproducing a published result means reproducing the
generator exactly, because the numbers a replication package draws depend on
the seed and on the order in which the draws are taken.

Three details matter for the sequence to match:

* The uniform variate is the top 52 bits of a single 64-bit draw, shifted
  up by half a unit: ``((x >> 12) + 0.5) * 2**-52``.  That keeps the
  variate strictly inside ``(0, 1)`` so that the logarithms taken by the
  tail algorithms are always defined.
* ``rnormal()`` does not use the inverse normal function.  It uses the
  ziggurat-style method whose constants live in
  :mod:`openstat.rng_tables`, and the implementation below reproduces
  Stata's draws bit for bit.
* The other non-uniform generators transform uniform variates, and Stata's
  transforms are the ones documented for the generator rather than the
  ones a particular library happens to use.
"""
from __future__ import annotations

import math

import numpy as np

from . import rng_tables

try:  # the compiled fast path; optional
    from . import _rngfast
except ImportError:  # pragma: no cover - depends on the build
    _rngfast = None

_MASK64 = (1 << 64) - 1


class MT64:
    """The 64-bit Mersenne Twister of Matsumoto and Nishimura (1998)."""

    NN = 312
    MM = 156
    MATRIX_A = 0xB5026F5AA96619E9
    UPPER_MASK = 0xFFFFFFFF80000000
    LOWER_MASK = 0x7FFFFFFF

    def __init__(self, seed: int = 123456789):
        self._state = [0] * self.NN
        self._index = self.NN
        self.seed(seed)

    def seed(self, value: int) -> None:
        """Restart the sequence from ``value``."""
        state = self._state
        state[0] = value & _MASK64
        for index in range(1, self.NN):
            previous = state[index - 1]
            state[index] = (6364136223846793005 * (previous ^ (previous >> 62)) + index) & _MASK64
        self._index = self.NN

    def next_uint64(self) -> int:
        """Return the next 64-bit draw."""
        if self._index >= self.NN:
            self._twist()
        value = self._state[self._index]
        self._index += 1
        value ^= (value >> 29) & 0x5555555555555555
        value ^= (value << 17) & 0x71D67FFFEDA60000
        value ^= (value << 37) & 0xFFF7EEE000000000
        value ^= (value >> 43)
        return value & _MASK64

    def _twist(self) -> None:
        state = self._state
        for index in range(self.NN - self.MM):
            value = (state[index] & self.UPPER_MASK) | (state[index + 1] & self.LOWER_MASK)
            state[index] = state[index + self.MM] ^ (value >> 1) ^ (self.MATRIX_A if value & 1 else 0)
        for index in range(self.NN - self.MM, self.NN - 1):
            value = (state[index] & self.UPPER_MASK) | (state[index + 1] & self.LOWER_MASK)
            state[index] = state[index + (self.MM - self.NN)] ^ (value >> 1) ^ (self.MATRIX_A if value & 1 else 0)
        value = (state[self.NN - 1] & self.UPPER_MASK) | (state[0] & self.LOWER_MASK)
        state[self.NN - 1] = state[self.MM - 1] ^ (value >> 1) ^ (self.MATRIX_A if value & 1 else 0)
        self._index = 0

    def uniform(self) -> float:
        """Return a uniform variate on (0, 1).

        Stata keeps the variate away from both endpoints by adding half a
        unit to the scaled top bits of the draw.
        """
        return ((self.next_uint64() >> 12) + 0.5) * (2.0 ** -52)


class RandomGenerator:
    """Stata's random-number generator, as the engine uses it.

    The engine keeps one generator per session, seeded with the value
    ``set seed`` gives, and every random-number function draws from it in
    the order the program asks for them.
    """

    def __init__(self, seed: int = 123456789):
        self._seed = seed
        if _rngfast is not None:
            # The compiled generator owns the whole 64-bit stream, so that
            # uniform and normal draws interleave exactly as Stata's do.
            self._fast = _rngfast.Generator(seed)
            self._mt = None
        else:
            self._fast = None
            self._mt = MT64(seed)

    @property
    def seed(self) -> int:
        return self._seed

    def set_seed(self, value: int) -> None:
        """Restart the sequence from ``value``."""
        self._seed = value
        if self._fast is not None:
            self._fast.seed(value)
        else:
            self._mt.seed(value)

    # ------------------------------------------------------------------
    # Uniform
    # ------------------------------------------------------------------
    def uniform(self, low: float = 0.0, high: float = 1.0, size: int | None = None) -> np.ndarray:
        """Draw from a uniform distribution, as ``runiform()`` does."""
        count = size or 1
        if self._fast is not None:
            draws = np.frombuffer(self._fast.uniform(count), dtype=np.float64)
        else:
            draws = np.array([self._mt.uniform() for _ in range(count)])
        return low + (high - low) * draws

    def uniform_int(self, low: int, high: int, size: int | None = None) -> np.ndarray:
        """Draw an integer from a uniform distribution."""
        draws = self.uniform(size=size)
        return np.floor(low + (high - low + 1) * draws).astype(np.int64)

    # ------------------------------------------------------------------
    # Normal
    # ------------------------------------------------------------------
    def normal(self, mean: float = 0.0, sd: float = 1.0, size: int | None = None) -> np.ndarray:
        """Draw from a normal distribution, as ``rnormal()`` does.

        Stata does not transform a uniform variate with the inverse normal
        function.  It uses the ziggurat-style method of
        :mod:`openstat.rng_tables`: a table of 64 rows maps a uniform
        variate onto a normal variate, and the rows that reach beyond the
        core of the distribution fall back on rejection tests so that the
        density stays exactly standard normal.

        A draw consumes three words of the generator -- one for the sign,
        one to select the row, and one for the variate -- plus two more
        words for every rejection test that has to be repeated.
        """
        count = size or 1
        if self._fast is not None:
            draws = np.frombuffer(self._fast.normal(count), dtype=np.float64)
        else:
            draws = np.array([self._normal_draw() for _ in range(count)])
        return mean + sd * draws

    def _normal_draw(self) -> float:
        """Return one standard normal variate, as Stata's ``rnormal()`` does."""
        tables = rng_tables
        # The first word carries the sign, the second selects the row of
        # the table, and the third supplies the variate.
        sign = -1.0 if self._mt.next_uint64() >= (1 << 63) else 1.0
        row = self._mt.next_uint64() >> 58
        u = self._mt.uniform()

        if u >= tables.A[row]:
            # The row's own linear piece covers this variate.
            return sign * (u * tables.B[row] + tables.C[row])

        if row < 32:
            # The alternative piece of the row, which reaches from zero
            # out to the row's strip.
            return sign * (u * tables.D[row] + tables.E[row])

        if row < 63:
            # A "second chance" row: the alternative piece has to be
            # accepted by a rejection test before it may be used.
            return sign * self._second_chance(row)

        # The outermost row, which always rejects until the exponential
        # tail test accepts.
        return sign * self._tail()

    def _second_chance(self, row: int) -> float:
        """Return the variate of one of the second-chance rows.

        The row's alternative piece covers the strip that starts at
        ``E[row - 31]``.  A draw is accepted outright when the larger of
        the two variates falls below the row's threshold; otherwise an
        exponential rejection test decides, and the accepted value is the
        same either way.
        """
        tables = rng_tables
        strip = row - 31
        next_strip = row - 30
        lower = tables.E[strip]
        upper = tables.E_OUTER if next_strip >= 32 else tables.E[next_strip]
        threshold = tables.T[strip]
        coefficient = tables.F[strip]
        for _ in range(100):
            first = self._mt.uniform()
            second = self._mt.uniform()
            small = first if first < second else second
            large = second if first < second else first
            value = tables.STRIP * small + lower
            if threshold >= large:
                return value
            # The exponential test: the two variates must fall inside the
            # region under the density that the strip leaves out.
            exponent = (upper * upper - value * value) / 2.0
            limit = small + coefficient * (math.exp(exponent) - 1.0)
            if large <= limit:
                return value
        return value

    def _tail(self) -> float:
        """Return a variate from the outermost row of the table.

        The row accepts an exponential tail variate only when the
        companion uniform variate is small enough, which reproduces the
        normal density out to the far tail.
        """
        tables = rng_tables
        for _ in range(100):
            first = self._mt.uniform()
            second = self._mt.uniform()
            value = math.sqrt(tables.TAIL_SQUARE - 2.0 * math.log(second))
            if first * value < tables.TAIL_LIMIT:
                return value
        return value

    # ------------------------------------------------------------------
    # Other distributions
    # ------------------------------------------------------------------
    def exponential(self, scale: float = 1.0, size: int | None = None) -> np.ndarray:
        """Draw from an exponential distribution."""
        draws = self.uniform(size=size)
        return -scale * np.log1p(-draws)

    def poisson(self, mu: float, size: int | None = None) -> np.ndarray:
        """Draw from a Poisson distribution."""
        draws = self.uniform(size=size)
        return _poisson_from_uniform(draws, mu)

    def binomial(self, n: int, p: float, size: int | None = None) -> np.ndarray:
        """Draw from a binomial distribution."""
        draws = self.uniform(size=size)
        return _binomial_from_uniform(draws, n, p)

    def gamma(self, shape: float, scale: float = 1.0, size: int | None = None) -> np.ndarray:
        """Draw from a gamma distribution."""
        draws = self.uniform(size=size)
        return _gamma_from_uniform(draws, shape) * scale

    def beta(self, a: float, b: float, size: int | None = None) -> np.ndarray:
        """Draw from a beta distribution."""
        draws = self.uniform(size=size)
        return _beta_from_uniform(draws, a, b)

    def chi2(self, df: float, size: int | None = None) -> np.ndarray:
        """Draw from a chi-squared distribution."""
        return self.gamma(df / 2.0, size=size) * 2.0

    def t(self, df: float, size: int | None = None) -> np.ndarray:
        """Draw from a Student t distribution."""
        normal = self.normal(size=size)
        chi = self.chi2(df, size=size)
        return normal / np.sqrt(chi / df)

    def logistic(self, size: int | None = None) -> np.ndarray:
        """Draw from a logistic distribution."""
        draws = self.uniform(size=size)
        return np.log(draws / (1.0 - draws))


# ----------------------------------------------------------------------
# Inverse transforms
# ----------------------------------------------------------------------
def _inverse_normal(probability: float | np.ndarray) -> float | np.ndarray:
    """The inverse normal function, as Stata's ``invnormal()`` computes it.

    Stata uses the Wichura algorithm AS241, which is accurate to about
    sixteen digits.  This is the transform of the *function* ``invnormal()``;
    the generator's ``rnormal()`` does not use it.
    """
    return _as241(np.asarray(probability, dtype=float))


def _as241(p: np.ndarray) -> np.ndarray:
    """Wichura's AS241 algorithm for the inverse normal distribution."""
    q = p - 0.5
    result = np.empty_like(q)
    central = np.abs(q) <= 0.425

    if central.any():
        r = 0.180625 - q[central] * q[central]
        num = (((((((2.5090809287301226727e+3 * r + 3.3430575583588128105e+4) * r
                     + 6.7265770927008700853e+4) * r + 4.5921953931549871457e+4) * r
                   + 1.3731693765509461125e+4) * r + 1.9715909503065514427e+3) * r
                 + 1.3314166789178437745e+2) * r + 3.3871328727963666080e0)
        den = (((((((5.2264952788528545610e+3 * r + 2.8729085735721942674e+4) * r
                     + 3.9307895800092710610e+4) * r + 2.1213794301586595867e+4) * r
                   + 5.3941960214247511077e+3) * r + 6.8718700749205790830e+2) * r
                 + 4.2313330701600911252e+1) * r + 1.0)
        result[central] = q[central] * num / den

    tail = ~central
    if tail.any():
        r = np.where(q[tail] < 0, p[tail], 1.0 - p[tail])
        r = np.sqrt(-np.log(r))
        upper = r <= 5.0
        r2 = r * r
        values = np.empty_like(r)

        near = upper & (r <= 5.0)
        if near.any():
            r1 = r[near]
            r1sq = r1 * r1
            num = (((((((7.74545014278341407640e-4 * r1sq + 0.0227238449892691845833) * r1sq
                         + 0.241780725177450611770) * r1sq + 1.27045825245236838258) * r1sq
                       + 3.64784832476320460504) * r1sq + 5.76949722146069140550) * r1sq
                     + 4.63033784615654529590) * r1sq + 1.42343711074968357734)
            den = (((((((1.05075007164441684324e-9 * r1sq + 5.47593808499534494600e-4) * r1sq
                         + 1.51986665636164571966e-2) * r1sq + 1.48103976427480074590e-1) * r1sq
                       + 1.17631950851018738300e0) * r1sq + 5.37181101862009857789) * r1sq
                     + 1.72170816660061227626) * r1sq + 1.0)
            values[near] = num / den

        far = upper & ~near
        if far.any():
            r1 = r[far]
            r1sq = r1 * r1
            num = (((((((2.01033439929228813265e-7 * r1sq + 2.71155556874348757815e-5) * r1sq
                         + 1.24266094738807843860e-3) * r1sq + 2.65321895265761230930e-2) * r1sq
                       + 2.96560571828504891230e-1) * r1sq + 1.78482653991729133580) * r1sq
                     + 5.46378491116411436990) * r1sq + 6.65790464350110377720)
            den = (((((((2.04426310338993978564e-15 * r1sq + 1.42151175831644588870e-7) * r1sq
                         + 1.84631831751005468180e-5) * r1sq + 7.86869131145613259100e-4) * r1sq
                       + 1.48753612908506148525e-2) * r1sq + 1.36929880922735805310e-1) * r1sq
                     + 5.99832206555887937690e-1) * r1sq + 1.0)
            values[far] = num / den

        result[tail] = np.where(q[tail] < 0, -values, values)
    return result


def _poisson_from_uniform(draws: np.ndarray, mu: float) -> np.ndarray:
    """Invert the Poisson distribution function by search."""
    result = np.zeros(len(draws), dtype=np.int64)
    cumulative = np.exp(-mu)
    total = cumulative
    count = 0
    pending = draws < total
    while pending.any() and count < 10000:
        count += 1
        cumulative *= mu / count
        total += cumulative
        pending = draws < total
    result[pending] = count
    return result


def _binomial_from_uniform(draws: np.ndarray, n: int, p: float) -> np.ndarray:
    """Invert the binomial distribution function by search."""
    result = np.zeros(len(draws), dtype=np.int64)
    cumulative = (1.0 - p) ** n
    total = cumulative
    count = 0
    pending = draws < total
    while pending.any() and count < n:
        count += 1
        cumulative *= (n - count + 1) / count * p / (1.0 - p)
        total += cumulative
        pending = draws < total
    result[pending] = count
    return result


def _gamma_from_uniform(draws: np.ndarray, shape: float) -> np.ndarray:
    """Invert the gamma distribution function by search."""
    result = np.zeros(len(draws))
    for index, value in enumerate(draws):
        low, high = 0.0, max(shape * 10.0, 1.0)
        for _ in range(200):
            middle = (low + high) / 2.0
            if _gamma_cdf(middle, shape) < value:
                low = middle
            else:
                high = middle
        result[index] = (low + high) / 2.0
    return result


def _gamma_cdf(x: float, shape: float) -> float:
    """The regularised lower incomplete gamma function."""
    if x <= 0:
        return 0.0
    from scipy.special import gammainc

    return float(gammainc(shape, x))


def _beta_from_uniform(draws: np.ndarray, a: float, b: float) -> np.ndarray:
    """Invert the beta distribution function by search."""
    result = np.zeros(len(draws))
    from scipy.special import betainc

    for index, value in enumerate(draws):
        low, high = 0.0, 1.0
        for _ in range(100):
            middle = (low + high) / 2.0
            if betainc(a, b, middle) < value:
                low = middle
            else:
                high = middle
        result[index] = (low + high) / 2.0
    return result
