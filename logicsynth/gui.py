"""
Graphical front end (Tkinter, standard library only).

    python -m logicsynth.gui [file]
    python -m logicsynth --gui [file]

Every button calls the same Engine methods as the CLI and shell, and the text
they return is shown in the output pane.  Engine calls run on a worker thread
so the window stays responsive during a long minimization.
"""

import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .engine import OPERATIONS, Engine, EngineError
from .fpga import BOARDS
from .parsers import PARSERS, ParseError

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
FILE_TYPES = [("Design files", " ".join("*" + ext for ext in sorted(PARSERS))),
              ("All files", "*.*")]
ERRORS = (EngineError, ParseError, ValueError, OSError)


class App:
    def __init__(self, root, path=None):
        self.root = root
        self.engine = Engine()
        self.results = queue.Queue()
        self.busy = False
        self.design_widgets = []        # enabled only once a design is loaded

        root.title("EC551 Logic Synthesis Engine")
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=10)
        for family in ("Consolas", "Menlo", "DejaVu Sans Mono"):
            if family in tkfont.families(root):
                self.mono.configure(family=family)
                break
        self.mono_bold = self.mono.copy()
        self.mono_bold.configure(weight="bold")

        self._build_design_bar()
        body = ttk.PanedWindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        body.add(self._build_sidebar(body), weight=0)
        body.add(self._build_output(body), weight=1)
        self.status = tk.StringVar(value="Load a design file or type an equation to begin.")
        ttk.Label(root, textvariable=self.status, anchor="w", relief="sunken",
                  padding=(6, 2)).pack(fill="x", side="bottom")

        self._set_design_state(False)
        self._fit_window()
        if path:
            self.load_file(path)

    # ------------------------------------------------------------------ layout
    def _fit_window(self):
        """Size the window from its content so nothing is clipped at any display scaling."""
        self.root.update_idletasks()
        req_w, req_h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        scr_w, scr_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(max(req_w + int(req_w * 0.3), int(scr_w * 0.7)), int(scr_w * 0.95))
        h = min(req_h + 20, int(scr_h * 0.9))
        self.root.geometry(f"{w}x{h}+{(scr_w - w) // 2}+{max((scr_h - h) // 3, 0)}")
        self.root.minsize(min(req_w, w), min(req_h, h))

    def _build_design_bar(self):
        bar = ttk.LabelFrame(self.root, text="Design", padding=6)
        bar.pack(fill="x", padx=8, pady=6)

        ttk.Button(bar, text="Open file...", command=self.browse_design).grid(row=0, column=0, padx=(0, 6))
        ttk.Label(bar, text="Example:").grid(row=0, column=1)
        examples = sorted(os.listdir(EXAMPLES_DIR)) if os.path.isdir(EXAMPLES_DIR) else []
        self.example = ttk.Combobox(bar, values=examples, state="readonly", width=18)
        self.example.grid(row=0, column=2, padx=(4, 12))
        self.example.bind("<<ComboboxSelected>>",
                          lambda e: self.load_file(os.path.join(EXAMPLES_DIR, self.example.get())))

        ttk.Label(bar, text="Equation:").grid(row=0, column=3)
        self.equation = ttk.Entry(bar, font=self.mono)
        self.equation.grid(row=0, column=4, sticky="ew", padx=4)
        self.equation.insert(0, "F(A,B,C,D) = m(1,3,7,11,15) + d(0,2,5)")
        self.equation.bind("<Return>", lambda e: self.load_equation())
        ttk.Button(bar, text="Load equation", command=self.load_equation).grid(row=0, column=5)
        bar.columnconfigure(4, weight=1)

        self.design_label = tk.StringVar(value="No design loaded")
        ttk.Label(bar, textvariable=self.design_label, foreground="#555").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _build_sidebar(self, parent):
        side = ttk.Frame(parent, padding=(0, 0, 6, 0))

        # outputs
        box = ttk.LabelFrame(side, text="Outputs (none selected = all)", padding=6)
        box.pack(fill="x")
        self.outputs = tk.Listbox(box, selectmode="extended", height=4, exportselection=False,
                                  font=self.mono)
        self.outputs.pack(fill="x")
        self.outputs.bind("<<ListboxSelect>>", lambda e: self.apply_selection())
        self.design_widgets.append(self.outputs)

        # the 12 operations
        box = ttk.LabelFrame(side, text="Operations", padding=6)
        box.pack(fill="x", pady=6)
        self.op_vars = {}
        for i, (num, key, title) in enumerate(OPERATIONS):
            var = tk.BooleanVar(value=False)
            self.op_vars[key] = var
            label = title.replace("Custom #1: ", "").replace("Custom #2: ", "")
            ttk.Checkbutton(box, text=f"{num:>2}. {label}", variable=var).grid(
                row=i % 6, column=i // 6, sticky="w", padx=(0, 8))
        row = ttk.Frame(box)
        row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(row, text="Check all", width=10,
                   command=lambda: self._check_ops(True)).pack(side="left")
        ttk.Button(row, text="Clear", width=8,
                   command=lambda: self._check_ops(False)).pack(side="left", padx=4)
        row = ttk.Frame(box)
        row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        for text, cmd in (("Run checked", self.run_checked), ("Run all 1-12", self.run_all)):
            b = ttk.Button(row, text=text, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=(0, 4))
            self.design_widgets.append(b)

        # views
        box = ttk.LabelFrame(side, text="Views", padding=6)
        box.pack(fill="x")
        for text, cmd in (("Info", lambda: self.run_task("Info", self.engine.info)),
                          ("K-map", lambda: self.run_task("K-map", self.engine.kmap)),
                          ("Truth table", lambda: self.run_task("Truth table", self.engine.truth_table))):
            b = ttk.Button(box, text=text, command=cmd)
            b.pack(side="left", expand=True, fill="x", padx=(0, 4))
            self.design_widgets.append(b)

        # equivalence
        box = ttk.LabelFrame(side, text="Equivalence check (#12)", padding=6)
        box.pack(fill="x", pady=6)
        self.equiv_target = ttk.Entry(box, font=self.mono)
        self.equiv_target.pack(fill="x")
        self.equiv_target.bind("<Return>", lambda e: self.run_equiv())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Browse...", command=self.browse_equiv).pack(side="left")
        b = ttk.Button(row, text="Compare", command=self.run_equiv)
        b.pack(side="left", expand=True, fill="x", padx=4)
        self.design_widgets.append(b)
        ttk.Label(box, text="File or equation. Leave empty to self-check the\n"
                            "minimized SOP, POS and gate netlist.",
                  foreground="#555").pack(anchor="w", pady=(4, 0))

        # export
        box = ttk.LabelFrame(side, text="Verilog / FPGA export", padding=6)
        box.pack(fill="x")
        ttk.Label(box, text="Form:").grid(row=0, column=0, sticky="w")
        self.form = ttk.Combobox(box, values=["auto", "sop", "pos"], state="readonly", width=7)
        self.form.set("auto")
        self.form.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(box, text="Board:").grid(row=0, column=2, sticky="w")
        self.board = ttk.Combobox(box, values=sorted(BOARDS), state="readonly", width=10)
        self.board.set("basys3")
        self.board.grid(row=0, column=3, sticky="w", padx=4)
        buttons = [("Preview Verilog", self.preview_verilog), ("Save Verilog...", self.save_verilog),
                   ("FPGA project...", self.save_fpga), ("Truth table CSV...", self.save_csv)]
        for i, (text, cmd) in enumerate(buttons):
            b = ttk.Button(box, text=text, command=cmd)
            b.grid(row=1 + i // 2, column=(i % 2) * 2, columnspan=2, sticky="ew", padx=(0, 4), pady=(4, 0))
            self.design_widgets.append(b)
        for c in range(4):
            box.columnconfigure(c, weight=1)

        # display limit
        row = ttk.Frame(side, padding=(0, 6))
        row.pack(fill="x")
        ttk.Label(row, text="Max list items shown (0 = all):").pack(side="left")
        self.limit = tk.IntVar(value=self.engine.max_terms)
        ttk.Spinbox(row, from_=0, to=100000, increment=16, width=7, textvariable=self.limit,
                    command=self._set_limit).pack(side="left", padx=4)
        self.limit.trace_add("write", lambda *a: self._set_limit())
        return side

    def _build_output(self, parent):
        frame = ttk.Frame(parent)
        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="Output", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(bar, text="Save report...", command=self.save_report).pack(side="right")
        ttk.Button(bar, text="Copy", command=self.copy_output).pack(side="right", padx=4)
        ttk.Button(bar, text="Clear", command=self.clear_output).pack(side="right")

        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True, pady=(4, 0))
        self.out = tk.Text(holder, wrap="none", font=self.mono, undo=False, padx=8, pady=6,
                           background="#fbfbfb", state="disabled")
        ys = ttk.Scrollbar(holder, orient="vertical", command=self.out.yview)
        xs = ttk.Scrollbar(holder, orient="horizontal", command=self.out.xview)
        self.out.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.out.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.out.tag_configure("banner", foreground="#ffffff", background="#3b5b92", font=self.mono_bold)
        self.out.tag_configure("heading", foreground="#1f4e9a", font=self.mono_bold)
        self.out.tag_configure("good", foreground="#1b7f2a", font=self.mono_bold)
        self.out.tag_configure("bad", foreground="#b3261e", font=self.mono_bold)
        return frame

    # ------------------------------------------------------------ output pane
    def write(self, title, text, error=False):
        self.out.configure(state="normal")
        if self.out.index("end-1c") != "1.0":
            self.out.insert("end", "\n")
        self.out.insert("end", f" {title} \n", "banner")
        start = self.out.index("end-1c")
        self.out.insert("end", text.rstrip("\n") + "\n", "bad" if error else ())
        if not error:
            self._highlight(start)
        self.out.configure(state="disabled")
        self.out.see(start)

    def _highlight(self, start):
        rules = [(r"^\[\d+\][^\n]*|^K-map of[^\n]*|^design '[^\n]*", "heading"),
                 (r"NOT (EQUIVALENT|equivalent)[^\n]*|designs differ|static-[01] hazards: \d+", "bad"),
                 (r"\bEQUIVALENT\b|designs are equivalent|no static-[01] hazards", "good")]
        text = self.out.get(start, "end-1c")
        for pattern, tag in rules:
            for m in re.finditer(pattern, text, re.MULTILINE):
                self.out.tag_add(tag, f"{start}+{m.start()}c", f"{start}+{m.end()}c")

    def clear_output(self):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")

    def copy_output(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.out.get("1.0", "end-1c"))
        self.status.set("Output copied to clipboard.")

    def save_report(self):
        path = filedialog.asksaveasfilename(title="Save report", defaultextension=".txt",
                                            filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.out.get("1.0", "end-1c"))
            self.status.set(f"Report saved to {path}")

    # --------------------------------------------------------- task running
    def run_task(self, title, fn, on_success=None):
        """Run an engine call on a worker thread and show its text output."""
        if self.busy:
            return
        self.busy = True
        self._set_busy(True)
        self.status.set(f"Running: {title} ...")

        def work():
            try:
                self.results.put((title, fn(), None, on_success))
            except ERRORS as e:
                self.results.put((title, None, e, None))
            except Exception as e:      # show unexpected failures instead of losing them
                self.results.put((title, None, f"{type(e).__name__}: {e}", None))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(50, self._poll)

    def _poll(self):
        try:
            title, text, err, on_success = self.results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll)
            return
        self.busy = False
        self._set_busy(False)
        if err is not None:
            self.write(title, f"error: {err}", error=True)
            self.status.set(f"{title}: error")
        else:
            self.write(title, text)
            self.status.set(f"{title}: done")
            if on_success:
                on_success()

    def _set_busy(self, busy):
        self.root.configure(cursor="watch" if busy else "")
        self._set_design_state(not busy and self.engine.design is not None)

    def _set_design_state(self, enabled):
        for w in self.design_widgets:
            w.configure(state="normal" if enabled else "disabled")

    # ------------------------------------------------------------ loading
    def browse_design(self):
        path = filedialog.askopenfilename(title="Open design", filetypes=FILE_TYPES,
                                          initialdir=EXAMPLES_DIR if os.path.isdir(EXAMPLES_DIR) else None)
        if path:
            self.example.set("")
            self.load_file(path)

    def load_file(self, path):
        self.run_task(f"Load {os.path.basename(path)}", lambda: self.engine.load(path), self._loaded)

    def load_equation(self):
        text = self.equation.get().strip()
        if not text:
            return
        self.example.set("")
        self.run_task("Load equation", lambda: self.engine.load_text(text), self._loaded)

    def _loaded(self):
        d = self.engine.design
        self.outputs.delete(0, "end")
        for name in d.outputs:
            self.outputs.insert("end", name)
        self.outputs.configure(height=min(max(len(d.outputs), 2), 8))
        self.design_label.set(f"{d.name}  ({d.source or 'input'})   inputs: {', '.join(d.inputs)}"
                              f"   outputs: {', '.join(d.outputs)}")
        self._set_design_state(True)

    def apply_selection(self):
        if self.engine.design is None:
            return
        names = [self.outputs.get(i) for i in self.outputs.curselection()]
        msg = self.engine.select(names if names and len(names) < self.outputs.size() else ["all"])
        self.status.set(msg)

    def _set_limit(self):
        try:
            self.engine.max_terms = max(0, int(self.limit.get()))
        except (tk.TclError, ValueError):
            pass

    # ---------------------------------------------------------- operations
    def _check_ops(self, value):
        for var in self.op_vars.values():
            var.set(value)

    def run_checked(self):
        keys = [key for _, key, _ in OPERATIONS if self.op_vars[key].get()]
        if not keys:
            messagebox.showinfo("No operations", "Check one or more operations first.")
            return
        equiv_arg = self.equiv_target.get().strip()
        self.run_task("Operations " + ", ".join(keys),
                      lambda: "\n\n".join(self.engine.run(k, equiv_arg if k == "equiv" else "")
                                          for k in keys))

    def run_all(self):
        self.run_task("All operations (1-12)", self.engine.run_all)

    def browse_equiv(self):
        path = filedialog.askopenfilename(title="Design to compare against", filetypes=FILE_TYPES,
                                          initialdir=EXAMPLES_DIR if os.path.isdir(EXAMPLES_DIR) else None)
        if path:
            self.equiv_target.delete(0, "end")
            self.equiv_target.insert(0, path)

    def run_equiv(self):
        arg = self.equiv_target.get().strip()
        self.run_task("Equivalence check", lambda: self.engine.run("equiv", arg))

    # ------------------------------------------------------------- export
    def _default_name(self, ext):
        return (self.engine.design.name if self.engine.design else "design") + ext

    def preview_verilog(self):
        form = self.form.get()
        self.run_task(f"Structural Verilog ({form})", lambda: self.engine.verilog(None, form))

    def save_verilog(self):
        path = filedialog.asksaveasfilename(title="Save structural Verilog", defaultextension=".v",
                                            initialfile=self._default_name(".v"),
                                            filetypes=[("Verilog", "*.v"), ("All files", "*.*")])
        if path:
            form = self.form.get()
            self.run_task("Save Verilog", lambda: self.engine.verilog(path, form))

    def save_fpga(self):
        path = filedialog.askdirectory(title="Choose (or create) a folder for the Vivado project",
                                       mustexist=False)
        if path:
            board, form = self.board.get(), self.form.get()
            self.run_task(f"FPGA project ({board})", lambda: self.engine.fpga(path, board, form))

    def save_csv(self):
        path = filedialog.asksaveasfilename(title="Export truth table", defaultextension=".csv",
                                            initialfile=self._default_name(".csv"),
                                            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if path:
            self.run_task("Truth table CSV", lambda: self.engine.truth_table(path))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        try:                            # render sharply on scaled displays instead of bitmap-stretching
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root, argv[0] if argv else None)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
