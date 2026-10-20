"""
Core Boolean-function representation used by every part of the engine.

A function of n inputs is stored as a *truth vector*: a Python int with 2**n
bits where bit m is 1 iff the function is 1 on minterm m.  Minterm indices use
the textbook convention that the FIRST declared input is the MOST significant
bit, i.e. for F(A,B,C) minterm 5 = 101 = A B' C.

Truth vectors let us evaluate expressions and whole gate-level circuits on all
2**n input combinations at once with ordinary bitwise operators (&, |, ^, ~),
which is both simple and fast.

Cubes (product terms / implicants) are stored as (value, mask) tuples:
    mask bit = 1  -> that variable is absent ("-", don't care)
    mask bit = 0  -> variable present, its polarity is the matching value bit
Bit (n-1-i) of value/mask corresponds to input i, same as minterm indices.
"""

from dataclasses import dataclass, field

MAX_INPUTS = 20          # 2**20 = 1M-bit truth vectors; QM gets slow well before this


# --------------------------------------------------------------------------
# Truth-vector helpers
# --------------------------------------------------------------------------

def full_mask(n):
    """Truth vector of the constant-1 function of n variables."""
    return (1 << (1 << n)) - 1


def var_vector(i, n):
    """Truth vector of input i (0 = MSB) in an n-input space.

    Input i is 1 on minterm m iff bit (n-1-i) of m is 1, which gives the
    repeating pattern 0..0 1..1 with blocks of length 2**(n-1-i).
    """
    half = 1 << (n - 1 - i)
    period = half << 1
    unit = ((1 << half) - 1) << half            # one period: `half` zeros then `half` ones
    repeats = (1 << n) // period
    # sum_{k<repeats} 2**(k*period)  ==  (2**(repeats*period) - 1) / (2**period - 1)
    spread = ((1 << (repeats * period)) - 1) // ((1 << period) - 1)
    return unit * spread


def vector_to_indices(vec):
    """Sorted list of set-bit positions of a truth vector."""
    bits = bin(vec)[2:][::-1]
    return [i for i, c in enumerate(bits) if c == "1"]


def indices_to_vector(indices):
    vec = 0
    for i in indices:
        vec |= 1 << i
    return vec


def popcount(x):
    return bin(x).count("1")


# --------------------------------------------------------------------------
# Cube helpers
# --------------------------------------------------------------------------

def cube_literals(cube, n):
    """Number of literals in a cube (variables that are not don't-care)."""
    return n - popcount(cube[1])


def cube_covers(cube, minterm):
    value, mask = cube
    return (minterm & ~mask) == value


def cube_minterms(cube):
    """All minterms contained in a cube (enumerates the subsets of its mask)."""
    value, mask = cube
    out = []
    sub = mask
    while True:
        out.append(value | sub)
        if sub == 0:
            break
        sub = (sub - 1) & mask
    out.sort()
    return out


def cube_vector(cube, n):
    """Truth vector of a cube."""
    vec = full_mask(n)
    value, mask = cube
    for i in range(n):
        bit = 1 << (n - 1 - i)
        if mask & bit:
            continue
        v = var_vector(i, n)
        vec &= v if value & bit else ~v
    return vec & full_mask(n)


def cube_to_string(cube, n):
    """Espresso-style cube string, e.g. '1-0'."""
    value, mask = cube
    chars = []
    for i in range(n):
        bit = 1 << (n - 1 - i)
        chars.append("-" if mask & bit else ("1" if value & bit else "0"))
    return "".join(chars)


# --------------------------------------------------------------------------
# Expression formatting
# --------------------------------------------------------------------------

def _compact(inputs):
    """Use juxtaposition (AB'C) only when every input is a single letter."""
    return all(len(v) == 1 and v.isalpha() for v in inputs)


def format_product(cube, inputs):
    """Product term of a cube, e.g. AB'C  or  a*b'*c.  Empty product = 1."""
    n = len(inputs)
    value, mask = cube
    lits = []
    for i, name in enumerate(inputs):
        bit = 1 << (n - 1 - i)
        if not mask & bit:
            lits.append(name if value & bit else name + "'")
    if not lits:
        return "1"
    return ("" if _compact(inputs) else "*").join(lits)


def format_sum(cube, inputs):
    """Sum (clause) for a cube of the COMPLEMENT function, by DeMorgan.

    A cube c of F' (F' = ... + c + ...) yields the clause (c)' of F, so each
    literal is inverted: cube A B' -> clause (A' + B).
    """
    n = len(inputs)
    value, mask = cube
    lits = []
    for i, name in enumerate(inputs):
        bit = 1 << (n - 1 - i)
        if not mask & bit:
            lits.append(name + "'" if value & bit else name)
    if not lits:
        return "0"
    if len(lits) == 1:
        return lits[0]
    return "(" + " + ".join(lits) + ")"


def format_sop(cubes, inputs):
    if not cubes:
        return "0"
    return " + ".join(format_product(c, inputs) for c in cubes)


def format_pos(complement_cubes, inputs):
    """POS of F given a cover of F' (list of cubes of the complement)."""
    if not complement_cubes:
        return "1"
    clauses = [format_sum(c, inputs) for c in complement_cubes]
    return ("" if _compact(inputs) else " * ").join(clauses)


def sort_cubes(cubes, n):
    """Deterministic, readable ordering: fewer literals first, then by value."""
    return sorted(cubes, key=lambda c: (cube_literals(c, n), cube_to_string(c, n)))


# --------------------------------------------------------------------------
# Function / design containers
# --------------------------------------------------------------------------

@dataclass
class BooleanFunction:
    """A single-output, possibly incompletely specified, Boolean function."""
    name: str
    inputs: list
    on_vec: int                 # ON-set  (F = 1)
    dc_vec: int = 0             # don't-care set

    def __post_init__(self):
        if len(self.inputs) > MAX_INPUTS:
            raise ValueError(f"{len(self.inputs)} inputs exceeds the limit of {MAX_INPUTS}")
        full = full_mask(self.n)
        self.dc_vec &= full
        self.on_vec &= full & ~self.dc_vec      # a point that is don't-care is not ON

    @property
    def n(self):
        return len(self.inputs)

    @property
    def off_vec(self):
        return full_mask(self.n) & ~(self.on_vec | self.dc_vec)

    def minterms(self):
        return vector_to_indices(self.on_vec)

    def dontcares(self):
        return vector_to_indices(self.dc_vec)

    def maxterms(self):
        return vector_to_indices(self.off_vec)

    def complement(self):
        """F' : ON and OFF sets swap, don't-cares stay don't-cares."""
        return BooleanFunction(self.name + "'", list(self.inputs), self.off_vec, self.dc_vec)

    def value(self, m):
        """1, 0 or None (don't care) for minterm m."""
        if (self.dc_vec >> m) & 1:
            return None
        return (self.on_vec >> m) & 1

    def signature(self):
        return f"{self.name}({', '.join(self.inputs)})"


@dataclass
class Design:
    """A set of functions sharing the same ordered input list."""
    name: str
    inputs: list
    outputs: dict = field(default_factory=dict)     # name -> BooleanFunction
    source: str = ""

    def add(self, fn):
        self.outputs[fn.name] = fn
