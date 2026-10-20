"""
The synthesis engine: holds the loaded design and implements the 12 required
operations plus utilities.  Every operation returns plain text so the same
code serves the one-shot CLI, the interactive shell and report files.

    #  key      operation
    1  csop     canonical SOP                 7  pi      prime implicants (count)
    2  cpos     canonical POS                 8  epi     essential PIs (count)
    3  icsop    canonical SOP of F'           9  on      ON-set minterm count
    4  icpos    canonical POS of F'          10  off     OFF-set maxterm count
    5  minsop   literal-minimized SOP        11  hazard  static hazard detection + fix
    6  minpos   literal-minimized POS        12  equiv   equivalence checking
"""

import os

from . import analysis
from .boolfunc import (BooleanFunction, cube_minterms, cube_vector, format_pos,
                       format_product, format_sop, format_sum, full_mask)
from .fpga import generate_project
from .minimize import minimize
from .netlist import build_netlist
from .parsers import load_expression, load_file
from .verilog import emit_module

OPERATIONS = [
    (1, "csop", "Canonical SOP"),
    (2, "cpos", "Canonical POS"),
    (3, "icsop", "Inverse canonical SOP (F')"),
    (4, "icpos", "Inverse canonical POS (F')"),
    (5, "minsop", "Literal-minimized SOP"),
    (6, "minpos", "Literal-minimized POS"),
    (7, "pi", "Prime implicants"),
    (8, "epi", "Essential prime implicants"),
    (9, "on", "ON-set minterms"),
    (10, "off", "OFF-set maxterms"),
    (11, "hazard", "Custom #1: static hazard detection"),
    (12, "equiv", "Custom #2: equivalence checking"),
]
OP_BY_KEY = {key: (num, key, title) for num, key, title in OPERATIONS}
OP_BY_KEY.update({str(num): (num, key, title) for num, key, title in OPERATIONS})


class EngineError(Exception):
    pass


def _pct(saved, total):
    return f"{100.0 * saved / total:.1f}%" if total else "n/a"


class Engine:
    def __init__(self, max_terms=64):
        self.design = None
        self.selected = None            # None = all outputs
        self.max_terms = max_terms      # display limit for long lists (0 = unlimited)
        self._sop, self._pos = {}, {}

    # ------------------------------------------------------------------ loading
    def load(self, path):
        self._set(load_file(path))
        return self.info()

    def load_text(self, text, inputs=None):
        self._set(load_expression(text, inputs))
        return self.info()

    def _set(self, design):
        self.design = design
        self.selected = None
        self._sop, self._pos = {}, {}

    def require(self):
        if self.design is None:
            raise EngineError("no design loaded (use 'load <file>' or 'expr <equation>')")
        return self.design

    def functions(self):
        d = self.require()
        names = self.selected or list(d.outputs)
        return [d.outputs[n] for n in names]

    def select(self, names):
        d = self.require()
        if not names or names == ["all"]:
            self.selected = None
            return "selected outputs: all (" + ", ".join(d.outputs) + ")"
        bad = [n for n in names if n not in d.outputs]
        if bad:
            raise EngineError(f"unknown output(s) {bad}; available: {', '.join(d.outputs)}")
        self.selected = names
        return "selected outputs: " + ", ".join(names)

    # ------------------------------------------------------------ minimization
    def sop(self, fn):
        if fn.name not in self._sop:
            self._sop[fn.name] = minimize(fn)
        return self._sop[fn.name]

    def pos(self, fn):
        """POS minimization = SOP minimization of F', clauses by DeMorgan."""
        if fn.name not in self._pos:
            self._pos[fn.name] = minimize(fn.complement())
        return self._pos[fn.name]

    # ----------------------------------------------------------------- helpers
    def _clip(self, items):
        if self.max_terms and len(items) > self.max_terms:
            return items[:self.max_terms] + [f"... (+{len(items) - self.max_terms} more)"]
        return items

    def _nums(self, nums):
        return ", ".join(str(x) for x in self._clip(nums))

    def _join(self, terms, sep):
        return sep.join(self._clip(terms)) if terms else None

    def _dc_note(self, fn):
        dc = fn.dontcares()
        return [f"  don't-cares: d({self._nums(dc)})"] if dc else []

    # -------------------------------------------------------------- operations
    def run(self, key, arg=""):
        if key not in OP_BY_KEY:
            raise EngineError(f"unknown operation '{key}'")
        num, key, title = OP_BY_KEY[key]
        if key == "equiv" and arg.strip():
            return f"[{num}] {title}\n" + self.equiv_external(arg.strip())
        method = getattr(self, "op_" + key)
        blocks = []
        for fn in self.functions():
            blocks.append(f"[{num}] {title} -- {fn.signature()}\n" + "\n".join(method(fn)))
        return "\n\n".join(blocks)

    def run_all(self):
        return "\n\n".join(self.run(key) for _, key, _ in OPERATIONS)

    def op_csop(self, fn):
        on, n = fn.minterms(), fn.n
        expr = self._join([format_product((m, 0), fn.inputs) for m in on], " + ") or "0"
        return [f"  {fn.name} = Sum m({self._nums(on)})",
                f"  {fn.name} = {expr}",
                f"  {len(on)} minterms x {n} literals = {len(on) * n} literals"] + self._dc_note(fn)

    def op_cpos(self, fn):
        off, n = fn.maxterms(), fn.n
        expr = self._join([format_sum((m, 0), fn.inputs) for m in off], "") or "1"
        return [f"  {fn.name} = Prod M({self._nums(off)})",
                f"  {fn.name} = {expr}",
                f"  {len(off)} maxterms x {n} literals = {len(off) * n} literals"] + self._dc_note(fn)

    def op_icsop(self, fn):
        off = fn.maxterms()
        expr = self._join([format_product((m, 0), fn.inputs) for m in off], " + ") or "0"
        return [f"  {fn.name}' = Sum m({self._nums(off)})",
                f"  {fn.name}' = {expr}"] + self._dc_note(fn)

    def op_icpos(self, fn):
        on = fn.minterms()
        expr = self._join([format_sum((m, 0), fn.inputs) for m in on], "") or "1"
        return [f"  {fn.name}' = Prod M({self._nums(on)})",
                f"  {fn.name}' = {expr}"] + self._dc_note(fn)

    def _search_note(self, res):
        return "  cover: exact minimum (branch and bound)" if res.optimal else \
               "  cover: best found (search limit reached, may not be minimum)"

    def op_minsop(self, fn):
        res = self.sop(fn)
        canon = len(fn.minterms()) * fn.n
        saved = canon - res.literals
        expr = self._join([format_product(c, fn.inputs) for c in res.cover], " + ") or "0"
        return [f"  {fn.name} = {expr}",
                f"  literals: {res.literals} (canonical SOP: {canon}) -> saved {saved} literals ({_pct(saved, canon)})",
                f"  product terms: {len(res.cover)} (canonical: {len(fn.minterms())})",
                self._search_note(res)]

    def op_minpos(self, fn):
        res = self.pos(fn)
        canon = len(fn.maxterms()) * fn.n
        saved = canon - res.literals
        clauses = [format_sum(c, fn.inputs) for c in res.cover]
        compact = all(len(v) == 1 and v.isalpha() for v in fn.inputs)
        expr = self._join(clauses, "" if compact else " * ") or "1"
        return [f"  {fn.name} = {expr}",
                f"  literals: {res.literals} (canonical POS: {canon}) -> saved {saved} literals ({_pct(saved, canon)})",
                f"  sum clauses: {len(res.cover)} (canonical: {len(fn.maxterms())})",
                self._search_note(res)]

    def _implicant_lines(self, fn, cubes):
        on = set(fn.minterms())
        lines = []
        for c in self._clip(cubes):
            if isinstance(c, str):
                lines.append("    " + c)
                continue
            covered = [m for m in cube_minterms(c) if m in on]
            lines.append(f"    {format_product(c, fn.inputs):<20} covers m({self._nums(covered)})")
        return lines

    def op_pi(self, fn):
        res = self.sop(fn)
        lines = [f"  prime implicants: {len(res.primes)}"]
        if res.dc_only_primes:
            lines.append(f"  (excluding {res.dc_only_primes} prime(s) that cover only don't-cares)")
        return lines + self._implicant_lines(fn, res.primes)

    def op_epi(self, fn):
        res = self.sop(fn)
        return [f"  essential prime implicants: {len(res.essentials)}"] + \
            self._implicant_lines(fn, res.essentials)

    def op_on(self, fn):
        on = fn.minterms()
        return [f"  ON-set minterms: {len(on)} of {1 << fn.n}",
                f"  {fn.name} = Sum m({self._nums(on)})"] + self._dc_note(fn)

    def op_off(self, fn):
        off = fn.maxterms()
        return [f"  OFF-set maxterms: {len(off)} of {1 << fn.n}",
                f"  {fn.name} = Prod M({self._nums(off)})"] + self._dc_note(fn)

    def op_hazard(self, fn):
        lines = []
        for form in ("SOP", "POS"):
            if form == "SOP":
                res, target, kind = self.sop(fn), fn, "static-1"
                fmt = lambda cubes: format_sop(cubes, fn.inputs)
            else:
                res, target, kind = self.pos(fn), fn.complement(), "static-0"
                fmt = lambda cubes: format_pos(cubes, fn.inputs)
            pairs = analysis.hazard_pairs(fn.n, target.on_vec, res.cover)
            lines.append(f"  minimized {form}: {fn.name} = {fmt(res.cover)}")
            if not pairs:
                lines.append(f"    no {kind} hazards (every adjacent pair shares a term)")
                continue
            lines.append(f"    {kind} hazards: {len(pairs)}")
            for p in self._clip(pairs):
                lines.append("      " + (p if isinstance(p, str) else analysis.describe_pair(p, fn.inputs)))
            added = analysis.hazard_free_cover(fn.n, pairs, res.cover, res.primes)
            fixed = res.cover + added
            extra = sum(fn.n - bin(c[1]).count("1") for c in added)
            what = "consensus term(s)" if form == "SOP" else "consensus clause(s)"
            lines.append(f"    hazard-free {form}: {fn.name} = {fmt(fixed)}")
            lines.append(f"    (+{len(added)} {what}, +{extra} literals)")
        return lines

    # ---------------------------------------------------------- equivalence
    def _vectors(self, fn):
        """Truth vectors realised by the minimized SOP, minimized POS and netlist."""
        full = full_mask(fn.n)
        sop_vec = 0
        for c in self.sop(fn).cover:
            sop_vec |= cube_vector(c, fn.n)
        not_pos = 0
        for c in self.pos(fn).cover:
            not_pos |= cube_vector(c, fn.n)
        return sop_vec, full & ~not_pos

    def op_equiv(self, fn):
        sop_vec, pos_vec = self._vectors(fn)
        nl = self.netlist("auto", only=[fn.name])
        net_vec = list(nl.simulate().values())[0]
        lines = ["  (no file given: verifying the engine's own results against the specification)"]
        for label, vec in (("minimized SOP", sop_vec), ("minimized POS", pos_vec),
                           ("gate netlist ", net_vec)):
            other = BooleanFunction(fn.name, list(fn.inputs), vec)
            ok, _, ex, count = analysis.check_equivalence(fn, other)
            lines.append(f"  {label} vs spec: " + ("EQUIVALENT" if ok else f"NOT equivalent ({count} mismatches)"))
        lines.append("  usage: equiv <file | equation>  compares against another design")
        return lines

    def equiv_external(self, arg):
        d = self.require()
        if os.path.isfile(arg):
            other = load_file(arg)
        else:
            other = load_expression(arg, d.inputs)
        pairs = [(n, n) for n in d.outputs if n in other.outputs]
        if not pairs and len(d.outputs) == 1 and len(other.outputs) == 1:
            pairs = [(next(iter(d.outputs)), next(iter(other.outputs)))]
        if not pairs:
            raise EngineError("no outputs with matching names to compare")
        lines, all_ok = [], True
        for a, b in pairs:
            ok, inputs, examples, count = analysis.check_equivalence(d.outputs[a], other.outputs[b])
            all_ok &= ok
            lines.append(f"  {a} vs {b}: " + ("EQUIVALENT" if ok else f"NOT EQUIVALENT ({count} mismatching input combinations)"))
            for m, fv, gv in examples:
                lines.append(f"    counterexample: {analysis.assignment(m, inputs)} -> "
                             f"loaded {a}={fv}, other {b}={gv}")
        unmatched = [n for n in d.outputs if n not in dict(pairs)]
        if unmatched:
            lines.append(f"  (not compared: {', '.join(unmatched)})")
        lines.append("  RESULT: " + ("designs are equivalent" if all_ok else "designs differ"))
        return "\n".join(lines)

    # ----------------------------------------------------------- utilities
    def info(self):
        d = self.require()
        lines = [f"design '{d.name}' from {d.source or 'input'}",
                 f"  inputs  ({len(d.inputs)}): {', '.join(d.inputs)}",
                 f"  outputs ({len(d.outputs)}): {', '.join(d.outputs)}"]
        for fn in d.outputs.values():
            lines.append(f"    {fn.name}: {len(fn.minterms())} ON, {len(fn.maxterms())} OFF, "
                         f"{len(fn.dontcares())} don't-care of {1 << fn.n}")
        return "\n".join(lines)

    def kmap(self):
        return "\n\n".join(f"K-map of {fn.signature()}\n" + analysis.kmap_text(fn) for fn in self.functions())

    def truth_table(self, path=None):
        d = self.require()
        outs = [fn.name for fn in self.functions()]
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(analysis.truth_table_csv(d, outs))
            return f"truth table ({1 << len(d.inputs)} rows) written to {path}"
        if len(d.inputs) > 8:
            return "truth table too large to print (>256 rows); use 'truthtable <file.csv>'"
        return analysis.truth_table_text(d, outs)

    def netlist(self, form="auto", only=None):
        """Gate netlist using SOP, POS or (auto) whichever has fewer literals."""
        d = self.require()
        names = only or list(d.outputs)
        sub = type(d)(d.name, d.inputs, {n: d.outputs[n] for n in names})
        choices = {}
        for name in names:
            fn = d.outputs[name]
            s, p = self.sop(fn), self.pos(fn)
            use_pos = form == "pos" or (form == "auto" and p.literals < s.literals)
            if use_pos:
                choices[name] = ("POS", p.cover, format_pos(p.cover, fn.inputs))
            else:
                choices[name] = ("SOP", s.cover, format_sop(s.cover, fn.inputs))
        return build_netlist(sub, d.name, choices)

    def _checked_netlist(self, form):
        nl = self.netlist(form)
        for fn, vec in zip(self.design.outputs.values(), nl.simulate().values()):
            care = full_mask(fn.n) & ~fn.dc_vec
            if (vec ^ fn.on_vec) & care:
                raise EngineError(f"internal error: netlist for {fn.name} does not match the specification")
        return nl

    def verilog(self, path=None, form="auto"):
        nl = self._checked_netlist(form)
        text = emit_module(nl, self.design.source)
        st = nl.stats()
        summary = (f"structural Verilog: module '{nl.module}', {st['gates']} gates, "
                   f"{st['gate_inputs']} gate inputs, depth {st['depth']} "
                   f"(netlist verified against the specification by simulation)")
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return summary + f"\nwritten to {path}"
        return text + "\n// " + summary

    def fpga(self, outdir, board="basys3", form="auto"):
        nl = self._checked_netlist(form)
        text = emit_module(nl, self.design.source)
        files, notes = generate_project(outdir, nl, self.design, text, board)
        lines = [f"FPGA project for {board} written to {outdir}:"] + [f"  {f}" for f in files]
        lines += [f"  note: {n}" for n in notes]
        lines.append(f"build with:  cd {outdir}  &&  vivado -mode batch -source build.tcl")
        return "\n".join(lines)
