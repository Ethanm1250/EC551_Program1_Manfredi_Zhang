"""
Analysis features beyond minimization:

* Static hazard detection (custom command #1).  For a two-level SOP cover, a
  static-1 hazard exists between two adjacent ON minterms (differing in one
  input) when no single product term contains both: while that input
  toggles, one AND gate turns off before the other turns on and the output
  can glitch to 0.  The fix is to add a prime implicant (consensus term)
  that spans each such pair.  Static-0 hazards in a POS cover are the dual,
  found by running the same check on F'.

* Equivalence checking (custom command #2).  Two functions are compared on
  their truth vectors over the union of their inputs; points that are
  don't-care in either function are ignored.  Mismatches are reported as
  counterexample input assignments.

* Truth table and Karnaugh-map rendering (extra utilities).
"""

from .boolfunc import BooleanFunction, cube_covers, full_mask, vector_to_indices


# --------------------------------------------------------------------------
# Static hazards
# --------------------------------------------------------------------------

def hazard_pairs(n, on_vec, cover):
    """Adjacent ON-minterm pairs (m_low, m_high, var_index) not covered by one cube."""
    pairs = []
    for m in vector_to_indices(on_vec):
        for i in range(n):
            bit = 1 << (n - 1 - i)
            if m & bit or not (on_vec >> (m | bit)) & 1:
                continue
            # A cube covers both minterms iff it covers m and ignores this bit.
            if not any(c[1] & bit and cube_covers(c, m) for c in cover):
                pairs.append((m, m | bit, i))
    return pairs


def hazard_free_cover(n, pairs, cover, primes):
    """Add primes (greedy) until every hazard pair lies inside one cube."""
    remaining = list(pairs)
    added = []
    while remaining:
        def gain(c):
            return sum(1 for a, b, i in remaining if cube_covers(c, a) and cube_covers(c, b))
        best = max((c for c in primes if c not in cover and c not in added), key=gain)
        added.append(best)
        remaining = [(a, b, i) for a, b, i in remaining
                     if not (cube_covers(best, a) and cube_covers(best, b))]
    return added


def describe_pair(pair, inputs):
    """'A=1 C=1, B toggles (m5 <-> m7)'"""
    a, b, i = pair
    n = len(inputs)
    fixed = []
    for k, name in enumerate(inputs):
        if k != i:
            fixed.append(f"{name}={(a >> (n - 1 - k)) & 1}")
    return f"{' '.join(fixed)}, {inputs[i]} toggles (m{a} <-> m{b})"


# --------------------------------------------------------------------------
# Equivalence
# --------------------------------------------------------------------------

def remap(fn, inputs):
    """Express fn over a (super)set of inputs; returns (on_vec, dc_vec)."""
    if list(inputs) == list(fn.inputs):
        return fn.on_vec, fn.dc_vec
    missing = [v for v in fn.inputs if v not in inputs]
    if missing:
        raise ValueError(f"inputs {missing} are not in {inputs}")
    n_new, n_old = len(inputs), fn.n
    # bit position of each old input inside a new minterm index
    pos = [n_new - 1 - inputs.index(v) for v in fn.inputs]
    on = dc = 0
    for m in range(1 << n_new):
        old = 0
        for k, p in enumerate(pos):
            old |= ((m >> p) & 1) << (n_old - 1 - k)
        if (fn.dc_vec >> old) & 1:
            dc |= 1 << m
        elif (fn.on_vec >> old) & 1:
            on |= 1 << m
    return on, dc


def check_equivalence(f, g, max_examples=5):
    """Return (equivalent, inputs, counterexamples[(minterm, f_val, g_val)], n_mismatches)."""
    inputs = list(f.inputs) + [v for v in g.inputs if v not in f.inputs]
    f_on, f_dc = remap(f, inputs)
    g_on, g_dc = remap(g, inputs)
    care = full_mask(len(inputs)) & ~(f_dc | g_dc)
    diff = (f_on ^ g_on) & care
    examples = []
    for m in vector_to_indices(diff)[:max_examples]:
        examples.append((m, (f_on >> m) & 1, (g_on >> m) & 1))
    return diff == 0, inputs, examples, bin(diff).count("1")


def assignment(m, inputs):
    n = len(inputs)
    return " ".join(f"{v}={(m >> (n - 1 - i)) & 1}" for i, v in enumerate(inputs))


# --------------------------------------------------------------------------
# Truth table / K-map
# --------------------------------------------------------------------------

def _val(fn, m):
    v = fn.value(m)
    return "X" if v is None else str(v)


def truth_table_rows(design, outputs):
    n = len(design.inputs)
    for m in range(1 << n):
        bits = [str((m >> (n - 1 - i)) & 1) for i in range(n)]
        yield m, bits, [_val(design.outputs[o], m) for o in outputs]


def truth_table_text(design, outputs):
    head = " ".join(design.inputs) + " | " + " ".join(outputs)
    lines = ["  m  " + head, "  " + "-" * (len(head) + 3)]
    for m, bits, vals in truth_table_rows(design, outputs):
        ins = " ".join(b.rjust(len(name)) for b, name in zip(bits, design.inputs))
        outs = " ".join(v.rjust(len(name)) for v, name in zip(vals, outputs))
        lines.append(f"{m:>4} {ins} | {outs}")
    return "\n".join(lines)


def truth_table_csv(design, outputs):
    lines = [",".join(["m"] + design.inputs + outputs)]
    for m, bits, vals in truth_table_rows(design, outputs):
        lines.append(",".join([str(m)] + bits + vals))
    return "\n".join(lines) + "\n"


_GRAY = {1: ["0", "1"], 2: ["00", "01", "11", "10"]}


def kmap_text(fn: BooleanFunction):
    """Karnaugh map for 2-4 inputs (rows = first inputs, columns = last)."""
    n = fn.n
    if not 2 <= n <= 4:
        return f"(K-map display supports 2-4 inputs; {fn.name} has {n})"
    r_bits = n // 2
    c_bits = n - r_bits
    rows, cols = _GRAY[r_bits], _GRAY[c_bits]
    r_name = "".join(fn.inputs[:r_bits])
    c_name = "".join(fn.inputs[r_bits:])
    label = f"{r_name}\\{c_name}"
    width = max(len(label), r_bits + 1)
    out = [label.ljust(width) + " | " + "  ".join(c.center(c_bits) for c in cols)]
    out.append("-" * len(out[0]))
    for r in rows:
        cells = []
        for c in cols:
            m = int(r + c, 2)
            cells.append(_val(fn, m).center(c_bits))
        out.append((r.ljust(width) + " | " + "  ".join(cells)).rstrip())
    return "\n".join(out)
