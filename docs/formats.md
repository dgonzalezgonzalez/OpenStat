# Stata display formats in OpenStat

This document records the numeric display model that `openstat/formats.py`
implements. Everything here was **measured** against Stata 19.5 SE on this
host and, where a rule could not be read off the output, **recovered from the
shipped binary** with REA. No StataCorp source was copied; the model is a
re-implementation of observed behaviour.

The model reproduces Stata's output byte-for-byte on a 385 815-measurement
corpus (350 865 valid formats, 10 338 data points, 1 408 wide values, 12 376
decimal-position probes and 6 346 targeted probes) plus 7 160 measurements
from fresh probes of the width, rounding and overflow rules.

## The `%w.dg` format

`%w.dg` is Stata's *general* numeric format. It picks the notation
(fixed or scientific) and the number of significant digits from the value
and the format, and it uses as few characters as it can while still
preserving the value.

### The pipeline

1. **Overflow.** A double whose magnitude reaches `2**1023` cannot be
   printed. Stata emits a missing marker instead (see below).
2. **Exact decimal.** The value is converted to its *exact* decimal
   expansion and then reduced to at most 19 significant digits. That
   reduction is the only rounding in the model that breaks ties to even;
   every later rounding is half-up.
3. **Fixed notation.** If the value's decimal exponent is at least `-5`
   and it needs at most 19 integer digits, Stata tries fixed notation
   first, from the most precise candidate down to two significant
   digits, and keeps the first candidate that fits the width budget.
4. **Scientific notation.** Otherwise Stata switches to `%e` notation and
   chooses the number of significant digits from the width.

### The width budget

The sign is charged separately from the digits.

| Format | Budget for the numeric text |
|---|---|
| `%w.0g` | `w - 1` |
| `%w.dg` with `d > 0` | `max(w - 1, 3)` |

A candidate's *charge* is the number of integer digits of the **rounded**
value, plus one for the decimal point, plus the number of decimal places.
A candidate is rejected when its charge exceeds the budget.

### The fixed/scientific switch

Stata 19 keeps a value in fixed notation only while it needs at most
**19 integer digits**. That is the one visible difference from Stata 15,
which used fixed notation up to 20 integer digits: Stata 15 renders `1e19`
at `%22.0g` as `1000000000000000000`, Stata 19 renders it as
`1.000000000000000e+19`.

In scientific notation the number of significant digits is:

- `%w.0g`: `w - 4 - exponent_width` — the width is filled with as many
  digits as it can hold.
- `%w.dg` with `d > 0`: `max(floor, min(max(d, 2), w - 4 - exponent_width))`,
  where `floor` is 2 significant digits, or 1 when the exponent needs
  three digits.

The *big value* rule then applies: a value whose integer part is not
representable as such (the rounding carries into a new integer digit, or
the value has more than 19 integer digits) is shown with as many digits as
its integer part needs, provided they still fit the budget.

### Rendering details

- The leading zero of a fraction is dropped: `.5`, never `0.5`.
- A negative sign is prefixed; the sign is not part of the digit budget.
- The exponent is written `e+NN` / `e-NN`, padded to two digits, or to
  three when the exponent itself needs three.
- The `c` suffix (`%w.dgc`) inserts thousands separators into the integer
  part.

## The overflow ladder

A double at or above `2**1023` cannot be shown. Stata's markers come from
the same ladder as the extended missing values `.a` … `.z`:

```
L(n) = 0x7fe0010000000000 + n * 2**40        (n = 0 .. 25)
```

The dta writer in `libstata-se.dylib` builds exactly these bit patterns
(`shl rsi,0x28 ; add 0x7fe0010000000000`), which is how the ladder was
confirmed with REA.

- A value whose raw IEEE-754 bit pattern is *exactly* a ladder entry is a
  genuine extended missing value and prints as `.<letter>`.
- A value whose pattern is *above* a ladder entry but below the next one
  prints as `.<letter>_` — the overflow marker.
- A negative value on the ladder prints as `.`.

## Where each format is used

| Context | Format | Source |
|---|---|---|
| Default display of a `byte`/`int` variable | `%8.0g` | measured |
| Default display of a `long` variable | `%12.0g` | measured |
| Default display of a `float` variable | `%9.0g` | measured |
| Default display of a `double` variable | `%10.0g` | measured |
| Default display of a `strN` variable | `%9s` | measured |
| `r()`/`e()`/`c()` in **expression** position | `%10.0g` | measured |
| `r()`/`e()`/`c()` in **macro** position | `%18.0g`, unpadded | measured + REA |

### The macro format, confirmed in the binary

`` `r(mean)' ``, `` `e(b)' `` and `` `c(pi)' `` expand to *text*: the value
rendered with `%18.0g` with the padding removed. This is not a guess — the
shipped binary names the format string and uses it in exactly the
functions that do the expansion:

| Function | What it does with the format |
|---|---|
| `_retval_get_as_string` | `r()`/`e()`/`c()` value → `_fmt_18p0g` → `_sreforms` → `_trimblnk` |
| `_macsub` | macro substitution → `_fmt_18p0g` → `_sreforms` → `_trimblnk` |
| `_macgen` | `local`/`global x = expr` → `_fmt_18p0g` → `_sreforms` → `_skipblnk` |
| `_macincr` | `local x = x + 1` → `_fmt_18p0g` → `_sreforms` → `_skipblnk` |

The literal at `0x4062e34` is the C string `"%18.0g"`; the compiler-emitted
symbol name for it is `_fmt_18p0g`. `_macgen` picks it conditionally on the
Stata version (`_vernum >= 220`, i.e. Stata 14 and later); earlier versions
used a different format.

Because the value is text, the expansion behaves differently in the two
positions:

```stata
display "[`c(pi)']"      // [3.141592653589793]  -- text, verbatim
display `c(pi)'          // 3.1415927             -- re-parsed, %10.0g
```

`openstat/macros.py` uses `MACRO_FORMAT = "%18.0g"` and strips the padding;
`openstat/engine.py` uses the same rendering for the text that a
`local`/`global` assignment stores.

## Storage types and their default formats

Stata distinguishes five numeric storage types plus string types. A
variable's storage type decides both how many bytes a value keeps and
which display format the variable gets when it is created:

| Storage | Format | Range |
|---|---|---|
| `byte` | `%8.0g` | -127 … 100 |
| `int` | `%8.0g` | -32767 … 32740 |
| `long` | `%12.0g` | -2147483647 … 2147483620 |
| `float` | `%9.0g` | single precision |
| `double` | `%10.0g` | double precision |

Two consequences are easy to get wrong and both are implemented:

- **`generate` creates a `float` variable** unless a type is named.
  `set type float` / `set type double` changes that default. This is why
  `generate x = 1e300` yields `.` (the value overflows a float) and why
  `generate x = 0.585533385208193` loses precision before `summarize` ever
  sees it.
- **An out-of-range integer becomes missing, not a clamped value.**
  `generate byte k = 200` produces a variable of missing values, not the
  value 100.

`recast` changes the storage type but keeps the display format.

## Known residuals

- `pow` cannot be reproduced bit-for-bit by any known algorithm;
  `Math.pow` differs by at most 1 ULP. This is a bounded residual, not a
  bug to chase.
- The overflow ladder is reproduced for the *display* path. The dta
  *writer* in OpenStat does not emit the extended missing values.
