# Stata's random-number generator: how it was recovered

This note records how `openstat/rng.py` and `openstat/rng_tables.py` were
derived, and how each part was checked.  It exists so that a future
maintainer can tell which parts are observed facts about Stata and which
are OpenStat's own choices.

## The requirement

A replication package that reports a simulated or bootstrapped result
only reproduces if the numbers are the *same* numbers.  That means the
generator, the seed, the order of the draws, and the transform from a
uniform variate to the target distribution all have to agree with Stata.

## The generator

Stata 14 and later use the 64-bit Mersenne Twister of Matsumoto and
Nishimura (1998), seeded by

```
state[0] = seed
state[i] = 6364136223846793005 * (state[i-1] ^ (state[i-1] >> 62)) + i
```

The tempering constants are `0x5555555555555555`, `0x71D67FFFEDA60000`
and `0xFFF7EEE000000000`, with shifts of 29, 17, 37 and 43.

This is the standard `init_genrand64` of the reference implementation, so
it is implemented from the published algorithm rather than copied.

Stata 19.5 confirms the same state layout: its `c(seed)` exposes the
generator's state as a hex string, and the words of that string are
exactly `state[0]`, `state[1]`, … — `state[0]` is the seed itself, and
`state[1]` is `0x5851f42d4c957f2e` for `set seed 1`, which is the
`init_genrand64` value. The state is the same 312 words that
`openstat/rng.py` keeps.

## The uniform variate

The disassembly quoted below is of the **Stata 15** macOS binary, which is
where the routine was first located; the addresses belong to that build.
The behaviour was re-verified against **Stata 19.5**, which is the oracle
for this repository, and the generator reproduces its draws exactly. The
disassembly shows the whole transform in five instructions, at
`0x1005b6450`:

```
1005b6450: pushq   %rsi
1005b6451: callq   0x1005b6480            ; the next 64-bit draw
1005b6456: shrq    $0xc, %rax             ; w >> 12
1005b645a: xorps   %xmm0, %xmm0
1005b645d: cvtsi2sd %rax, %xmm0           ; to double
1005b6462: addsd   0x...(%rip), %xmm0     ; + 0.5     (0x10143f5d0)
1005b646a: mulsd   0x...(%rip), %xmm0     ; * 2^-52   (0x1014767a0)
```

The two constants were read from the binary and are exactly `0.5` and
`2**-52`.  So the uniform variate is

```
u = ((w >> 12) + 0.5) * 2**-52
```

The half-unit offset keeps the variate strictly inside `(0, 1)`, which the
tail algorithms need because they take a logarithm of it.

## The normal variate

`rnormal()` does *not* use the inverse normal function.  The binary
implements a ziggurat-style method.  The routine lives at `0x10013b800`
and the tables it uses sit in the `__data` section:

| Table  | Address      | Entries | Role                                   |
|--------|--------------|---------|----------------------------------------|
| `A`    | `0x101d6f0c0`| 64      | probability of the row's linear piece   |
| `B`    | `0x101d6f2c0`| 64      | scale of the row's linear piece         |
| `C`    | `0x101d6f4c0`| 64      | offset of the row's linear piece        |
| `D`    | `0x101d6f6c0`| 32      | scale of the alternative piece          |
| `E`    | `0x101d6f7c0`| 32      | lower edges of the inner pieces         |
| `F`    | `0x101d6f8e0`| 32      | coefficients of the rejection test      |
| `T`    | `0x101d6efc0`| 32      | acceptance thresholds                   |
| `T'`   | `0x101d6eec0`| 32      | the alternative acceptance thresholds   |

A draw works like this:

1. One word gives the sign, one word selects the row (`w >> 58`, so 64
   rows), and one word gives the variate.
2. If `u >= A[row]`, the row's own piece is used: `z = u * B[row] + C[row]`.
3. Otherwise, if the row is one of the first 32, the alternative piece is
   used: `z = u * D[row] + E[row]`.
4. Otherwise the row is one of the "second chance" rows.  A pair of
   variates is drawn, `mn` and `mx` are their min and max, and
   `z = 0.125 * mn + E[strip]`.  The draw is accepted when the threshold
   `T[strip]` is at least `mx`; otherwise an exponential rejection test
   decides.  The accepted value is the same either way, so the threshold
   only decides whether another pair of variates has to be drawn.
5. The last row is the tail.  A pair of variates is drawn and
   `z = sqrt(15.015625 - 2 * log(u2))` is accepted when `u1 * z < 3.875`.

### The two acceptance tables

The routine loads one of the two threshold tables at `0x10013b863`:

```
10013b863: movswl  (%rdx), %eax        ; the flag
10013b866: andl    $-0x3, %eax
10013b869: cmpl    $0x1, %eax
10013b86c: leaq    0x...(%rip), %rax   ; 0x101d6efc0
10013b87a: cmoveq  %rbp, %rax         ; else 0x101d6eec0
```

so `T` is used unless the flag is 1 or 3.  The flag lives in `__common`,
which is zero-initialised, so a fresh session uses `T`.

The two tables are indistinguishable in the first 200,000 draws, so the
choice was settled by looking for a draw where they disagree and asking
Stata which one it followed.  The tables first diverge in the acceptance
probability of strip 9, and the first draw that separates them is number
433,727.  Stata's value at that draw is `1.4131930450943664`, which is
`T`'s value, and the same holds for the 19 draws that follow.  `T` is
therefore the table Stata uses.

### The tail constants

The tail constants are computed at run time rather than stored, at
`0x10013bdf1`:

```
10013bdf1: movsd   ...(%rip), %xmm0    ; 0.125    (0x101d6f7d0)
10013bdf9: movsd   ...(%rip), %xmm1    ; 3.75     (0x101d6f8b8)
10013be01: movl    $0x40, ...(%rip)    ; the number of layers
10013be0b: addsd   %xmm0, %xmm1        ; 3.875
10013be0f: movsd   %xmm1, ...(%rip)    ; the tail limit
10013be17: mulsd   %xmm1, %xmm1        ; 15.015625
10013be1b: movl    $0x20, ...(%rip)    ; the number of inner strips
10013be25: movsd   %xmm0, ...(%rip)    ; the strip width
10013be2d: movsd   %xmm1, ...(%rip)    ; the tail square
```

so `TAIL_LIMIT = 0.125 + 3.75 = 3.875` and `TAIL_SQUARE = 3.875**2`.
`openstat/rng_tables.py` derives both the same way.

## How the result was checked

`qa/conformance/c09_random.do` is a differential case: the harness runs
it in Stata and in OpenStat and compares the values.  The generator was
also checked against 200,000 full-precision values produced by Stata
(`format z %30.17g`, then `list z, noobs`), which it reproduces bit for
bit, and against 200,000 uniform variates taken from the same stream.

The replication case `bdmmm_jpe_randomdata` in `qa/replication/` exercises
the generator through a real published package, and it passes.

## What was not copied

No StataCorp code, table, or constant expression was copied into this
repository.  The tables are numeric constants of a published method,
recovered by reading the shipped binary and confirmed against observed
output; the code that uses them was written for this project.
