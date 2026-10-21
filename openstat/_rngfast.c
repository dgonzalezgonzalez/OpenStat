/* The fast path of Stata's random-number generator.
 *
 * Stata's ``rnormal()`` is a ziggurat-style algorithm driven by the 64-bit
 * Mersenne Twister.  In Python the generator costs about twenty
 * microseconds per draw, because each draw pulls three 64-bit words out of
 * the Twister one at a time and then walks the ziggurat row by row.  A
 * program that generates a large variable therefore spends most of its time
 * inside the interpreter rather than in the statistics.
 *
 * This module is the same algorithm in C.  It is a *speed* change only: the
 * sequence of draws is identical to the Python implementation, bit for bit,
 * and the Python implementation stays in place as the reference and as the
 * fallback when the extension is not built.
 *
 * Two details are needed for the bit-for-bit match and are easy to get
 * wrong:
 *
 *   * the compiler must not contract a multiply and an add into a fused
 *     multiply-add, because the rounding differs;
 *   * the rejection test uses ``exp(x) - 1`` and not ``expm1(x)``, which
 *     differ in the last bit.
 *
 * The build sets -ffp-contract=off for the first reason; the second is in
 * the code below.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <math.h>

#include "rngtables.h"

#define NN 312
#define MM 156
#define MATRIX_A 0xB5026F5AA96619E9ULL
#define UPPER_MASK 0xFFFFFFFF80000000ULL
#define LOWER_MASK 0x7FFFFFFFULL

typedef struct {
    uint64_t state[NN];
    int index;
} MT64;

static void mt_seed(MT64 *m, uint64_t seed) {
    m->state[0] = seed;
    for (int i = 1; i < NN; i++) {
        uint64_t previous = m->state[i - 1];
        m->state[i] = 6364136223846793005ULL * (previous ^ (previous >> 62))
                      + (uint64_t)i;
    }
    m->index = NN;
}

static void mt_twist(MT64 *m) {
    uint64_t *s = m->state;
    for (int i = 0; i < NN - MM; i++) {
        uint64_t v = (s[i] & UPPER_MASK) | (s[i + 1] & LOWER_MASK);
        s[i] = s[i + MM] ^ (v >> 1) ^ ((v & 1) ? MATRIX_A : 0);
    }
    for (int i = NN - MM; i < NN - 1; i++) {
        uint64_t v = (s[i] & UPPER_MASK) | (s[i + 1] & LOWER_MASK);
        s[i] = s[i + (MM - NN)] ^ (v >> 1) ^ ((v & 1) ? MATRIX_A : 0);
    }
    uint64_t v = (s[NN - 1] & UPPER_MASK) | (s[0] & LOWER_MASK);
    s[NN - 1] = s[MM - 1] ^ (v >> 1) ^ ((v & 1) ? MATRIX_A : 0);
    m->index = 0;
}

static inline uint64_t mt_next(MT64 *m) {
    if (m->index >= NN) {
        mt_twist(m);
    }
    uint64_t v = m->state[m->index++];
    v ^= (v >> 29) & 0x5555555555555555ULL;
    v ^= (v << 17) & 0x71D67FFFEDA60000ULL;
    v ^= (v << 37) & 0xFFF7EEE000000000ULL;
    v ^= (v >> 43);
    return v;
}

/* Stata keeps the variate away from both endpoints by adding half a unit
   to the scaled top bits of the draw. */
static inline double mt_uniform(MT64 *m) {
    return ((double)(mt_next(m) >> 12) + 0.5) * 0x1p-52;
}

static inline double normal_draw(MT64 *m) {
    double sign = (mt_next(m) >= (1ULL << 63)) ? -1.0 : 1.0;
    int row = (int)(mt_next(m) >> 58);
    double u = mt_uniform(m);

    if (u >= Z_A[row]) {
        return sign * (u * Z_B[row] + Z_C[row]);
    }
    if (row < 32) {
        return sign * (u * Z_D[row] + Z_E[row]);
    }
    if (row < 63) {
        int strip = row - 31;
        int next_strip = row - 30;
        double lower = Z_E[strip];
        double upper = (next_strip >= 32) ? Z_E_OUTER : Z_E[next_strip];
        double threshold = Z_T[strip];
        double coefficient = Z_F[strip];
        double value = 0.0;
        for (int attempt = 0; attempt < 100; attempt++) {
            double first = mt_uniform(m);
            double second = mt_uniform(m);
            double small = first < second ? first : second;
            double large = first < second ? second : first;
            value = Z_STRIP * small + lower;
            if (threshold >= large) {
                return sign * value;
            }
            double exponent = (upper * upper - value * value) / 2.0;
            /* exp(x) - 1, not expm1(x): the two differ in the last bit. */
            double limit = small + coefficient * (exp(exponent) - 1.0);
            if (large <= limit) {
                return sign * value;
            }
        }
        return sign * value;
    }
    /* the outermost row, which falls back on the exponential tail test */
    double value = 0.0;
    for (int attempt = 0; attempt < 100; attempt++) {
        double first = mt_uniform(m);
        double second = mt_uniform(m);
        value = sqrt(Z_TAIL_SQUARE - 2.0 * log(second));
        if (first * value < Z_TAIL_LIMIT) {
            return sign * value;
        }
    }
    return sign * value;
}

/* ------------------------------------------------------------------ */

typedef struct {
    PyObject_HEAD
    MT64 mt;
} Generator;

static PyObject *generator_new(PyTypeObject *type, PyObject *args, PyObject *kw) {
    Generator *g = (Generator *)type->tp_alloc(type, 0);
    if (g) {
        mt_seed(&g->mt, 123456789ULL);
    }
    return (PyObject *)g;
}

static PyObject *generator_seed(Generator *g, PyObject *args) {
    unsigned long long seed;
    if (!PyArg_ParseTuple(args, "K", &seed)) {
        return NULL;
    }
    mt_seed(&g->mt, seed);
    Py_RETURN_NONE;
}

/* The raw standard normal variates.  The caller applies the mean and the
   standard deviation, so that the arithmetic stays in one place and the
   numbers are the same as the Python implementation's. */
static PyObject *generator_normal(Generator *g, PyObject *args) {
    Py_ssize_t count;
    if (!PyArg_ParseTuple(args, "n", &count)) {
        return NULL;
    }
    if (count < 0) {
        PyErr_SetString(PyExc_ValueError, "count must not be negative");
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)(count * sizeof(double)));
    if (!out) {
        return NULL;
    }
    double *buffer = (double *)PyBytes_AS_STRING(out);
    for (Py_ssize_t i = 0; i < count; i++) {
        buffer[i] = normal_draw(&g->mt);
    }
    return out;
}

/* The raw uniform variates, for the same reason. */
static PyObject *generator_uniform(Generator *g, PyObject *args) {
    Py_ssize_t count;
    if (!PyArg_ParseTuple(args, "n", &count)) {
        return NULL;
    }
    if (count < 0) {
        PyErr_SetString(PyExc_ValueError, "count must not be negative");
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)(count * sizeof(double)));
    if (!out) {
        return NULL;
    }
    double *buffer = (double *)PyBytes_AS_STRING(out);
    for (Py_ssize_t i = 0; i < count; i++) {
        buffer[i] = mt_uniform(&g->mt);
    }
    return out;
}

/* One 64-bit word, for the transforms that need the raw stream. */
static PyObject *generator_next_uint64(Generator *g, PyObject *unused) {
    (void)unused;
    return PyLong_FromUnsignedLongLong(mt_next(&g->mt));
}

static PyMethodDef generator_methods[] = {
    {"seed", (PyCFunction)generator_seed, METH_VARARGS,
     "Restart the sequence from the given seed."},
    {"normal", (PyCFunction)generator_normal, METH_VARARGS,
     "Draw count standard normal variates into a buffer of doubles."},
    {"uniform", (PyCFunction)generator_uniform, METH_VARARGS,
     "Draw count uniform variates into a buffer of doubles."},
    {"next_uint64", (PyCFunction)generator_next_uint64, METH_NOARGS,
     "Return the next 64-bit word of the stream."},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject GeneratorType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "openstat._rngfast.Generator",
    .tp_basicsize = sizeof(Generator),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = generator_new,
    .tp_methods = generator_methods,
};

static PyObject *make_generator(PyObject *self, PyObject *args) {
    unsigned long long seed;
    if (!PyArg_ParseTuple(args, "K", &seed)) {
        return NULL;
    }
    Generator *g = (Generator *)GeneratorType.tp_alloc(&GeneratorType, 0);
    if (!g) {
        return NULL;
    }
    mt_seed(&g->mt, seed);
    return (PyObject *)g;
}

static PyMethodDef module_methods[] = {
    {"Generator", (PyCFunction)make_generator, METH_VARARGS,
     "Create a generator with the given seed."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_definition = {
    PyModuleDef_HEAD_INIT,
    "openstat._rngfast",
    "The compiled fast path of Stata's random-number generator.",
    -1,
    module_methods,
};

PyMODINIT_FUNC PyInit__rngfast(void) {
    if (PyType_Ready(&GeneratorType) < 0) {
        return NULL;
    }
    return PyModule_Create(&module_definition);
}
