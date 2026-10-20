"""
Command-line front end.

One-shot mode (run operations on a file and exit):
    python -m logicsynth examples/hazard.eqn --all
    python -m logicsynth examples/bcd_7seg.pla --ops 5,6,7 --verilog out/bcd.v
    python -m logicsynth -e "F(A,B,C) = AB' + BC" --ops minsop,hazard
    python -m logicsynth examples/full_adder.blif --fpga out/fa_fpga --board basys3

Interactive mode (no operation flags, or -i):
    python -m logicsynth [file]
    logicsynth> csop
"""

import argparse
import cmd
import shlex
import sys

from .engine import OPERATIONS, OP_BY_KEY, Engine, EngineError
from .fpga import BOARDS
from .parsers import ParseError

BANNER = """\
EC551 Logic Synthesis Engine  --  type 'help' for commands, 'quit' to exit
Operations: """ + "  ".join(f"{n}:{k}" for n, k, _ in OPERATIONS)


class Shell(cmd.Cmd):
    prompt = "logicsynth> "

    def __init__(self, engine, report=None):
        super().__init__()
        self.engine = engine
        self.report = report

    def emit(self, text):
        print(text)
        if self.report:
            self.report.write(text + "\n")
            self.report.flush()

    def onecmd(self, line):
        try:
            return super().onecmd(line)
        except (EngineError, ParseError, ValueError, OSError) as e:
            self.emit(f"error: {e}")
        return False

    def emptyline(self):
        pass

    def default(self, line):
        parts = line.split(None, 1)
        key = parts[0].lower()
        if key in OP_BY_KEY:
            self.emit(self.engine.run(key, parts[1] if len(parts) > 1 else ""))
        else:
            self.emit(f"unknown command '{parts[0]}' (type 'help')")

    # --- design management -------------------------------------------------
    def do_load(self, arg):
        """load <file>     load a design (.eqn .txt .pla .blif .bench .json)"""
        self.emit(self.engine.load(arg.strip().strip('"')))

    def do_expr(self, arg):
        """expr <equation>  e.g.  expr F(A,B,C) = AB' + BC   or   expr F(A,B,C,D) = m(1,3,7) + d(0,2)"""
        self.emit(self.engine.load_text(arg))

    def do_info(self, arg):
        """info             show inputs, outputs and set sizes"""
        self.emit(self.engine.info())

    def do_use(self, arg):
        """use <out ...|all> restrict operations to some outputs"""
        self.emit(self.engine.select(arg.split()))

    def do_limit(self, arg):
        """limit <n>        max terms printed per list (0 = unlimited)"""
        self.engine.max_terms = int(arg)
        self.emit(f"display limit = {self.engine.max_terms or 'unlimited'}")

    # --- operations ---------------------------------------------------------
    def do_all(self, arg):
        """all              run operations 1-12"""
        self.emit(self.engine.run_all())

    def do_kmap(self, arg):
        """kmap             print Karnaugh maps (2-4 inputs)"""
        self.emit(self.engine.kmap())

    def do_truthtable(self, arg):
        """truthtable [file.csv]   print or export the truth table"""
        self.emit(self.engine.truth_table(arg.strip() or None))

    def do_verilog(self, arg):
        """verilog [file.v] [sop|pos|auto]   structural Verilog (printed if no file)"""
        path, form = _path_and_form(arg)
        self.emit(self.engine.verilog(path, form))

    def do_fpga(self, arg):
        """fpga <dir> [basys3|nexys-a7] [sop|pos|auto]   generate a Vivado project"""
        args = shlex.split(arg)
        if not args:
            raise EngineError("usage: fpga <dir> [board] [form]")
        board = next((a for a in args[1:] if a in BOARDS), "basys3")
        form = next((a for a in args[1:] if a in ("sop", "pos", "auto")), "auto")
        self.emit(self.engine.fpga(args[0], board, form))

    def do_ops(self, arg):
        """ops              list the 12 operations"""
        self.emit("\n".join(f"  {n:>2}  {k:<7} {t}" for n, k, t in OPERATIONS))

    def do_quit(self, arg):
        """quit             exit"""
        return True

    do_exit = do_quit
    do_EOF = do_quit

    def do_help(self, arg):
        if arg:
            return super().do_help(arg)
        self.emit("""\
Design:      load <file> | expr <equation> | info | use <outputs|all> | limit <n>
Operations:  1 csop   2 cpos   3 icsop   4 icpos   5 minsop   6 minpos
             7 pi     8 epi    9 on     10 off    11 hazard  12 equiv [file|equation]
             all      (run 1-12; type the number or the name)
Extras:      kmap | truthtable [file.csv] | verilog [file.v] [sop|pos|auto]
             fpga <dir> [basys3|nexys-a7] [sop|pos|auto] | ops | quit""")


def _path_and_form(arg):
    path, form = None, "auto"
    for a in shlex.split(arg):
        if a in ("sop", "pos", "auto"):
            form = a
        else:
            path = a
    return path, form


def _parse_ops(text):
    keys = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part and all(p.isdigit() for p in part.split("-")):
            lo, hi = (int(p) for p in part.split("-"))
            keys += [str(k) for k in range(lo, hi + 1)]
        else:
            keys.append(part.lower())
    bad = [k for k in keys if k not in OP_BY_KEY]
    if bad:
        raise SystemExit(f"unknown operation(s): {', '.join(bad)}")
    return keys


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="logicsynth",
        description="EC551 Logic Synthesis Engine: canonical forms, two-level minimization, "
                    "hazard analysis, equivalence checking and structural Verilog / FPGA generation.")
    ap.add_argument("file", nargs="?", help="input design (.eqn .txt .pla .blif .bench .json)")
    ap.add_argument("-e", "--expr", help="equation instead of a file, e.g. \"F(A,B,C) = AB' + C\"")
    ap.add_argument("--all", action="store_true", help="run all 12 operations")
    ap.add_argument("--ops", help="operations to run, e.g. 1-4,minsop,hazard")
    ap.add_argument("--outputs", help="comma-separated subset of outputs to operate on")
    ap.add_argument("--kmap", action="store_true", help="print Karnaugh maps (2-4 inputs)")
    ap.add_argument("--truthtable", metavar="CSV", nargs="?", const="-", help="print or export the truth table")
    ap.add_argument("--equiv", metavar="FILE_OR_EQN", help="check equivalence against another design")
    ap.add_argument("--verilog", metavar="FILE", help="write structural Verilog")
    ap.add_argument("--form", choices=["auto", "sop", "pos"], default="auto",
                    help="two-level form used for Verilog (default: fewer literals)")
    ap.add_argument("--fpga", metavar="DIR", help="write a Vivado project (wrapper, XDC, build script, testbench)")
    ap.add_argument("--board", choices=sorted(BOARDS), default="basys3")
    ap.add_argument("-o", "--report", metavar="FILE", help="also write all text output to this file")
    ap.add_argument("--max-terms", type=int, default=64, help="max list items printed (0 = unlimited)")
    ap.add_argument("-i", "--interactive", action="store_true", help="open the shell after running")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    engine = Engine(max_terms=args.max_terms)
    report = open(args.report, "w", encoding="utf-8") if args.report else None
    shell = Shell(engine, report)

    try:
        if args.file and args.expr:
            raise SystemExit("give either a file or --expr, not both")
        if args.file:
            shell.emit(engine.load(args.file))
        elif args.expr:
            shell.emit(engine.load_text(args.expr))
        if args.outputs:
            shell.emit(engine.select(args.outputs.split(",")))

        did_work = False
        steps = []
        if args.all:
            steps.append(lambda: engine.run_all())
        elif args.ops:
            for k in _parse_ops(args.ops):
                steps.append(lambda k=k: engine.run(k))
        if args.kmap:
            steps.append(engine.kmap)
        if args.truthtable:
            path = None if args.truthtable == "-" else args.truthtable
            steps.append(lambda: engine.truth_table(path))
        if args.equiv:
            steps.append(lambda: engine.run("equiv", args.equiv))
        if args.verilog:
            steps.append(lambda: engine.verilog(args.verilog, args.form))
        if args.fpga:
            steps.append(lambda: engine.fpga(args.fpga, args.board, args.form))
        for step in steps:
            shell.emit("")
            shell.emit(step())
            did_work = True
    except (EngineError, ParseError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        if report:
            report.close()
        return 1

    if not did_work or args.interactive:
        shell.cmdloop(BANNER)
    if report:
        report.close()
    return 0
