"""
Unit tests (standard library only):   python -m unittest discover -s tests -v

The minimizer is checked against brute force on random functions: every
cover must equal the function on its care set, and for small functions the
literal count must equal the true minimum over all subsets of primes.
"""

import itertools
import os
import random
import tempfile
import unittest

from logicsynth.analysis import check_equivalence, hazard_pairs
from logicsynth.boolfunc import BooleanFunction, cube_literals, cube_vector, full_mask
from logicsynth.engine import Engine
from logicsynth.minimize import minimize, prime_implicants, prime_implicants_qm
from logicsynth.parsers import load_expression, load_file

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "examples")


def random_function(n, rng, dc_rate=0.15):
    on = dc = 0
    for m in range(1 << n):
        r = rng.random()
        if r < dc_rate:
            dc |= 1 << m
        elif r < 0.5 + dc_rate / 2:
            on |= 1 << m
    return BooleanFunction("F", [chr(65 + i) for i in range(n)], on, dc)


def cover_vector(cover, n):
    vec = 0
    for c in cover:
        vec |= cube_vector(c, n)
    return vec


class MinimizerTests(unittest.TestCase):
    def test_cover_matches_function(self):
        rng = random.Random(551)
        for n in range(1, 9):
            for _ in range(25):
                fn = random_function(n, rng)
                res = minimize(fn)
                care = full_mask(n) & ~fn.dc_vec
                self.assertEqual(cover_vector(res.cover, n) & care, fn.on_vec)
                # POS = complement of the cover of F'
                pos = minimize(fn.complement())
                self.assertEqual((full_mask(n) & ~cover_vector(pos.cover, n)) & care, fn.on_vec)

    def test_shannon_primes_equal_quine_mccluskey(self):
        rng = random.Random(42)
        for n in range(1, 9):
            for _ in range(20):
                fn = random_function(n, rng)
                qm = prime_implicants_qm(n, fn.minterms(), fn.dontcares())
                self.assertEqual(sorted(prime_implicants(n, fn.on_vec | fn.dc_vec)), sorted(qm))

    def test_cover_is_literal_minimum(self):
        rng = random.Random(2026)
        for n in (3, 4):
            for _ in range(40):
                fn = random_function(n, rng)
                res = minimize(fn)
                if not res.primes or len(res.primes) > 14:
                    continue
                best = None
                for k in range(1, len(res.primes) + 1):
                    for subset in itertools.combinations(res.primes, k):
                        if cover_vector(subset, n) & fn.on_vec == fn.on_vec:
                            lits = sum(cube_literals(c, n) for c in subset)
                            best = lits if best is None else min(best, lits)
                self.assertEqual(res.literals, best)

    def test_textbook_cases(self):
        fn = load_expression("F(A,B,C) = m(0,1,2,5,6,7)").outputs["F"]   # cyclic core
        res = minimize(fn)
        self.assertEqual((len(res.primes), len(res.essentials), len(res.cover)), (6, 0, 3))
        fn = load_expression("F(A,B,C,D) = m(1,3,7,11,15) + d(0,2,5)").outputs["F"]
        res = minimize(fn)
        self.assertEqual((len(res.primes), len(res.essentials), res.literals), (3, 1, 4))

    def test_constants(self):
        zero = load_expression("F(A,B) = 0").outputs["F"]
        one = load_expression("F(A,B) = A + A'").outputs["F"]
        self.assertEqual(minimize(zero).cover, [])
        self.assertEqual(minimize(one).cover, [(0, 0b11)])


class ParserTests(unittest.TestCase):
    def test_expression_styles_agree(self):
        ref = load_expression("F(A,B,C) = AB' + BC").outputs["F"]
        for text in ["F = A & ~B | B & C", "F = A*!B + B*C", "F = (A + B)(B' + C)",
                     "F(A,B,C) = m(3,4,5,7)", "F(A,B,C) = M(0,1,2,6)", "F = A B' + B C"]:
            other = load_expression(text).outputs["F"]
            self.assertTrue(check_equivalence(ref, other)[0], text)

    def test_example_files_load(self):
        for name in os.listdir(EXAMPLES):
            design = load_file(os.path.join(EXAMPLES, name))
            self.assertTrue(design.outputs, name)

    def test_circuit_formats(self):
        fa = load_file(os.path.join(EXAMPLES, "full_adder.blif"))
        ref = load_expression("sum = a ^ b ^ cin; cout = a b + a cin + b cin")
        for o in ("sum", "cout"):
            self.assertTrue(check_equivalence(fa.outputs[o], ref.outputs[o])[0])
        mux = load_file(os.path.join(EXAMPLES, "mux4.json")).outputs["y"]
        ref = load_expression("y = s1's0'd0 + s1's0 d1 + s1 s0'd2 + s1 s0 d3",
                              ["s1", "s0", "d0", "d1", "d2", "d3"]).outputs["y"]
        self.assertTrue(check_equivalence(mux, ref)[0])

    def test_pla_dont_cares(self):
        d = load_file(os.path.join(EXAMPLES, "bcd_7seg.pla"))
        self.assertEqual(d.outputs["a"].dontcares(), list(range(10, 16)))


class EngineTests(unittest.TestCase):
    def test_netlists_match_spec(self):
        rng = random.Random(7)
        for n in range(1, 7):
            for form in ("sop", "pos", "auto"):
                fn = random_function(n, rng)
                eng = Engine()
                eng._set(type(load_expression("F=A"))("t", fn.inputs, {"F": fn}))
                # (with 6 inputs A..F the output port is renamed to avoid the clash)
                out = list(eng.netlist(form).simulate().values())[0]
                care = full_mask(n) & ~fn.dc_vec
                self.assertEqual(out & care, fn.on_vec)

    def test_hazard_fix(self):
        eng = Engine()
        eng.load_text("F(A,B,C) = AB' + BC")
        fn = eng.design.outputs["F"]
        self.assertEqual(len(hazard_pairs(3, fn.on_vec, eng.sop(fn).cover)), 1)
        self.assertIn("AB' + AC", eng.run("hazard"))

    def test_all_operations_and_fpga(self):
        eng = Engine()
        eng.load(os.path.join(EXAMPLES, "bcd_7seg.pla"))
        text = eng.run_all()
        for k in range(1, 13):
            self.assertIn(f"[{k}]", text)
        with tempfile.TemporaryDirectory() as tmp:
            eng.fpga(tmp, "nexys-a7")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "top.v")))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "sim", "tb_bcd_7seg.v")))


if __name__ == "__main__":
    unittest.main()
