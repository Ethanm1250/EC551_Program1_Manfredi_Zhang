"""
Two-level minimization: Quine-McCluskey prime implicants + exact cover.

1. prime_implicants()  - all primes by recursive Shannon expansion on truth
   vectors (fast on large structured functions).  prime_implicants_qm() is
   the textbook tabular Quine-McCluskey version - merge cubes differing in
   one variable until nothing merges - kept as a reference / cross-check.

2. minimum_cover()     - choose the cheapest set of primes covering every ON
   minterm (don't-cares need not be covered).  Essential primes are taken
   first, then an exact branch-and-bound search with the classic reductions
   (essential columns, column dominance) finds the literal-minimum cover.
   A node / time limit guards against blow-up; if it is hit the best cover
   found so far (at worst the greedy cover) is returned and flagged as not
   proven optimal.

Covers for POS are produced by minimizing F' and applying DeMorgan
(see boolfunc.format_pos).
"""

import time
from dataclasses import dataclass

from .boolfunc import cube_literals, cube_minterms, cube_vector, popcount, sort_cubes

NODE_LIMIT = 200_000        # branch-and-bound budget per function:
TIME_LIMIT = 5.0            # whichever is hit first ends the exact search


@dataclass
class MinResult:
    fn_name: str
    n: int
    primes: list            # prime implicants covering >= 1 ON minterm
    dc_only_primes: int     # primes that cover only don't-cares (unused)
    essentials: list        # essential prime implicants
    cover: list             # chosen minimum cover
    optimal: bool           # False if the B&B search hit its node limit

    @property
    def literals(self):
        return sum(cube_literals(c, self.n) for c in self.cover)


def prime_implicants_qm(n, on, dc):
    """All prime implicants by tabular Quine-McCluskey (minterm lists in)."""
    current = {(m, 0) for m in list(on) + list(dc)}
    primes = []
    while current:
        merged = set()
        nxt = set()
        for v, m in current:
            for b in range(n):
                bit = 1 << b
                if (m | v) & bit:
                    continue
                partner = (v | bit, m)
                if partner in current:
                    nxt.add((v, m | bit))
                    merged.add((v, m))
                    merged.add(partner)
        primes.extend(c for c in current if c not in merged)
        current = nxt
    return primes


def prime_implicants(n, care_vec):
    """All prime implicants of the truth vector care_vec (= ON | DC).

    Recursive Shannon expansion on the first variable x, with cofactors
    f0 = f(x=0) and f1 = f(x=1) (the low / high half of the truth vector):

        P(f) = P(f0 & f1)                              (x absent)
             + { x' p : p in P(f0), p not in P(f0 & f1) }
             + { x  p : p in P(f1), p not in P(f0 & f1) }

    A prime p of f0 that is also an implicant of f1 lies inside f0 & f1 and
    is then a prime of f0 & f1, so it extends to a prime without x; every
    other prime of f0 needs the literal x'.  This yields exactly the same
    set as Quine-McCluskey (the unit tests check this), but works on whole
    cubes, so large implicants never have to be built up minterm by minterm.
    """
    memo = {}

    def rec(f, k):
        if f == 0:
            return []
        full = (1 << (1 << k)) - 1
        if f == full:
            return [(0, (1 << k) - 1)]
        key = (f, k)
        if key in memo:
            return memo[key]
        half = 1 << (k - 1)
        low_mask = (1 << half) - 1
        f0, f1 = f & low_mask, f >> half
        top = 1 << (k - 1)
        both = rec(f0 & f1, k - 1)
        both_set = set(both)
        out = [(v, m | top) for v, m in both]
        out += [c for c in rec(f0, k - 1) if c not in both_set]
        out += [(v | top, m) for v, m in rec(f1, k - 1) if (v, m) not in both_set]
        memo[key] = out
        return out

    return rec(care_vec, n)


def _iter_bits(x):
    while x:
        low = x & -x
        yield low.bit_length() - 1
        x ^= low


class _CoverSolver:
    """Exact unate covering by branch and bound, using int bitsets.

    cov[p]  : bitset of ON-minterm indices covered by prime p
    rows[j] : bitset of primes covering ON-minterm j
    """

    def __init__(self, cov, rows, costs):
        self.cov, self.rows, self.costs = cov, rows, costs
        self.best = None
        self.best_cost = float("inf")
        self.nodes = 0
        self.optimal = True
        self.deadline = time.monotonic() + TIME_LIMIT

    def greedy(self, universe, cands):
        chosen, cost = [], 0
        while universe:
            p = max(_iter_bits(cands),
                    key=lambda q: (popcount(self.cov[q] & universe) / self.costs[q], -q))
            chosen.append(p)
            cost += self.costs[p]
            universe &= ~self.cov[p]
            cands &= ~(1 << p)
        return chosen, cost

    def lower_bound(self, universe, cands):
        """Sum of cheapest costs over a set of minterms sharing no prime."""
        lb, blocked = 0, 0
        for j in _iter_bits(universe):
            r = self.rows[j] & cands
            if r & blocked:
                continue
            blocked |= r
            lb += min(self.costs[p] for p in _iter_bits(r))
        return lb

    def search(self, universe, cands, chosen, cost):
        self.nodes += 1
        if self.nodes > NODE_LIMIT or time.monotonic() > self.deadline:
            self.optimal = False
            return
        while universe:
            # Essential columns: a minterm with a single remaining candidate.
            forced = 0
            for j in _iter_bits(universe):
                r = self.rows[j] & cands
                if r == 0:
                    return                          # infeasible branch
                if r & (r - 1) == 0:
                    forced |= r
            if forced:
                for p in _iter_bits(forced):
                    chosen = chosen + [p]
                    cost += self.costs[p]
                    universe &= ~self.cov[p]
                cands &= ~forced
                continue
            # Column dominance: drop p if some q covers a superset at <= cost.
            # Any such q must also cover p's first minterm, so only those
            # primes (one row of the table) need to be compared.
            live = [p for p in _iter_bits(cands) if self.cov[p] & universe]
            cands = 0
            for p in live:
                cands |= 1 << p
            for p in live:
                cp = self.cov[p] & universe
                first = (cp & -cp).bit_length() - 1
                for q in _iter_bits(self.rows[first] & cands):
                    if q == p:
                        continue
                    cq = self.cov[q] & universe
                    if cp & ~cq == 0 and self.costs[q] <= self.costs[p] and \
                            (cp != cq or self.costs[q] < self.costs[p] or q < p):
                        cands &= ~(1 << p)
                        break
            break

        if cost >= self.best_cost:
            return
        if not universe:
            self.best, self.best_cost = chosen, cost
            return
        if cost + self.lower_bound(universe, cands) >= self.best_cost:
            return
        # Branch on the minterm with the fewest candidate primes.
        j = min(_iter_bits(universe), key=lambda k: popcount(self.rows[k] & cands))
        options = sorted(_iter_bits(self.rows[j] & cands),
                         key=lambda p: self.costs[p] / popcount(self.cov[p] & universe))
        for p in options:
            self.search(universe & ~self.cov[p], cands & ~(1 << p), chosen + [p], cost + self.costs[p])
            cands &= ~(1 << p)          # later branches exclude p (already explored)


def minimum_cover(n, on, primes):
    """Return (cover, essentials, optimal) for ON-set list `on`."""
    if not on:
        return [], [], True
    index = {m: j for j, m in enumerate(on)}
    cov = []
    for c in primes:
        bits = 0
        for m in cube_minterms(c):
            j = index.get(m)
            if j is not None:
                bits |= 1 << j
        cov.append(bits)
    rows = [0] * len(on)
    for p, bits in enumerate(cov):
        for j in _iter_bits(bits):
            rows[j] |= 1 << p

    essential_mask = 0
    for r in rows:
        if r & (r - 1) == 0:
            essential_mask |= r
    essentials = [primes[p] for p in _iter_bits(essential_mask)]

    # Cost: literals first, number of terms as tie-breaker.
    weight = len(primes) + 1
    costs = [cube_literals(c, n) * weight + 1 for c in primes]
    universe = (1 << len(on)) - 1
    cands = (1 << len(primes)) - 1

    solver = _CoverSolver(cov, rows, costs)
    solver.best, solver.best_cost = solver.greedy(universe, cands)
    solver.search(universe, cands, [], 0)
    cover = [primes[p] for p in solver.best]
    return sort_cubes(cover, n), sort_cubes(essentials, n), solver.optimal


def minimize(fn):
    """Minimize a BooleanFunction into a literal-minimum SOP cover."""
    on, n = fn.minterms(), fn.n
    if not on:
        return MinResult(fn.name, n, [], 0, [], [], True)
    all_primes = prime_implicants(n, fn.on_vec | fn.dc_vec)
    primes = [c for c in all_primes if cube_vector(c, n) & fn.on_vec]
    cover, essentials, optimal = minimum_cover(n, on, primes)
    return MinResult(fn.name, n, sort_cubes(primes, n), len(all_primes) - len(primes),
                     essentials, cover, optimal)
