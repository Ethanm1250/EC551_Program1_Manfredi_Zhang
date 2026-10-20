# EC551 Programming Assignment 1: Logic Synthesis Engine

**Team:** Ethan Manfredi & Kevin Zhang

A Python logic synthesis engine. It reads a Boolean function (SOP, POS, any
multi-level expression, minterm/maxterm lists, or an Espresso PLA) or a
gate-level circuit graph (BLIF, ISCAS `.bench`, or our JSON format), then:

* computes canonical and literal-minimized SOP and POS forms,
* reports prime implicants, essential primes, ON-set and OFF-set counts,
* detects static hazards and synthesizes a hazard-free cover (custom command #1),
* checks the equivalence of two designs and gives counterexamples (custom command #2),
* writes **structural Verilog** built only from gate primitives (`not`, `and`,
  `or`, `buf`), and
* generates a complete **Vivado project** (board wrapper, pin constraints,
  build/program scripts, self-checking testbench) for the Digilent Basys 3
  or Nexys A7.

The engine uses only the Python standard library (Python 3.8+), so there's
nothing to install.

---

## Quick start

```bash
# all 12 operations on an example
python -m logicsynth examples/hazard.eqn --all

# a function typed on the command line
python -m logicsynth -e "F(A,B,C,D) = m(1,3,7,11,15) + d(0,2,5)" --ops minsop,minpos,pi,epi

# write structural Verilog and an FPGA project for the Basys 3
python -m logicsynth examples/bcd_7seg.pla --verilog out/bcd_7seg.v --fpga out/bcd_fpga --board basys3

# interactive shell
python -m logicsynth examples/full_adder.blif
```

Run the unit tests:

```bash
python -m unittest discover -s tests -v
```

---

## Repository layout

```
EC551-LogicSynthesis/
├── README.md
├── logicsynth/                 # the engine (Python package)
│   ├── __main__.py             # `python -m logicsynth` entry point
│   ├── cli.py                  # argparse one-shot mode + interactive shell (cmd.Cmd)
│   ├── engine.py               # the 12 operations, Verilog/FPGA commands, text formatting
│   ├── boolfunc.py             # truth-vector function model, cubes, expression printing
│   ├── minimize.py             # prime implicants + exact branch-and-bound cover
│   ├── analysis.py             # hazards, equivalence, K-map, truth table
│   ├── netlist.py              # gate netlist from covers, netlist simulation
│   ├── verilog.py              # structural Verilog + exhaustive testbench writer
│   ├── fpga.py                 # board wrapper, XDC, Vivado Tcl scripts, 7-seg driver
│   └── parsers/
│       ├── __init__.py         # picks a parser from the file extension
│       ├── expr.py             # expression tokenizer / recursive-descent parser
│       ├── eqn.py              # .eqn/.txt equation files, minterm/maxterm lists
│       ├── pla.py              # Espresso PLA
│       ├── blif.py             # BLIF logic networks
│       ├── bench.py            # ISCAS .bench netlists
│       ├── jsoncircuit.py      # JSON gate graph
│       └── network.py          # shared topological evaluator for circuit graphs
├── examples/                   # test inputs in every supported format
└── tests/test_engine.py        # unit tests (unittest)
```

---

## Architecture

```
 input file / -e equation
          │
          ▼
   parsers/*  ──►  Design = ordered inputs + {output: BooleanFunction}
                   (each function stored as a truth vector: ON-set + DC-set)
          │
          ▼
   minimize.py ──► prime implicants ──► exact minimum cover (SOP of F, and of F' for POS)
          │
          ├──► engine.py   operations 1-12 (text output)
          ├──► analysis.py hazards / equivalence / K-map / truth table
          └──► netlist.py  gate netlist ──► simulated and checked against the spec
                              │
                              ├──► verilog.py  structural .v + testbench
                              └──► fpga.py     top.v + .xdc + build.tcl → bitstream
```

### Key design decisions

**Truth vectors.** Each function of *n* inputs is stored as a Python
integer with 2^n bits, where bit *m* is the function's value on minterm *m*.
Input *i* has a fixed bit pattern (e.g. `A` = `11110000` for 3 inputs), so
evaluating an expression or a whole gate-level circuit on **all** input
combinations at once only takes ordinary `& | ^ ~` operations on integers.
Every input format reduces to these vectors, so the rest of the engine
doesn't care where a design came from. Minterm numbering follows the
textbook convention: the first declared input is the MSB. The limit is 20
inputs.

**Cubes** (product terms) are `(value, mask)` integer pairs. A mask bit set
to 1 means that variable is absent (`-`).

**Prime implicants.** `minimize.prime_implicants` uses recursive Shannon
expansion on the truth vector:
P(f) = P(f0·f1) ∪ x'·(P(f0) \ P(f0·f1)) ∪ x·(P(f1) \ P(f0·f1)).
It produces exactly the same primes as tabular Quine-McCluskey
(`prime_implicants_qm` is kept, and the tests check that the two agree).
Because it works on whole cubes, it doesn't have to build large implicants
up minterm by minterm. On a 16-input function, QM took 141 s and this takes
0.45 s.

**Minimum cover.** Essential primes are selected first. A branch-and-bound
unate-covering search then finishes the cover. At each node it:

* takes essential columns,
* removes dominated primes,
* prunes with an independent-set lower bound, and
* branches on the minterm that has the fewest candidate primes.

The cost is literals first, with the number of terms as a tie-breaker, so
the result is literal-minimal. If a function is too large (200k nodes or
5 s), the engine returns the best cover it found and labels it
"best found" instead of "exact minimum".

**POS minimization** uses duality. The engine minimizes F' as an SOP, then
applies DeMorgan: each product term of F' becomes a sum clause of F.

**Don't-cares** are used when generating primes but never need to be
covered. Primes that cover only don't-cares are excluded from the PI count,
and the output says so.

**Verified output.** Before writing Verilog, the engine simulates the gate
netlist on truth vectors and checks it against the specification. Running
`equiv` with no argument repeats this check for the minimized SOP, the
minimized POS and the netlist.

---

## Input formats

| Kind | Extension | Example |
|------|-----------|---------|
| SOP / POS / multi-level equations | `.eqn` `.txt` `.expr` | `F(A,B,C) = AB' + BC` |
| Minterm / maxterm lists (+ don't-cares) | `.eqn` | `F(A,B,C,D) = m(1,3,7) + d(0,2)`, `G(A,B,C) = M(0,4)` |
| ABC-style EQN with intermediate nodes | `.eqn` | `examples/multilevel.eqn` |
| Espresso PLA (`.type f/fd/fr/fdr`) | `.pla` | `examples/bcd_7seg.pla` |
| BLIF logic network | `.blif` | `examples/full_adder.blif` |
| ISCAS-85 gate netlist | `.bench` | `examples/c17.bench` |
| JSON gate graph | `.json` | `examples/mux4.json` |

**Expression syntax** (lowest to highest precedence):

| Operator | Written as |
|----------|------------|
| OR | `+` or `\|` |
| XOR | `^` |
| AND | `*`, `&`, `.`, or juxtaposition (`A B`, `A(B+C)`) |
| NOT | `A'`, `~A`, `!A` |

You can also use the constants `0` and `1`, and parentheses.

When the inputs are declared, as in `F(A,B,C) = ...`, a run of single
letters such as `AB'C` is split into declared names (`A B' C`). Without a
declaration, every identifier is its own variable, so separate them with
spaces or `*`.

If a file has several equations, each left-hand side becomes an output.
An exception: a name that another equation uses becomes an intermediate
node, i.e. multi-level logic.

**JSON circuit format** (nodes are gates; edges are the names in each gate's `inputs`):

```json
{
  "name": "mux2",
  "inputs": ["s", "a", "b"],
  "outputs": ["y"],
  "gates": [
    {"name": "s_n", "type": "NOT", "inputs": ["s"]},
    {"name": "t0",  "type": "AND", "inputs": ["s_n", "a"]},
    {"name": "t1",  "type": "AND", "inputs": ["s", "b"]},
    {"name": "y",   "type": "OR",  "inputs": ["t0", "t1"]}
  ]
}
```

Supported gate types: AND OR NAND NOR XOR XNOR NOT BUF CONST0 CONST1.

---

## Operations

| # | Command | Output |
|---|---------|--------|
| 1 | `csop` | Canonical SOP, `Sum m(...)` and expanded |
| 2 | `cpos` | Canonical POS, `Prod M(...)` and expanded |
| 3 | `icsop` | Canonical SOP of F' |
| 4 | `icpos` | Canonical POS of F' |
| 5 | `minsop` | Literal-minimized SOP + literals saved vs. canonical SOP |
| 6 | `minpos` | Literal-minimized POS + literals saved vs. canonical POS |
| 7 | `pi` | Number of prime implicants (each listed with the minterms it covers) |
| 8 | `epi` | Number of essential prime implicants (listed) |
| 9 | `on` | Number of ON-set minterms |
| 10 | `off` | Number of OFF-set maxterms |
| 11 | `hazard` | **Custom #1: static hazard detection.** Lists every static-1 hazard in the minimized SOP and every static-0 hazard in the minimized POS. For each, it shows which input toggles and what the other inputs are held at, then gives a hazard-free cover with the consensus terms added. |
| 12 | `equiv [file\|equation]` | **Custom #2: equivalence checking.** Compares the loaded design against another file or equation. Outputs are matched by name, inputs are aligned by name, and don't-cares are respected. Mismatches are shown as counterexample input assignments. With no argument, it checks the engine's own minimized SOP, minimized POS and gate netlist against the specification. |

Extras: `kmap` (Karnaugh map, 2–4 inputs), `truthtable [file.csv]`,
`verilog [file.v] [sop|pos|auto]`, `fpga <dir> [basys3|nexys-a7] [sop|pos|auto]`,
`info`, `use <outputs>`, `limit <n>`.

### Command line (one-shot mode)

```
python -m logicsynth [FILE | -e EQUATION] [options]

  --all                 run operations 1-12
  --ops LIST            e.g. --ops 1-4,minsop,hazard
  --outputs F,G         only operate on these outputs
  --kmap                print Karnaugh maps
  --truthtable [CSV]    print, or export to CSV
  --equiv FILE_OR_EQN   equivalence check against another design
  --verilog FILE        write structural Verilog
  --form auto|sop|pos   form used for Verilog (auto = fewer literals per output)
  --fpga DIR            write a Vivado project
  --board basys3|nexys-a7
  -o REPORT             also write all output to a text file
  --max-terms N         list items shown before "... (+k more)"; 0 = all
  -i                    open the interactive shell after running
```

If you give no operation flags, the interactive shell opens:

```
logicsynth> expr F(A,B,C) = AB' + BC
logicsynth> minsop
logicsynth> hazard
logicsynth> equiv F = AB' + BC + AC
logicsynth> verilog out/f.v
logicsynth> fpga out/f_fpga basys3
logicsynth> help
```

### Example output

```
$ python -m logicsynth examples/hazard.eqn --ops minsop,hazard
[5] Literal-minimized SOP -- F(A, B, C)
  F = BC + AB'
  literals: 4 (canonical SOP: 12) -> saved 8 literals (66.7%)
  product terms: 2 (canonical: 4)
  cover: exact minimum (branch and bound)

[11] Custom #1: static hazard detection -- F(A, B, C)
  minimized SOP: F = BC + AB'
    static-1 hazards: 1
      A=1 C=1, B toggles (m5 <-> m7)
    hazard-free SOP: F = BC + AB' + AC
    (+1 consensus term(s), +2 literals)
  ...
```

---

## Structural Verilog and FPGA deployment

`--verilog` writes one module that contains only primitive gate instances:

```verilog
module c17 (
    input  wire N1, ...
    output wire N22,
    output wire N23
);
    wire N6_n, N3_n, t0, t1, t2, s3, s4;
    not g0 (N6_n, N6);
    and g2 (t0, N2, N6_n);
    or  g5 (N22, t0, t1, t2);
    ...
endmodule
```

* Inverters are shared, and identical product terms are shared between
  outputs.
* With `--form auto` (the default), each output uses SOP (AND-OR) or POS
  (OR-AND), whichever has fewer literals.

`--fpga DIR --board basys3` (or `nexys-a7`) writes these files:

| File | Purpose |
|------|---------|
| `<module>.v` | the generated structural logic |
| `top.v` | wrapper (generated). Switches drive the inputs, LEDs show the outputs, and the 7-segment display shows the current input combination (minterm index) in hex |
| `seg7_hex.v` | multiplexed 7-segment driver |
| `<board>.xdc` | pin constraints |
| `build.tcl` | synthesis → implementation → `top.bit` |
| `program.tcl` | downloads `top.bit` to the board over USB |
| `sim/tb_<module>.v` | exhaustive self-checking testbench (≤ 16 inputs) |

**I/O mapping:**

* Design input *i* (first declared = MSB) is wired to switch `sw[n-1-i]`,
  so the switches read as the binary minterm index, with `sw[0]` as the
  LSB.
* Inputs 17–21 go to the push buttons (C, U, L, R, D). Any inputs beyond
  that are tied to 0, which the assignment allows.
* Output *k* drives `led[k]`.

**Build and program:**

```bash
python -m logicsynth examples/bcd_7seg.pla --fpga out/bcd_fpga --board basys3
cd out/bcd_fpga
vivado -mode batch -source build.tcl      # synthesize, place, route, write top.bit
vivado -mode batch -source program.tcl    # program the connected board
```

You can also do it in the Vivado GUI:

1. Create an RTL project and add `<module>.v`, `top.v` and `seg7_hex.v`.
2. Add `<board>.xdc` as a constraint.
3. Set `top` as the top module.
4. Generate the bitstream and program the device through Hardware Manager.

To simulate, add `sim/tb_<module>.v` as a simulation source and run
behavioral simulation. It prints `PASS` or every mismatch.

Good demo designs:

* `examples/bcd_7seg.pla`: set a BCD digit on the right 4 switches; the
  LEDs show the segment pattern and the 7-segment display shows the digit.
* `examples/full_adder.blif`
* `examples/hazard.eqn`

> **Pin assignments** in `fpga.py` come from the Digilent master XDC files
> for the Basys 3 and Nexys A7. Before programming, check them against the
> master XDC for your board revision.

---

## Limitations

* Up to 20 inputs; a design's truth table must fit in memory.
* Exact minimization is guaranteed only within the search budget. Bigger
  covers are labeled "best found".
* Hazard analysis covers static hazards for single-input changes in
  two-level implementations.
* BLIF/BENCH inputs must be combinational (`.latch`, `.subckt` and `DFF`
  are rejected).

---

## AI usage (per course policy)

This project was developed with help from **Claude (Anthropic)**, an AI
coding assistant. It was used to draft the code architecture, the parsers,
the minimization and cover algorithms, the Verilog/FPGA generators, the
tests and this README. The team reviewed, tested and ran every part, and is
prepared to explain and defend all of the logic during the demo.
