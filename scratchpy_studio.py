#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 ScratchPy Studio
===============================================================================
 A single-file, Scratch-style visual programming environment for real Python.

 * Drag colourful puzzle blocks out of a palette and snap them together.
 * Every script you build is compiled into genuine, readable Python source and
   written into a .py file that sits right next to your project.
 * Press the green flag and the generated file is executed for real, with its
   output (and input) wired to the built-in console.
 * A pip dashboard installs ANY package from PyPI.  After installing, the
   package is introspected in a sandboxed subprocess and turned into a fresh
   set of blocks automatically, so every dependency on PyPI can be programmed
   visually.

 Requires nothing but the Python standard library (tkinter).

     python scratchpy_studio.py
     python scratchpy_studio.py --selftest    # headless smoke test

 Author: built for Zayn.  Public domain / MIT-style: do whatever you like.
===============================================================================
"""

from __future__ import annotations

import ast
import json
import keyword as pykeyword
import math
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

APP_NAME = "ScratchPy Studio"
APP_VERSION = "1.0"
PROJECT_EXT = ".spy"
IS_WINDOWS = sys.platform.startswith("win")

# --------------------------------------------------------------------------- #
#  Palette of colours - lifted from the Scratch 3 design language
# --------------------------------------------------------------------------- #

CATS: Dict[str, Dict[str, str]] = {
    "events":    {"name": "Events",    "color": "#FFBF00", "dark": "#CC9900"},
    "control":   {"name": "Control",   "color": "#FFAB19", "dark": "#CF8B17"},
    "operators": {"name": "Operators", "color": "#59C059", "dark": "#389438"},
    "text":      {"name": "Text",      "color": "#9966FF", "dark": "#774DCB"},
    "variables": {"name": "Variables", "color": "#FF8C1A", "dark": "#DB6E00"},
    "lists":     {"name": "Lists",     "color": "#FF661A", "dark": "#E64D00"},
    "files":     {"name": "Files",     "color": "#5CB1D6", "dark": "#2E8EB8"},
    "functions": {"name": "Functions", "color": "#FF6680", "dark": "#FF3355"},
    "python":    {"name": "Python",    "color": "#4C97FF", "dark": "#3373CC"},
    "packages":  {"name": "Packages",  "color": "#0FBD8C", "dark": "#0B8E69"},
}

CAT_ORDER = ["events", "control", "operators", "text", "variables", "lists",
             "files", "functions", "python", "packages"]

UI = {
    "topbar":      "#855CD6",   # Scratch purple menu bar
    "topbar_dark": "#774BC0",
    "toolbar":     "#F9F9F9",
    "panel":       "#FFFFFF",
    "palette_bg":  "#F9F9F9",
    "canvas_bg":   "#F9F9F9",
    "grid":        "#E2E5EA",
    "border":      "#D9D9D9",
    "text":        "#575E75",
    "text_light":  "#FFFFFF",
    "accent":      "#4C97FF",
    "danger":      "#FF4C4C",
    "ok":          "#4CBF56",
    "console_bg":  "#1E2430",
    "console_fg":  "#DDE3EC",
    "console_err": "#FF7B72",
    "console_sys": "#7FD1B9",
    "slot":        "#FFFFFF",
    "slot_text":   "#575E75",
}

FONT_FAMILY = "Segoe UI" if IS_WINDOWS else "Helvetica"
MONO_FAMILY = "Consolas" if IS_WINDOWS else "Menlo"


def hexdark(color: str, factor: float = 0.82) -> str:
    """Return a darker shade of an #rrggbb colour."""
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return "#%02X%02X%02X" % (r, g, b)


def hexlight(color: str, factor: float = 0.35) -> str:
    """Blend an #rrggbb colour towards white."""
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(c + (255 - c) * factor) for c in (r, g, b))
    return "#%02X%02X%02X" % (r, g, b)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
#  Geometry - the puzzle-piece outlines that make a block look like Scratch
# --------------------------------------------------------------------------- #

def _arc(cx: float, cy: float, r: float, a0: float, a1: float,
         steps: int = 5) -> List[Tuple[float, float]]:
    """Sample a circular arc (angles in radians, canvas coordinates)."""
    out = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * (i / steps)
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


class Metrics:
    """All the sizes used to lay a block out, scaled by a zoom factor."""

    def __init__(self, scale: float = 1.0):
        z = self.z = float(scale)
        self.r = 4 * z                 # outer corner radius
        self.notch_x = 13 * z          # where the top notch starts
        self.notch_w = 18 * z          # notch width
        self.notch_d = 5 * z           # notch depth
        self.notch_s = 3 * z           # notch slope
        self.pad_x = 10 * z            # inner horizontal padding
        self.pad_y = 7 * z             # inner vertical padding
        self.gap = 6 * z               # gap between the parts of a row
        self.min_h = 40 * z            # minimum header height
        self.min_w = 68 * z            # minimum block width
        self.slot_h = 24 * z           # height of an input slot
        self.slot_pad = 9 * z
        self.slot_min_w = 32 * z
        self.bool_min_w = 44 * z
        self.hat_h = 16 * z            # height of the hat hump
        self.hat_w = 66 * z            # width of the hat hump
        self.indent = 16 * z           # width of the left bar of a C block
        self.footer_h = 16 * z         # bottom bar of a C block
        self.mouth_min_h = 26 * z      # empty mouth height
        self.arrow_w = 14 * z          # dropdown arrow
        self.script_gap = 14 * z
        self.font_size = max(7, int(round(11.5 * z)))
        self.small_size = max(6, int(round(10 * z)))

    # -- outline builders --------------------------------------------------- #

    def _notch_down(self, x: float, y: float, left_to_right: bool = True):
        """A notch cut/bump of depth notch_d starting at x (block left edge)."""
        nx, nw, nd, ns = self.notch_x, self.notch_w, self.notch_d, self.notch_s
        pts = [(x + nx, y), (x + nx + ns, y + nd),
               (x + nx + nw - ns, y + nd), (x + nx + nw, y)]
        return pts if left_to_right else list(reversed(pts))

    def outline(self, x: float, y: float, w: float,
                segments: List[Tuple[str, float]],
                hat: bool = False, top_notch: bool = True,
                bottom_bump: bool = True) -> List[float]:
        """
        Build the polygon for a block.

        ``segments`` is an alternating list of ("bar", height) and
        ("mouth", height) pieces, always starting and ending with a bar.
        """
        r, ind = self.r, self.indent
        H = sum(h for _, h in segments)
        pts: List[Tuple[float, float]] = []

        # ---- top edge ----
        if hat:
            pts.append((x, y + self.hat_h))
            steps = 12
            for i in range(1, steps + 1):
                t = i / steps
                pts.append((x + self.hat_w * t,
                            y + self.hat_h * (1.0 - math.sin(t * math.pi / 2))))
        else:
            pts.append((x + r, y))
            if top_notch:
                pts.extend(self._notch_down(x, y, True))
        pts.extend(_arc(x + w - r, y + r, r, -math.pi / 2, 0.0))

        # ---- right side, walking down through the segments ----
        cur = y
        for kind, h in segments:
            if kind == "bar":
                cur += h
            else:  # mouth: cut a C shaped bite out of the right hand side
                pts.append((x + w, cur))
                pts.append((x + ind + self.notch_x + self.notch_w, cur))
                pts.extend(self._notch_down(x + ind, cur, False))
                pts.append((x + ind, cur))
                pts.append((x + ind, cur + h))
                pts.append((x + w, cur + h))
                cur += h

        # ---- bottom edge ----
        pts.extend(_arc(x + w - r, y + H - r, r, 0.0, math.pi / 2))
        if bottom_bump:
            pts.extend(self._notch_down(x, y + H, False))
        pts.extend(_arc(x + r, y + H - r, r, math.pi / 2, math.pi))

        # ---- left edge ----
        if hat:
            pts.append((x, y + self.hat_h))
        else:
            pts.extend(_arc(x + r, y + r, r, math.pi, math.pi * 1.5))

        flat: List[float] = []
        for px, py in pts:
            flat.append(px)
            flat.append(py)
        return flat

    def pill(self, x: float, y: float, w: float, h: float) -> List[float]:
        """Rounded 'reporter' capsule."""
        r = min(h / 2.0, w / 2.0)
        pts = []
        pts.extend(_arc(x + w - r, y + r, r, -math.pi / 2, 0.0, 6))
        pts.extend(_arc(x + w - r, y + h - r, r, 0.0, math.pi / 2, 6))
        pts.extend(_arc(x + r, y + h - r, r, math.pi / 2, math.pi, 6))
        pts.extend(_arc(x + r, y + r, r, math.pi, math.pi * 1.5, 6))
        flat = []
        for px, py in pts:
            flat.extend((px, py))
        return flat

    def hexagon(self, x: float, y: float, w: float, h: float) -> List[float]:
        """Pointed 'boolean' hexagon."""
        c = h / 2.0
        return [x + c, y, x + w - c, y, x + w, y + c,
                x + w - c, y + h, x + c, y + h, x, y + c]


# --------------------------------------------------------------------------- #
#  Small helpers for turning slot text into Python source
# --------------------------------------------------------------------------- #

def py_str(s: Any) -> str:
    """Quote a value as a Python string literal (json escaping is compatible)."""
    return json.dumps("" if s is None else str(s), ensure_ascii=False)


def is_expression(text: str) -> bool:
    try:
        ast.parse(text, mode="eval")
        return True
    except Exception:
        return False


def num_expr(text: str) -> str:
    """A numeric slot: pass real expressions through, quote anything else."""
    t = (text or "").strip()
    if not t:
        return "0"
    if is_expression(t):
        return t
    return py_str(t)


def any_expr(text: str) -> str:
    """A 'anything' slot: numbers/booleans stay literal, the rest becomes text."""
    t = (text or "").strip()
    if t == "":
        return '""'
    try:
        v = ast.literal_eval(t)
        if isinstance(v, (int, float, bool)) or v is None:
            return t
    except Exception:
        pass
    return py_str(text if text is not None else "")


def ident(name: str, fallback: str = "value") -> str:
    """Turn arbitrary user text into a safe Python identifier."""
    t = re.sub(r"[^0-9A-Za-z_]", "_", (name or "").strip())
    if not t:
        t = fallback
    if t[0].isdigit():
        t = "_" + t
    if pykeyword.iskeyword(t) or t in ("main", "broadcast", "print", "input"):
        t = t + "_"
    return t


def dedupe(seq):
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# =========================================================================== #
#  SECTION 2 - Block specifications
# =========================================================================== #
#
#  A BlockSpec describes one kind of block: what it looks like and what Python
#  it turns into.  Rows are written in a tiny mark-up language so the whole
#  library stays readable:
#
#      "repeat %n(times,10) times"
#
#  markers
#      %n(name,default)      number slot        -> literal expression
#      %s(name,default)      text slot          -> always quoted
#      %a(name,default)      any slot           -> number if numeric else text
#      %b(name)              boolean slot       -> hexagon shaped hole
#      %r(name,default)      raw slot           -> pasted straight into the code
#      %f(name,default)      f-string slot      -> f"..."
#      %d(name,a|b|c)        dropdown           -> pasted raw
#      %q(name,a|b|c)        dropdown           -> quoted as a string
#      %D(name,@vars)        dropdown           -> sanitised identifier
#
#  Options beginning with "@" are filled in live from the project
#  (@vars, @lists, @msgs, @funcs).
#
#  Code templates use {slotname} for values and a line holding only {BODY0}
#  (or {BODY1} ...) for the contents of a C shaped mouth.
# =========================================================================== #

MARKER_RE = re.compile(r"%([nsabrfdqD])\(([^()]*)\)")

# Problems found while defining blocks; --selftest turns these into failures.
SPEC_PROBLEMS: List[str] = []

KIND_OF = {"n": "num", "s": "str", "a": "any", "b": "bool",
           "r": "raw", "f": "fstr"}


def parse_row(text: str) -> List[tuple]:
    """Turn a mark-up row into a list of tokens."""
    tokens: List[tuple] = []
    pos = 0
    for m in MARKER_RE.finditer(text):
        lead = text[pos:m.start()].strip()
        if lead:
            tokens.append(("t", lead))
        code, body = m.group(1), m.group(2)
        if code in KIND_OF:
            if "," in body:
                name, default = body.split(",", 1)
            else:
                name, default = body, ""
            tokens.append(("i", name.strip(), KIND_OF[code], default))
        else:
            name, _, opts = body.partition(",")
            options = opts if opts.startswith("@") else [
                o for o in opts.split("|") if o != ""]
            mode = {"d": "raw", "q": "str", "D": "ident"}[code]
            default = options[0] if isinstance(options, list) and options else ""
            tokens.append(("d", name.strip(), options, default, mode))
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        tokens.append(("t", tail))
    return tokens


class BlockSpec:
    """One kind of block."""

    def __init__(self, bid: str, category: str, shape: str, rows,
                 code: str = "", imports=(), helpers=(), maps=None,
                 tip: str = "", dynamic: bool = False, meta=None):
        self.id = bid
        self.category = category
        # shape: hat | stack | cap | c | reporter | boolean | define
        self.shape = shape
        if isinstance(rows, str):
            rows = [rows]
        self.raw_rows = list(rows)
        self.rows = [parse_row(r) for r in self.raw_rows]
        self.code = code
        self.imports = tuple(imports)
        self.helpers = tuple(helpers)
        self.maps = maps or {}
        self.tip = tip
        self.dynamic = dynamic
        self.meta = meta or {}
        self.validate()

    def validate(self):
        """Catch mark-up that silently did not parse (a bracket in a default,
        a placeholder with no matching slot).  Checked by --selftest."""
        names = set()
        for raw, tokens in zip(self.raw_rows, self.rows):
            expected = len(re.findall(r"%[nsabrfdqD]\(", raw))
            found = sum(1 for t in tokens if t[0] in ("i", "d"))
            if expected != found:
                SPEC_PROBLEMS.append(
                    "%s: %d of %d inputs did not parse in %r "
                    "(brackets are not allowed inside a default)"
                    % (self.id, expected - found, expected, raw))
            for tok in tokens:
                if tok[0] in ("i", "d"):
                    names.add(tok[1])
        for used in re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)\}", self.code):
            if used.startswith("BODY"):
                continue
            if used not in names:
                SPEC_PROBLEMS.append(
                    "%s: the code uses {%s} but there is no such input"
                    % (self.id, used))

    # ---- handy queries ---------------------------------------------------- #
    @property
    def is_value(self) -> bool:
        return self.shape in ("reporter", "boolean")

    @property
    def is_c(self) -> bool:
        return self.shape == "c"

    @property
    def is_hat(self) -> bool:
        return self.shape in ("hat", "define")

    @property
    def mouths(self) -> int:
        return len(self.rows) if self.shape == "c" else 0

    @property
    def has_next(self) -> bool:
        return self.shape in ("hat", "stack", "c", "define")

    @property
    def has_prev(self) -> bool:
        return self.shape in ("stack", "cap", "c")

    def color(self) -> str:
        # package blocks carry their own colour so every library is distinct
        return (self.meta.get("color") or
                CATS.get(self.category, CATS["python"])["color"])

    def dark(self) -> str:
        return (self.meta.get("dark") or
                CATS.get(self.category, CATS["python"])["dark"])

    def label(self) -> str:
        """Plain text of the block, used for searching."""
        out = []
        for row in self.rows:
            for tok in row:
                if tok[0] == "t":
                    out.append(tok[1])
                elif tok[0] == "i":
                    out.append(str(tok[3]))
                else:
                    out.append(str(tok[3]))
        return " ".join(out)


SPECS: Dict[str, BlockSpec] = {}
PALETTE: Dict[str, List[str]] = {c: [] for c in CAT_ORDER}


def B(bid, category, shape, rows, code="", imports=(), helpers=(), maps=None,
      tip="", listed=True, dynamic=False, meta=None) -> BlockSpec:
    """Define and register a block."""
    spec = BlockSpec(bid, category, shape, rows, code, imports, helpers,
                     maps, tip, dynamic, meta)
    SPECS[bid] = spec
    if listed:
        PALETTE.setdefault(category, []).append(bid)
    return spec


def unregister(bid: str):
    SPECS.pop(bid, None)
    for lst in PALETTE.values():
        if bid in lst:
            lst.remove(bid)


# --------------------------------------------------------------------------- #
#  Runtime helpers - only emitted into the generated file when actually used
# --------------------------------------------------------------------------- #

HELPERS: Dict[str, str] = {
    "messages": '''
_HANDLERS = {}


def when_i_receive(name):
    """Register a function as the handler for a broadcast message."""
    def deco(fn):
        _HANDLERS.setdefault(name, []).append(fn)
        return fn
    return deco


def broadcast(name):
    """Run every handler registered for this message."""
    for fn in _HANDLERS.get(name, []):
        fn()
''',
    "read_text": '''
def read_text(path):
    """Return the whole contents of a text file."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
''',
    "write_text": '''
def write_text(path, text):
    """Overwrite a text file."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(text))
''',
    "append_text": '''
def append_text(path, text):
    """Add a line to the end of a text file."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(str(text) + "\\n")
''',
    "read_csv": '''
def read_csv(path):
    """Return a list of rows (each row a list of strings)."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return [row for row in csv.reader(fh)]
''',
    "write_csv": '''
def write_csv(path, rows):
    """Write a list of rows to a csv file."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
''',
    "timer": '''
_START_TIME = time.time()


def timer():
    """Seconds since the program started."""
    return round(time.time() - _START_TIME, 3)
''',
    "ask": '''
def ask(prompt):
    """Show a question and wait for the answer (like Scratch's ask block)."""
    try:
        return input(str(prompt) + " ")
    except EOFError:
        return ""
''',
}


# =========================================================================== #
#  SECTION 3 - the standard block library
# =========================================================================== #

# ---------------------------------------------------------------- EVENTS --- #

B("event_start", "events", "hat", "when green flag clicked", "",
  tip="Everything under this block runs when the program starts.")

B("event_receive", "events", "hat", "when I receive %q(msg,@msgs)", "",
  helpers=("messages",),
  tip="Runs when another script broadcasts this message.")

B("event_broadcast", "events", "stack", "broadcast %q(msg,@msgs)",
  "broadcast({msg})", helpers=("messages",),
  tip="Run every 'when I receive' script for this message.")

B("event_wait_start", "events", "stack", "wait %n(secs,1) seconds before starting",
  "time.sleep({secs})", imports=("time",))

B("event_banner", "events", "stack",
  "announce %a(msg,Program starting) in the console",
  'print("=" * 40)\nprint({msg})\nprint("=" * 40)')

# --------------------------------------------------------------- CONTROL --- #

B("control_wait", "control", "stack", "wait %n(secs,1) seconds",
  "time.sleep({secs})", imports=("time",))

B("control_repeat", "control", "c", "repeat %n(times,10)",
  "for _ in range(int({times})):\n    {BODY0}",
  tip="Repeat the blocks inside a fixed number of times.")

B("control_forever", "control", "c", "forever",
  "while True:\n    {BODY0}")

B("control_if", "control", "c", "if %b(cond) then",
  "if {cond}:\n    {BODY0}")

B("control_if_else", "control", "c", ["if %b(cond) then", "else"],
  "if {cond}:\n    {BODY0}\nelse:\n    {BODY1}")

B("control_wait_until", "control", "stack", "wait until %b(cond)",
  "while not ({cond}):\n    time.sleep(0.05)", imports=("time",))

B("control_repeat_until", "control", "c", "repeat until %b(cond)",
  "while not ({cond}):\n    {BODY0}")

B("control_while", "control", "c", "while %b(cond)",
  "while {cond}:\n    {BODY0}")

B("control_for_each", "control", "c",
  "for each %D(var,@vars) in %a(seq)",
  "for {var} in {seq}:\n    {BODY0}")

B("control_count", "control", "c",
  "count with %D(var,@vars) from %n(a,1) to %n(b,10)",
  "for {var} in range(int({a}), int({b}) + 1):\n    {BODY0}")

B("control_try", "control", "c", ["try", "if an error happens, call it %r(err,error)"],
  "try:\n    {BODY0}\nexcept Exception as {err}:\n    {BODY1}",
  tip="Run risky blocks and deal with the problem instead of crashing.")

B("control_break", "control", "cap", "break out of the loop", "break")
B("control_continue", "control", "cap", "skip to the next loop turn", "continue")
B("control_pass", "control", "stack", "do nothing", "pass")
B("control_stop_all", "control", "cap", "stop everything",
  "sys.exit(0)", imports=("sys",))

# ------------------------------------------------------------- OPERATORS --- #

B("op_add", "operators", "reporter", "%n(a,) + %n(b,)", "({a} + {b})")
B("op_sub", "operators", "reporter", "%n(a,) - %n(b,)", "({a} - {b})")
B("op_mul", "operators", "reporter", "%n(a,) * %n(b,)", "({a} * {b})")
B("op_div", "operators", "reporter", "%n(a,) / %n(b,)", "({a} / {b})")
B("op_floordiv", "operators", "reporter", "%n(a,) / %n(b,) rounded down",
  "({a} // {b})")
B("op_mod", "operators", "reporter", "%n(a,) mod %n(b,)", "({a} % {b})")
B("op_pow", "operators", "reporter", "%n(a,2) to the power of %n(b,8)", "({a} ** {b})")

B("op_compare", "operators", "boolean",
  "%a(a,) %d(op,<|>|==|!=|<=|>=) %a(b,)", "({a} {op} {b})")

B("op_and", "operators", "boolean", "%b(a) and %b(b)", "({a} and {b})")
B("op_or", "operators", "boolean", "%b(a) or %b(b)", "({a} or {b})")
B("op_not", "operators", "boolean", "not %b(a)", "(not {a})")
B("op_true", "operators", "boolean", "true", "True")
B("op_false", "operators", "boolean", "false", "False")
B("op_in_range", "operators", "boolean",
  "%n(v,5) is between %n(a,1) and %n(b,10)", "({a} <= {v} <= {b})")

B("op_random", "operators", "reporter", "pick random %n(a,1) to %n(b,10)",
  "random.randint(int({a}), int({b}))", imports=("random",))
B("op_random_float", "operators", "reporter", "random decimal 0 to 1",
  "random.random()", imports=("random",))
B("op_random_choice", "operators", "reporter", "pick random item of %a(seq)",
  "random.choice({seq})", imports=("random",))

B("op_round", "operators", "reporter", "round %n(a,)", "round({a})")
B("op_round_to", "operators", "reporter", "round %n(a,) to %n(n,2) decimals",
  "round({a}, int({n}))")

B("op_math", "operators", "reporter",
  "%d(fn,abs|floor|ceil|sqrt|sin|cos|tan|asin|acos|atan|ln|log|e^) of %n(a,9)",
  "{fn}({a})", imports=("math",),
  maps={"fn": {"abs": "abs", "floor": "math.floor", "ceil": "math.ceil",
               "sqrt": "math.sqrt", "sin": "math.sin", "cos": "math.cos",
               "tan": "math.tan", "asin": "math.asin", "acos": "math.acos",
               "atan": "math.atan", "ln": "math.log", "log": "math.log10",
               "e^": "math.exp"}})

B("op_min_max", "operators", "reporter", "%d(fn,min|max) of %n(a,1) and %n(b,2)",
  "{fn}({a}, {b})")

B("op_pi", "operators", "reporter", "pi", "math.pi", imports=("math",))

B("op_convert", "operators", "reporter",
  "%a(v,3.7) as %d(t,int|float|str|bool|list)", "{t}({v})")

B("op_is_number", "operators", "boolean", "%a(v,) is a number",
  "isinstance({v}, (int, float))")

# ------------------------------------------------------------------ TEXT --- #

B("text_print", "text", "stack", "print %a(msg,Hello world!)", "print({msg})",
  tip="Show a message in the console.")

B("text_print2", "text", "stack", "print %a(a,Score:) and %a(b,0)",
  "print({a}, {b})")

B("text_fprint", "text", "stack", "print sentence %f(msg,My score is {score})",
  "print({msg})",
  tip="Anything inside curly braces is replaced by that variable's value.")

B("text_ask", "text", "reporter", "ask %s(prompt,What is your name?) and wait",
  "ask({prompt})", helpers=("ask",))

B("text_join", "text", "reporter", "join %a(a,apple ) %a(b,banana)",
  "(str({a}) + str({b}))")

B("text_fstring", "text", "reporter", "sentence %f(t,Hello {name}!)", "{t}")

B("text_letter", "text", "reporter", "letter %n(i,1) of %a(s,world)",
  "str({s})[int({i}) - 1]")

B("text_length", "text", "reporter", "length of %a(s,world)", "len(str({s}))")

B("text_contains", "text", "boolean", "%a(s,apple) contains %a(sub,a)",
  "(str({sub}) in str({s}))")

B("text_case", "text", "reporter", "%a(s,hello) in %d(case,upper|lower|title) case",
  "str({s}).{case}()")

B("text_replace", "text", "reporter",
  "replace %a(x,a) with %a(y,b) in %a(s,banana)",
  "str({s}).replace(str({x}), str({y}))")

B("text_split", "text", "reporter", "split %a(s,a,b,c) by %a(sep,,)",
  "str({s}).split(str({sep}))")

B("text_strip", "text", "reporter", "%a(s, hi ) without spaces around it",
  "str({s}).strip()")

B("text_repeat", "text", "reporter", "%a(s,ha) repeated %n(n,3) times",
  "(str({s}) * int({n}))")

B("text_find", "text", "reporter", "position of %a(sub,a) in %a(s,banana)",
  "(str({s}).find(str({sub})) + 1)")

B("text_starts", "text", "boolean", "%a(s,banana) starts with %a(sub,ba)",
  "str({s}).startswith(str({sub}))")

B("text_pad", "text", "reporter", "%a(s,7) padded to %n(n,3) characters",
  "str({s}).rjust(int({n}), '0')")

# ------------------------------------------------------------- VARIABLES --- #

B("var_set", "variables", "stack", "set %D(var,@vars) to %a(val,0)",
  "{var} = {val}")

B("var_change", "variables", "stack", "change %D(var,@vars) by %n(val,1)",
  "{var} = {var} + {val}")

B("var_set_expr", "variables", "stack", "set %D(var,@vars) to result of %n(val,1 + 1)",
  "{var} = {val}")

B("var_show", "variables", "stack", "show %D(var,@vars) in the console",
  'print("{var} =", {var})')

# ----------------------------------------------------------------- LISTS --- #

B("list_add", "lists", "stack", "add %a(item,thing) to %D(list,@lists)",
  "{list}.append({item})")

B("list_delete", "lists", "stack", "delete item %n(i,1) of %D(list,@lists)",
  "del {list}[int({i}) - 1]")

B("list_clear", "lists", "stack", "delete all of %D(list,@lists)", "{list}.clear()")

B("list_insert", "lists", "stack",
  "insert %a(item,thing) at %n(i,1) of %D(list,@lists)",
  "{list}.insert(int({i}) - 1, {item})")

B("list_replace", "lists", "stack",
  "replace item %n(i,1) of %D(list,@lists) with %a(item,thing)",
  "{list}[int({i}) - 1] = {item}")

B("list_set_all", "lists", "stack", "set %D(list,@lists) to %a(seq)",
  "{list} = list({seq})")

B("list_item", "lists", "reporter", "item %n(i,1) of %D(list,@lists)",
  "{list}[int({i}) - 1]")

B("list_last", "lists", "reporter", "last item of %D(list,@lists)", "{list}[-1]")

B("list_index", "lists", "reporter", "index of %a(item,thing) in %D(list,@lists)",
  "({list}.index({item}) + 1)")

B("list_length", "lists", "reporter", "length of %D(list,@lists)", "len({list})")

B("list_contains", "lists", "boolean", "%D(list,@lists) contains %a(item,thing)",
  "({item} in {list})")

B("list_join", "lists", "reporter", "join %D(list,@lists) with %a(sep,, )",
  "str({sep}).join(str(_x) for _x in {list})")

B("list_sort", "lists", "stack", "sort %D(list,@lists)", "{list}.sort()")
B("list_reverse", "lists", "stack", "reverse %D(list,@lists)", "{list}.reverse()")
B("list_shuffle", "lists", "stack", "shuffle %D(list,@lists)",
  "random.shuffle({list})", imports=("random",))

B("list_sum", "lists", "reporter", "%d(fn,sum|max|min|len) of %D(list,@lists)",
  "{fn}({list})")

B("list_average", "lists", "reporter", "average of %D(list,@lists)",
  "(sum({list}) / len({list}))")

B("list_copy", "lists", "reporter", "copy of %D(list,@lists)", "list({list})")

B("list_range", "lists", "reporter", "numbers from %n(a,1) to %n(b,10)",
  "list(range(int({a}), int({b}) + 1))")

B("list_new", "lists", "reporter", "empty list", "[]")

B("list_slice", "lists", "reporter",
  "items %n(a,1) to %n(b,3) of %D(list,@lists)",
  "{list}[int({a}) - 1:int({b})]")

# ----------------------------------------------------------------- FILES --- #

B("file_read", "files", "reporter", "text of file %s(path,data.txt)",
  "read_text({path})", helpers=("read_text",))

B("file_lines", "files", "reporter", "lines of file %s(path,data.txt)",
  "read_text({path}).splitlines()", helpers=("read_text",))

B("file_write", "files", "stack",
  "write %a(text,hello) to file %s(path,data.txt)",
  "write_text({path}, {text})", helpers=("write_text",))

B("file_append", "files", "stack",
  "add line %a(text,hello) to file %s(path,data.txt)",
  "append_text({path}, {text})", helpers=("append_text",))

B("file_exists", "files", "boolean", "file %s(path,data.txt) exists",
  "os.path.exists({path})", imports=("os",))

B("file_delete", "files", "stack", "delete file %s(path,data.txt)",
  "os.remove({path})", imports=("os",))

B("file_list", "files", "reporter", "files inside folder %s(path,.)",
  "os.listdir({path})", imports=("os",))

B("file_makedir", "files", "stack", "create folder %s(path,new folder)",
  "os.makedirs({path}, exist_ok=True)", imports=("os",))

B("file_json_read", "files", "reporter", "read JSON from %s(path,data.json)",
  "json.loads(read_text({path}))", imports=("json",), helpers=("read_text",))

B("file_json_write", "files", "stack",
  "save %a(data) as JSON to %s(path,data.json)",
  "write_text({path}, json.dumps({data}, indent=2))",
  imports=("json",), helpers=("write_text",))

B("file_csv_read", "files", "reporter", "read table from %s(path,data.csv)",
  "read_csv({path})", imports=("csv",), helpers=("read_csv",))

B("file_csv_write", "files", "stack", "save table %a(rows) to %s(path,data.csv)",
  "write_csv({path}, {rows})", imports=("csv",), helpers=("write_csv",))

B("file_with", "files", "c",
  "open %s(path,data.txt) as %r(var,f) for %d(mode,r|w|a)",
  'with open({path}, {mode}, encoding="utf-8") as {var}:\n    {BODY0}',
  maps={"mode": {"r": '"r"', "w": '"w"', "a": '"a"'}})

B("sys_shell", "files", "reporter", "output of command %s(cmd,echo hello)",
  "subprocess.run({cmd}, shell=True, capture_output=True, text=True).stdout",
  imports=("subprocess",))

B("sys_time", "files", "reporter",
  "current %d(part,time|date|year|month|day|hour|minute|second|weekday)",
  "{part}", imports=("datetime",),
  maps={"part": {
      "time": 'datetime.datetime.now().strftime("%H:%M:%S")',
      "date": "datetime.date.today().isoformat()",
      "year": "datetime.date.today().year",
      "month": "datetime.date.today().month",
      "day": "datetime.date.today().day",
      "hour": "datetime.datetime.now().hour",
      "minute": "datetime.datetime.now().minute",
      "second": "datetime.datetime.now().second",
      "weekday": 'datetime.date.today().strftime("%A")'}})

B("sys_timer", "files", "reporter", "timer", "timer()",
  imports=("time",), helpers=("timer",))

B("sys_env", "files", "reporter", "setting %s(name,PATH) from the computer",
  'os.environ.get({name}, "")', imports=("os",))

B("sys_args", "files", "reporter", "words typed after the file name",
  "sys.argv[1:]", imports=("sys",))

B("sys_folder", "files", "reporter", "this program's folder",
  "os.path.dirname(os.path.abspath(__file__))", imports=("os",))

B("sys_join", "files", "reporter", "path %s(a,folder) then %s(b,file.txt)",
  "os.path.join({a}, {b})", imports=("os",))

# ------------------------------------------------------------- FUNCTIONS --- #

B("func_return", "functions", "cap", "return %a(val,0)", "return {val}")
B("func_return_nothing", "functions", "cap", "return", "return")

# ---------------------------------------------------------------- PYTHON --- #

B("py_stmt", "python", "stack", "python %r(code,pass)", "{code}",
  tip="Type any line of Python here. It is copied into the file untouched.")

B("py_code", "python", "stack", "python code %r(code,pass)", "{code}",
  tip="A whole chunk of Python kept exactly as it was. "
      "Right click the block to edit it.")

B("py_expr", "python", "reporter", "value of %r(code,2 ** 10)", "{code}",
  tip="Any Python expression - great for things no block covers yet.")

B("py_do", "python", "stack", "do %r(code)", "{code}",
  tip="Run a reporter block (or a line of Python) and throw the result away.")

B("py_comment", "python", "stack", "note %r(text,explain your code)", "# {text}",
  tip="A comment. It does nothing when the program runs.")

B("py_import", "python", "stack", "import %r(module,math)", "import {module}")

B("py_from_import", "python", "stack", "from %r(module,math) import %r(names,sqrt)",
  "from {module} import {names}")

B("py_assign", "python", "stack", "%r(target,x) = %r(value,1)", "{target} = {value}")

B("py_call", "python", "reporter", "call %r(fn,round) with %r(args,3.7)",
  "{fn}({args})")

B("py_attr", "python", "reporter",
  "%r(obj,text) . %r(attr,upper) with %r(args,)", "{obj}.{attr}({args})",
  tip="Use a method of an object, e.g. text.upper()")

B("py_none", "python", "reporter", "nothing", "None")

B("py_type", "python", "reporter", "type of %a(v,hello)", "type({v}).__name__")

B("py_dict_new", "python", "reporter", "empty record", "{}")

B("py_dict_get", "python", "reporter", "%r(d,record) at key %a(k,name)",
  "{d}[{k}]")

B("py_dict_set", "python", "stack", "set key %a(k,name) of %r(d,record) to %a(v,)",
  "{d}[{k}] = {v}")

B("py_raise", "python", "cap", "stop with error %a(msg,something went wrong)",
  "raise Exception({msg})")

B("py_assert", "python", "stack", "check that %b(cond) or fail with %a(msg,oops)",
  "assert {cond}, {msg}")


# =========================================================================== #
#  SECTION 4 - dynamic specs (variables, lists, custom functions)
# =========================================================================== #

def var_spec_id(name: str) -> str:
    return "var::" + name


def list_spec_id(name: str) -> str:
    return "list::" + name


def ensure_var_spec(name: str) -> BlockSpec:
    """A rounded reporter that simply reads a variable."""
    bid = var_spec_id(name)
    if bid not in SPECS:
        B(bid, "variables", "reporter", [name], ident(name),
          listed=False, dynamic=True, meta={"var": name})
    return SPECS[bid]


def ensure_list_spec(name: str) -> BlockSpec:
    bid = list_spec_id(name)
    if bid not in SPECS:
        B(bid, "lists", "reporter", [name], ident(name),
          listed=False, dynamic=True, meta={"list": name})
    return SPECS[bid]


def ensure_param_spec(func: str, param: str) -> BlockSpec:
    bid = "param::%s::%s" % (func, param)
    if bid not in SPECS:
        B(bid, "functions", "reporter", [param], ident(param),
          listed=False, dynamic=True, meta={"param": param, "func": func})
    return SPECS[bid]


def ensure_func_specs(fn: dict) -> Tuple[BlockSpec, BlockSpec]:
    """
    Build the two blocks that make up a custom function: the 'define' hat and
    the block that calls it.
    """
    name = fn["name"]
    params = [p for p in fn.get("params", []) if p.strip()]
    returns = bool(fn.get("returns"))
    pyname = ident(name, "my_block")

    label = "define " + name
    if params:
        label += " (" + ", ".join(params) + ")"
    def_id = "func::%s::def" % name
    unregister(def_id)
    def_spec = B(def_id, "functions", "define", [label], "",
                 listed=False, dynamic=True,
                 meta={"func": name, "params": params, "returns": returns,
                       "pyname": pyname})

    call_id = "func::%s" % name
    unregister(call_id)
    row = name
    for i, p in enumerate(params):
        row += " %a(p" + str(i) + "," + str(p) + ")"
    args = ", ".join("{p" + str(i) + "}" for i in range(len(params)))
    code = pyname + "(" + args + ")"
    call_spec = B(call_id, "functions", "reporter" if returns else "stack",
                  [row], code, listed=False, dynamic=True,
                  meta={"func": name, "params": params, "returns": returns,
                        "pyname": pyname})
    for p in params:
        ensure_param_spec(name, p)
    return def_spec, call_spec


# =========================================================================== #
#  SECTION 5 - the document model: blocks, files and projects
# =========================================================================== #

class Block:
    """One block placed in a workspace."""

    __slots__ = ("id", "spec", "values", "fields", "next", "prev",
                 "branches", "parent", "parent_slot", "x", "y", "collapsed")

    def __init__(self, spec: BlockSpec, bid: Optional[str] = None):
        self.id = bid or new_id()
        self.spec = spec
        self.values: Dict[str, Any] = {}
        self.fields: Dict[str, str] = {}
        self.next: Optional["Block"] = None
        self.prev: Optional["Block"] = None
        self.branches: List[Optional["Block"]] = [None] * spec.mouths
        self.parent: Optional["Block"] = None      # owner for slots/mouths
        self.parent_slot: Optional[Any] = None     # slot name or mouth index
        self.x = 0.0
        self.y = 0.0
        self.collapsed = False
        # fill in the defaults declared by the spec
        for row in spec.rows:
            for tok in row:
                if tok[0] == "i":
                    self.values[tok[1]] = tok[3]
                elif tok[0] == "d":
                    self.fields[tok[1]] = tok[3]

    # -- structure ---------------------------------------------------------- #

    def top(self) -> "Block":
        """The first block of the script this block belongs to."""
        b = self
        while True:
            if b.prev is not None:
                b = b.prev
            elif b.parent is not None:
                b = b.parent
            else:
                return b

    def chain(self):
        """Yield this block and everything stacked under it."""
        b = self
        while b is not None:
            yield b
            b = b.next

    def last(self) -> "Block":
        b = self
        while b.next is not None:
            b = b.next
        return b

    def descendants(self):
        """Every block in this sub-tree (slots, mouths and following blocks)."""
        yield self
        for v in self.values.values():
            if isinstance(v, Block):
                yield from v.descendants()
        for br in self.branches:
            if br is not None:
                yield from br.descendants()
        if self.next is not None:
            yield from self.next.descendants()

    def body_only(self):
        """This block's own sub-tree, ignoring blocks stacked underneath."""
        yield self
        for v in self.values.values():
            if isinstance(v, Block):
                yield from v.descendants()
        for br in self.branches:
            if br is not None:
                yield from br.descendants()

    # -- editing ------------------------------------------------------------ #

    def detach(self):
        """Unhook this block (and its followers) from whatever holds it."""
        if self.prev is not None:
            self.prev.next = None
            self.prev = None
        elif self.parent is not None:
            p, slot = self.parent, self.parent_slot
            if isinstance(slot, int):
                p.branches[slot] = None
            else:
                tok = p.slot_token(slot)
                p.values[slot] = tok[3] if tok else ""
            self.parent = None
            self.parent_slot = None

    def slot_token(self, name):
        for row in self.spec.rows:
            for tok in row:
                if tok[0] == "i" and tok[1] == name:
                    return tok
        return None

    def slot_kind(self, name) -> str:
        tok = self.slot_token(name)
        return tok[2] if tok else "any"

    def attach_next(self, other: "Block"):
        """Stick ``other`` (a stack) directly under this block."""
        tail = other.last()
        following = self.next
        self.next = other
        other.prev = self
        other.parent = None
        other.parent_slot = None
        if following is not None:
            tail.next = following
            following.prev = tail

    def attach_branch(self, index: int, other: "Block"):
        existing = self.branches[index]
        self.branches[index] = other
        other.prev = None
        other.parent = self
        other.parent_slot = index
        if existing is not None:
            other.last().attach_next(existing)

    def attach_slot(self, name: str, other: "Block"):
        old = self.values.get(name)
        self.values[name] = other
        other.prev = None
        other.parent = self
        other.parent_slot = name
        return old if isinstance(old, Block) else None

    def copy(self) -> "Block":
        return Block.from_json(self.to_json(), keep_ids=False)

    # -- persistence -------------------------------------------------------- #

    def to_json(self) -> dict:
        data: Dict[str, Any] = {"spec": self.spec.id, "id": self.id}
        vals = {}
        for k, v in self.values.items():
            vals[k] = {"__block__": v.to_json()} if isinstance(v, Block) else v
        if vals:
            data["values"] = vals
        if self.fields:
            data["fields"] = dict(self.fields)
        if any(self.branches):
            data["branches"] = [b.to_json() if b else None for b in self.branches]
        if self.next is not None:
            data["next"] = self.next.to_json()
        if self.parent is None and self.prev is None:
            data["x"], data["y"] = round(self.x, 1), round(self.y, 1)
        return data

    @staticmethod
    def from_json(data: dict, keep_ids: bool = True) -> Optional["Block"]:
        if not data:
            return None
        spec = SPECS.get(data.get("spec"))
        if spec is None:
            spec = missing_spec(data.get("spec", "?"))
        b = Block(spec, data.get("id") if keep_ids else None)
        for k, v in (data.get("values") or {}).items():
            if isinstance(v, dict) and "__block__" in v:
                child = Block.from_json(v["__block__"], keep_ids)
                if child is not None:
                    b.values[k] = child
                    child.parent = b
                    child.parent_slot = k
            else:
                b.values[k] = v
        b.fields.update(data.get("fields") or {})
        branches = data.get("branches") or []
        for i, bd in enumerate(branches):
            if i < len(b.branches) and bd:
                child = Block.from_json(bd, keep_ids)
                if child is not None:
                    b.branches[i] = child
                    child.parent = b
                    child.parent_slot = i
        if data.get("next"):
            nxt = Block.from_json(data["next"], keep_ids)
            if nxt is not None:
                b.next = nxt
                nxt.prev = b
        b.x = float(data.get("x", 0.0))
        b.y = float(data.get("y", 0.0))
        return b


def missing_spec(bid: str) -> BlockSpec:
    """Placeholder so a project still opens if a block type has vanished."""
    key = "missing::" + bid
    if key not in SPECS:
        B(key, "python", "stack", ["missing block " + bid],
          "# missing block: " + bid, listed=False, dynamic=True)
    return SPECS[key]


class SpyFile:
    """One tab / one generated .py file."""

    def __init__(self, name: str = "main"):
        self.name = name
        self.scripts: List[Block] = []
        self.scroll = (0.0, 0.0)
        # Imports and chunks of plain Python kept from an imported file. They
        # are written at the top of the generated module, outside main().
        self.header_imports: List[str] = []
        self.header_code: List[str] = []

    def add_script(self, block: Block, x: float, y: float):
        block.x, block.y = x, y
        self.scripts.append(block)

    def remove_script(self, block: Block):
        if block in self.scripts:
            self.scripts.remove(block)

    def all_blocks(self):
        for s in self.scripts:
            yield from s.descendants()

    def to_json(self) -> dict:
        data = {"name": self.name,
                "scripts": [s.to_json() for s in self.scripts]}
        if self.header_imports:
            data["header_imports"] = self.header_imports
        if self.header_code:
            data["header_code"] = self.header_code
        return data

    @staticmethod
    def from_json(data: dict) -> "SpyFile":
        f = SpyFile(data.get("name", "main"))
        for sd in data.get("scripts", []):
            b = Block.from_json(sd)
            if b is not None:
                f.scripts.append(b)
        f.header_imports = list(data.get("header_imports", []))
        f.header_code = list(data.get("header_code", []))
        return f


class Project:
    """Everything the user is working on."""

    def __init__(self):
        self.files: List[SpyFile] = [SpyFile("main")]
        self.variables: List[str] = ["score"]
        self.var_init: Dict[str, str] = {"score": "0"}
        self.lists: List[str] = ["things"]
        self.messages: List[str] = ["message1"]
        self.functions: List[dict] = []
        self.packs: List[dict] = []
        self.path: Optional[str] = None
        self.dirty = False
        self.sync_specs()

    # -- names -------------------------------------------------------------- #

    @property
    def name(self) -> str:
        if self.path:
            return os.path.splitext(os.path.basename(self.path))[0]
        return "Untitled"

    def folder(self) -> str:
        if self.path:
            return os.path.dirname(os.path.abspath(self.path))
        return DEFAULT_FOLDER

    def file_by_name(self, name: str) -> Optional[SpyFile]:
        for f in self.files:
            if f.name == name:
                return f
        return None

    def unique_file_name(self, base: str = "script") -> str:
        n, name = 1, base
        while self.file_by_name(name):
            n += 1
            name = "%s%d" % (base, n)
        return name

    # -- dynamic block specs ------------------------------------------------ #

    def sync_specs(self):
        """Make sure a block exists for every variable, list and function."""
        for v in self.variables:
            ensure_var_spec(v)
        for l in self.lists:
            ensure_list_spec(l)
        for fn in self.functions:
            ensure_func_specs(fn)
        for pack in self.packs:
            if not pack.get("color"):
                taken = [p.get("color", "") for p in self.packs if p is not pack]
                pack["color"], pack["dark"] = pick_pack_color(
                    pack.get("module", ""), taken)
            register_pack(pack)

    def options_for(self, source: str) -> List[str]:
        if source == "@vars":
            return list(self.variables) or ["score"]
        if source == "@lists":
            return list(self.lists) or ["things"]
        if source == "@msgs":
            return list(self.messages) or ["message1"]
        if source == "@funcs":
            return [f["name"] for f in self.functions] or ["my block"]
        return []

    def function_by_name(self, name: str) -> Optional[dict]:
        for f in self.functions:
            if f["name"] == name:
                return f
        return None

    # -- persistence -------------------------------------------------------- #

    def to_json(self) -> dict:
        return {
            "app": APP_NAME,
            "version": APP_VERSION,
            "files": [f.to_json() for f in self.files],
            "variables": self.variables,
            "var_init": self.var_init,
            "lists": self.lists,
            "messages": self.messages,
            "functions": self.functions,
            "packs": self.packs,
        }

    @staticmethod
    def from_json(data: dict) -> "Project":
        p = Project()
        p.variables = list(data.get("variables", []))
        p.var_init = dict(data.get("var_init", {}))
        p.lists = list(data.get("lists", []))
        p.messages = list(data.get("messages", ["message1"])) or ["message1"]
        p.functions = list(data.get("functions", []))
        p.packs = list(data.get("packs", []))
        p.sync_specs()
        files = [SpyFile.from_json(fd) for fd in data.get("files", [])]
        p.files = files or [SpyFile("main")]
        return p

    def snapshot(self) -> str:
        return json.dumps(self.to_json())


# =========================================================================== #
#  SECTION 6 - the compiler: blocks in, real Python out
# =========================================================================== #

FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z_0-9]*)\}")
BODY_RE = re.compile(r"^(\s*)\{BODY(\d+)\}\s*$")


def with_pass(lines: List[str]) -> List[str]:
    """Python needs a real statement, so a body of only comments gets a pass."""
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return list(lines)
    return list(lines) + ["pass"]

STDLIB_SAFE = {"time", "os", "sys", "json", "math", "random", "csv",
               "datetime", "subprocess", "re", "statistics", "pathlib"}


def safe_module_name(name: str) -> str:
    n = re.sub(r"[^0-9A-Za-z_]", "_", (name or "script").strip())
    if not n or n[0].isdigit():
        n = "s_" + n
    return n


class CodeGen:
    """Turns one SpyFile full of scripts into a Python module."""

    def __init__(self, project: "Project", spyfile: "SpyFile"):
        self.project = project
        self.file = spyfile
        self.imports: List[str] = []
        self.helpers: List[str] = []
        self.notes: List[str] = []

    # -- little pieces ------------------------------------------------------ #

    def need(self, spec: BlockSpec):
        for imp in spec.imports:
            if imp not in self.imports:
                self.imports.append(imp)
        for h in spec.helpers:
            if h not in self.helpers:
                self.helpers.append(h)

    def slot_value(self, block: Block, name: str, kind: str) -> str:
        v = block.values.get(name)
        if isinstance(v, Block):
            return self.expr(v)
        text = "" if v is None else str(v)
        if kind == "num":
            return num_expr(text)
        if kind == "str":
            return py_str(text)
        if kind == "any":
            return any_expr(text)
        if kind == "bool":
            t = text.strip()
            if not t:
                return "False"
            return t if is_expression(t) else py_str(t)
        if kind == "fstr":
            return "f" + py_str(text)
        return text  # raw

    def field_value(self, block: Block, tok) -> str:
        _, name, options, default, mode = tok
        val = block.fields.get(name, default)
        mapping = block.spec.maps.get(name)
        if mapping:
            return mapping.get(val, val)
        if mode == "str":
            return py_str(val)
        if mode == "ident":
            return ident(val)
        return str(val)

    def values_of(self, block: Block) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for row in block.spec.rows:
            for tok in row:
                if tok[0] == "i":
                    out[tok[1]] = self.slot_value(block, tok[1], tok[2])
                elif tok[0] == "d":
                    out[tok[1]] = self.field_value(block, tok)
        return out

    def render(self, tpl: str, values: Dict[str, str],
               bodies: List[List[str]]) -> List[str]:
        out: List[str] = []

        def repl(m):
            return values.get(m.group(1), m.group(0))

        for line in tpl.split("\n"):
            mb = BODY_RE.match(line)
            if mb:
                pad = mb.group(1)
                idx = int(mb.group(2))
                body = with_pass(bodies[idx] if idx < len(bodies) else [])
                out.extend(pad + l for l in body)
                continue
            filled = FIELD_RE.sub(repl, line)
            pad = re.match(r"^[ \t]*", line).group(0)
            parts = filled.split("\n")
            out.append(parts[0])
            for extra in parts[1:]:
                out.append(pad + extra)
        return out

    # -- expressions -------------------------------------------------------- #

    def expr(self, block: Block) -> str:
        self.need(block.spec)
        code = block.spec.code or "None"
        values = self.values_of(block)

        def repl(m):
            return values.get(m.group(1), m.group(0))

        return FIELD_RE.sub(repl, code)

    # -- statements --------------------------------------------------------- #

    def stmts(self, block: Block) -> List[str]:
        spec = block.spec
        self.need(spec)
        bodies = [self.stack(br) for br in block.branches]
        if not spec.code.strip():
            return []
        lines = self.render(spec.code, self.values_of(block), bodies)
        lines = [l for l in lines if l.strip() != ""]
        return lines

    def stack(self, first: Optional[Block]) -> List[str]:
        out: List[str] = []
        b = first
        while b is not None:
            if b.spec.is_value:
                out.append("_ = " + self.expr(b))
            else:
                out.extend(self.stmts(b))
            b = b.next
        return out

    # -- whole file --------------------------------------------------------- #

    def globals_used(self, first: Optional[Block]) -> List[str]:
        names: List[str] = []
        known = {ident(v): v for v in self.project.variables}
        known_lists = {ident(v): v for v in self.project.lists}
        b = first
        while b is not None:
            for d in b.body_only():
                sid = d.spec.id
                if sid in ("var_set", "var_change", "var_set_expr"):
                    names.append(ident(d.fields.get("var", "")))
                elif sid in ("control_for_each", "control_count"):
                    names.append(ident(d.fields.get("var", "")))
                elif sid == "list_set_all":
                    names.append(ident(d.fields.get("list", "")))
                elif sid == "py_assign":
                    tgt = str(d.values.get("target", "")).strip()
                    if tgt.isidentifier():
                        names.append(tgt)
            b = b.next
        out = [n for n in dedupe(names) if n in known or n in known_lists]
        return out

    def body_with_globals(self, first: Optional[Block],
                          skip: List[str] = ()) -> List[str]:
        lines: List[str] = []
        gl = [g for g in self.globals_used(first) if g not in skip]
        if gl:
            lines.append("global " + ", ".join(gl))
        lines.extend(self.stack(first))
        return with_pass(lines)

    def generate(self) -> str:
        p = self.project
        starts: List[Block] = []
        receivers: List[Block] = []
        defines: List[Block] = []
        loose = 0

        for script in self.file.scripts:
            sid = script.spec.id
            if sid == "event_start":
                starts.append(script)
            elif sid == "event_receive":
                receivers.append(script)
            elif script.spec.shape == "define":
                defines.append(script)
            else:
                loose += 1

        chunks: List[str] = []

        # ---- function definitions ----
        func_src: List[str] = []
        for d in defines:
            meta = d.spec.meta
            name = meta.get("pyname") or ident(meta.get("func", "my_block"))
            params = [ident(x) for x in meta.get("params", [])]
            head = "def %s(%s):" % (name, ", ".join(params))
            body = self.body_with_globals(d.next, skip=params)
            doc = '    """Custom block: %s"""' % meta.get("func", name)
            func_src.append(head)
            func_src.append(doc)
            func_src.extend("    " + l for l in body)
            func_src.append("")

        # ---- broadcast handlers ----
        handler_src: List[str] = []
        for i, r in enumerate(receivers):
            if "messages" not in self.helpers:
                self.helpers.append("messages")
            msg = r.fields.get("msg", "message1")
            fname = "on_" + ident(msg, "message") + ("" if i == 0 else "_%d" % i)
            handler_src.append('@when_i_receive(%s)' % py_str(msg))
            handler_src.append("def %s():" % fname)
            handler_src.append('    """Runs when %s is broadcast."""' % msg)
            body = self.body_with_globals(r.next)
            handler_src.extend("    " + l for l in body)
            handler_src.append("")

        # ---- main ----
        main_src: List[str] = []
        all_main_globals: List[str] = []
        for s in starts:
            all_main_globals.extend(self.globals_used(s.next))
        all_main_globals = dedupe(all_main_globals)
        if all_main_globals:
            main_src.append("global " + ", ".join(all_main_globals))
        for s in starts:
            main_src.extend(self.stack(s.next))
        main_src = with_pass(main_src)

        # ---- assemble ----
        head: List[str] = []
        head.append('"""')
        head.append("%s - generated by %s %s." % (self.file.name, APP_NAME,
                                                  APP_VERSION))
        head.append("")
        head.append("This file is written automatically from the blocks in the")
        head.append("'%s' tab.  Editing it by hand is fine, but the next time" %
                    self.file.name)
        head.append("you press the green flag your changes will be replaced.")
        head.append('"""')
        head.append("")

        kept = list(self.file.header_imports)
        for imp in sorted(dedupe(self.imports)):
            line = imp if (imp.startswith("import ") or
                           imp.startswith("from ")) else "import " + imp
            if line not in kept:
                kept.append(line)
        for line in kept:
            head.append(line)
        if kept:
            head.append("")

        for chunk in self.file.header_code:
            head.append(chunk.rstrip())
            head.append("")
            head.append("")

        for h in dedupe(self.helpers):
            src = HELPERS.get(h)
            if src:
                head.append(src.strip("\n"))
                head.append("")

        var_src: List[str] = []
        if p.variables or p.lists:
            var_src.append("# ---- variables ----")
            for v in p.variables:
                init = p.var_init.get(v, "0")
                var_src.append("%s = %s" % (ident(v), init if init.strip() else "0"))
            for l in p.lists:
                var_src.append("%s = []" % ident(l))
            var_src.append("")

        chunks.extend(head)
        chunks.extend(var_src)
        chunks.extend(func_src)
        chunks.extend(handler_src)
        chunks.append("def main():")
        chunks.append('    """Everything under the green flag."""')
        chunks.extend("    " + l for l in main_src)
        chunks.append("")
        chunks.append("")
        chunks.append('if __name__ == "__main__":')
        chunks.append("    main()")
        if loose:
            chunks.append("")
            chunks.append("# %d script%s in this tab %s not connected to a hat"
                          % (loose, "" if loose == 1 else "s",
                             "is" if loose == 1 else "are"))
            chunks.append("# block, so it was not included.")

        text = "\n".join(chunks).rstrip() + "\n"
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text


def generate_file(project: "Project", spyfile: "SpyFile") -> str:
    return CodeGen(project, spyfile).generate()


def textwrap_dedent(text: str) -> str:
    """Remove the common leading spaces so a snippet can be checked alone."""
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return text
    pad = min(len(l) - len(l.lstrip()) for l in lines)
    if not pad:
        return text
    return "\n".join(l[pad:] if len(l) >= pad else l for l in text.split("\n"))


def check_syntax(source: str) -> Optional[str]:
    try:
        compile(source, "<generated>", "exec")
        return None
    except SyntaxError as exc:
        return "line %s: %s" % (exc.lineno, exc.msg)


# =========================================================================== #
#  SECTION 6b - the importer: any Python file becomes blocks
# =========================================================================== #
#
#  Every statement the editor understands turns into a proper block.  Anything
#  it does not understand (classes, decorators, comprehension heavy code) is
#  kept word for word inside a "python code" block, so no program is ever
#  refused and nothing is ever silently lost.
# =========================================================================== #

BIN_OPS = {ast.Add: "op_add", ast.Sub: "op_sub", ast.Mult: "op_mul",
           ast.Div: "op_div", ast.FloorDiv: "op_floordiv", ast.Mod: "op_mod",
           ast.Pow: "op_pow"}

CMP_OPS = {ast.Lt: "<", ast.Gt: ">", ast.Eq: "==", ast.NotEq: "!=",
           ast.LtE: "<=", ast.GtE: ">="}

MATH_CALLS = {"math.sqrt": "sqrt", "math.floor": "floor", "math.ceil": "ceil",
              "math.sin": "sin", "math.cos": "cos", "math.tan": "tan",
              "math.log": "ln", "math.log10": "log", "math.exp": "e^"}

try:
    STDLIB_NAMES = set(sys.stdlib_module_names)
except AttributeError:                                   # pragma: no cover
    STDLIB_NAMES = set(sys.builtin_module_names) | {
        "os", "sys", "re", "json", "math", "random", "time", "datetime", "csv",
        "typing", "abc", "enum", "turtle", "tkinter", "subprocess", "pathlib",
        "collections", "itertools", "statistics", "sqlite3", "urllib", "shutil"}

DIST_FOR_MODULE = {"PIL": "pillow", "bs4": "beautifulsoup4", "cv2": "opencv-python",
                   "sklearn": "scikit-learn", "yaml": "pyyaml",
                   "dateutil": "python-dateutil", "attr": "attrs",
                   "Crypto": "pycryptodome", "docx": "python-docx",
                   "pptx": "python-pptx", "serial": "pyserial",
                   "OpenGL": "PyOpenGL", "win32com": "pywin32"}


def same_literal(a: str, b: str) -> bool:
    """Do two pieces of Python source mean exactly the same constant?"""
    try:
        va, vb = ast.literal_eval(a), ast.literal_eval(b)
    except Exception:
        return a == b
    return type(va) is type(vb) and va == vb


def source_of(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:                                    # pragma: no cover
        return "pass"


class PythonImporter:
    """Reads Python source and builds the blocks that produce it."""

    def __init__(self, project: "Project"):
        self.project = project
        self.notes: List[str] = []
        self.packages: List[str] = []
        self.params: List[str] = []          # parameters of the function we are in
        self.func_name = ""
        self.handled = 0
        self.raw = 0

    # -- entry point -------------------------------------------------------- #

    def convert(self, source: str, name: str = "imported") -> "SpyFile":
        tree = ast.parse(source)
        spyfile = SpyFile(safe_module_name(name))
        self.collect_functions(tree)
        self.collect_names(tree)

        main_body: List[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.remember_import(node)
                spyfile.header_imports.append(source_of(node))
            elif isinstance(node, ast.FunctionDef) and not node.decorator_list:
                script = self.function_script(node)
                if script is not None:
                    spyfile.scripts.append(script)
            elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) or \
                    (isinstance(node, ast.FunctionDef) and node.decorator_list):
                spyfile.header_code.append(source_of(node))
                self.raw += 1
                self.notes.append("kept %s as plain Python at the top of the file"
                                  % getattr(node, "name", "a block of code"))
            elif self.is_main_guard(node):
                main_body.extend(node.body)
            elif isinstance(node, ast.Assign) and self.is_constant_setup(node):
                self.remember_constant(node)
            else:
                main_body.append(node)

        hat = Block(SPECS["event_start"])
        hat.x, hat.y = 80.0, 60.0
        blocks = self.body(main_body)
        if blocks:
            link_blocks(hat, blocks)
        spyfile.scripts.insert(0, hat)
        self.layout(spyfile)
        return spyfile

    def layout(self, spyfile: "SpyFile"):
        y = 60.0
        for script in spyfile.scripts:
            script.x = 80.0
            script.y = y
            y += 140.0 + 34.0 * sum(1 for _ in script.descendants())

    # -- first pass: learn the names -------------------------------------- --#

    def is_main_guard(self, node) -> bool:
        if not isinstance(node, ast.If):
            return False
        text = source_of(node.test)
        return "__name__" in text and "__main__" in text

    def is_constant_setup(self, node: ast.Assign) -> bool:
        return (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant))

    def remember_constant(self, node: ast.Assign):
        name = node.targets[0].id
        if name not in self.project.variables:
            self.project.variables.append(name)
            ensure_var_spec(name)
        self.project.var_init[name] = repr(node.value.value)

    def remember_import(self, node):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif node.module:
            names = [node.module]
        for full in names:
            top = full.split(".")[0]
            if top and top not in STDLIB_NAMES and top not in self.packages:
                self.packages.append(top)

    def collect_functions(self, tree: ast.Module):
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.decorator_list:
                continue
            params = [a.arg for a in node.args.args
                      if a.arg not in ("self", "cls")]
            returns = any(isinstance(n, ast.Return) and n.value is not None
                          for n in ast.walk(node))
            entry = {"name": node.name, "params": params, "returns": returns}
            existing = self.project.function_by_name(node.name)
            if existing:
                existing.update(entry)
            else:
                self.project.functions.append(entry)
            ensure_func_specs(entry)

    def collect_names(self, tree: ast.Module):
        """Work out which names are variables and which are lists."""
        lists, variables = [], []
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                value = node.value
            elif isinstance(node, ast.AugAssign):
                target, value = node.target, node.value
            elif isinstance(node, ast.For):
                target, value = node.target, None
            else:
                continue
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            listy = isinstance(value, (ast.List, ast.ListComp)) or (
                isinstance(value, ast.Call) and
                source_of(value.func).split(".")[-1] in
                ("list", "sorted", "split", "splitlines", "readlines"))
            if listy:
                if name not in lists:
                    lists.append(name)
            elif name not in variables:
                variables.append(name)
        for name in lists:
            if name not in self.project.lists:
                self.project.lists.append(name)
            ensure_list_spec(name)
        for name in variables:
            if name in lists:
                continue
            if name not in self.project.variables:
                self.project.variables.append(name)
                self.project.var_init.setdefault(name, "0")
            ensure_var_spec(name)

    # -- functions ---------------------------------------------------------- #

    def function_script(self, node: ast.FunctionDef) -> Optional[Block]:
        spec = SPECS.get("func::%s::def" % node.name)
        if spec is None:
            return None
        hat = Block(spec)
        previous, previous_name = self.params, self.func_name
        self.params = [a.arg for a in node.args.args]
        self.func_name = node.name
        for prm in self.params:
            ensure_param_spec(node.name, prm)
        body = self.body(node.body)
        self.params, self.func_name = previous, previous_name
        if body:
            link_blocks(hat, body)
        return hat

    # -- statements --------------------------------------------------------- #

    def body(self, nodes: List[ast.stmt]) -> List[Block]:
        out: List[Block] = []
        for node in nodes:
            try:
                out.extend(self.statement(node))
            except Exception:
                out.append(self.code_block(source_of(node)))
        return out

    def code_block(self, text: str) -> Block:
        self.raw += 1
        block = Block(SPECS["py_code"])
        block.values["code"] = text
        return block

    def raw_line(self, text: str) -> Block:
        self.raw += 1
        block = Block(SPECS["py_stmt"])
        block.values["code"] = text
        return block

    def statement(self, node) -> List[Block]:
        self.handled += 1
        make = self.make

        if isinstance(node, ast.Expr):
            return self.expression_statement(node.value)

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                if name in self.project.lists:
                    return [make("list_set_all", {"seq": node.value},
                                 {"list": name})]
                return [make("var_set", {"val": node.value}, {"var": name})]
            if isinstance(target, ast.Subscript):
                block = Block(SPECS["py_dict_set"])
                block.values["d"] = source_of(target.value)
                self.slot(block, "k", target.slice)
                self.slot(block, "v", node.value)
                return [block]
            block = Block(SPECS["py_assign"])
            block.values["target"] = source_of(target)
            block.values["value"] = source_of(node.value)
            return [block]

        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and \
                    isinstance(node.op, ast.Add) and \
                    node.target.id in self.project.variables:
                return [make("var_change", {"val": node.value},
                             {"var": node.target.id})]
            return [self.raw_line(source_of(node))]

        if isinstance(node, ast.If):
            test = self.condition(node.test)
            if node.orelse:
                block = Block(SPECS["control_if_else"])
                self.attach(block, "cond", test)
                self.branch(block, 0, node.body)
                self.branch(block, 1, node.orelse)
            else:
                block = Block(SPECS["control_if"])
                self.attach(block, "cond", test)
                self.branch(block, 0, node.body)
            return [block]

        if isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and node.test.value is True:
                block = Block(SPECS["control_forever"])
            else:
                block = Block(SPECS["control_while"])
                self.attach(block, "cond", self.condition(node.test))
            self.branch(block, 0, node.body)
            return [block]

        if isinstance(node, ast.For):
            return [self.for_loop(node)]

        if isinstance(node, ast.Return):
            if node.value is None:
                return [Block(SPECS["func_return_nothing"])]
            return [make("func_return", {"val": node.value})]

        if isinstance(node, ast.Break):
            return [Block(SPECS["control_break"])]
        if isinstance(node, ast.Continue):
            return [Block(SPECS["control_continue"])]
        if isinstance(node, ast.Pass):
            return [Block(SPECS["control_pass"])]

        if isinstance(node, ast.Try):
            block = Block(SPECS["control_try"])
            self.branch(block, 0, node.body)
            handler = node.handlers[0] if node.handlers else None
            block.values["err"] = (handler.name if handler and handler.name
                                   else "error")
            self.branch(block, 1, handler.body if handler else [])
            extra: List[Block] = []
            if len(node.handlers) > 1 or node.finalbody or node.orelse:
                self.notes.append("only the first 'except' of a try block "
                                  "became blocks")
            return [block] + extra

        if isinstance(node, ast.With):
            block = self.with_block(node)
            if block is not None:
                return [block]
            return [self.code_block(source_of(node))]

        if isinstance(node, ast.Raise):
            call = node.exc
            if isinstance(call, ast.Call) and call.args:
                return [make("py_raise", {"msg": call.args[0]})]
            return [self.raw_line(source_of(node))]

        if isinstance(node, ast.Assert):
            block = Block(SPECS["py_assert"])
            self.attach(block, "cond", self.condition(node.test))
            if node.msg is not None:
                self.slot(block, "msg", node.msg)
            else:
                block.values["msg"] = "something went wrong"
            return [block]

        if isinstance(node, ast.Import):
            self.remember_import(node)
            out = []
            for alias in node.names:
                block = Block(SPECS["py_import"])
                block.values["module"] = alias.name + (
                    " as " + alias.asname if alias.asname else "")
                out.append(block)
            return out

        if isinstance(node, ast.ImportFrom):
            self.remember_import(node)
            block = Block(SPECS["py_from_import"])
            block.values["module"] = node.module or "."
            block.values["names"] = ", ".join(
                a.name + (" as " + a.asname if a.asname else "")
                for a in node.names)
            return [block]

        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return []          # ScratchPy writes these itself

        self.handled -= 1
        return [self.code_block(source_of(node))]

    def expression_statement(self, value) -> List[Block]:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            block = Block(SPECS["py_comment"])
            block.values["text"] = value.value.strip().split("\n")[0][:70]
            return [block]
        if isinstance(value, ast.Call):
            name = source_of(value.func)
            if name == "print":
                if len(value.args) == 1 and not value.keywords:
                    return [self.make("text_print", {"msg": value.args[0]})]
                if len(value.args) == 2 and not value.keywords:
                    return [self.make("text_print2", {"a": value.args[0],
                                                      "b": value.args[1]})]
            if name in ("time.sleep", "sleep") and len(value.args) == 1:
                return [self.make("control_wait", {"secs": value.args[0]})]
            if name in ("sys.exit", "exit", "quit"):
                return [Block(SPECS["control_stop_all"])]
            custom = self.project.function_by_name(name)
            if custom and not custom.get("returns"):
                block = Block(SPECS["func::%s" % name])
                for i, arg in enumerate(value.args[:len(custom["params"])]):
                    self.slot(block, "p%d" % i, arg)
                return [block]
            for attr, spec_id, slot in (
                    ("append", "list_add", "item"),
                    ("insert", None, None), ("clear", "list_clear", None),
                    ("sort", "list_sort", None), ("reverse", "list_reverse", None)):
                block = self.list_method(value, attr, spec_id, slot)
                if block is not None:
                    return [block]
        block = Block(SPECS["py_do"])
        inner = self.value(value)
        if isinstance(inner, Block):
            block.attach_slot("code", inner)
        else:
            block.values["code"] = source_of(value)
        return [block]

    def list_method(self, call: ast.Call, attr: str, spec_id: Optional[str],
                    slot: Optional[str]) -> Optional[Block]:
        if spec_id is None or not isinstance(call.func, ast.Attribute):
            return None
        if call.func.attr != attr or not isinstance(call.func.value, ast.Name):
            return None
        name = call.func.value.id
        if name not in self.project.lists:
            return None
        block = Block(SPECS[spec_id])
        block.fields["list"] = name
        if slot and call.args:
            self.slot(block, slot, call.args[0])
        return block

    def for_loop(self, node: ast.For) -> Block:
        iterable = node.iter
        name = node.target.id if isinstance(node.target, ast.Name) else "item"
        if name not in self.project.variables and name not in self.project.lists:
            self.project.variables.append(name)
            self.project.var_init.setdefault(name, "0")
            ensure_var_spec(name)
        if isinstance(iterable, ast.Call) and source_of(iterable.func) == "range":
            args = iterable.args
            used = any(isinstance(n, ast.Name) and n.id == name
                       for body in (node.body, node.orelse)
                       for stmt in body for n in ast.walk(stmt))
            if len(args) == 1 and not used:
                # nobody looks at the counter, so a plain "repeat" is clearer
                block = Block(SPECS["control_repeat"])
                self.slot(block, "times", args[0])
                self.branch(block, 0, node.body)
                return block
            if len(args) == 1:
                block = Block(SPECS["control_count"])
                block.fields["var"] = name
                block.values["a"] = "0"
                stop = args[0]
                if isinstance(stop, ast.Constant) and isinstance(stop.value, int):
                    block.values["b"] = str(stop.value - 1)
                else:
                    block.values["b"] = source_of(stop) + " - 1"
                self.branch(block, 0, node.body)
                return block
            if len(args) == 2:
                block = Block(SPECS["control_count"])
                block.fields["var"] = name
                self.slot(block, "a", args[0])
                stop = args[1]
                if isinstance(stop, ast.Constant) and \
                        isinstance(stop.value, int):
                    block.values["b"] = str(stop.value - 1)
                else:
                    block.values["b"] = source_of(stop) + " - 1"
                self.branch(block, 0, node.body)
                return block
        block = Block(SPECS["control_for_each"])
        block.fields["var"] = name
        self.slot(block, "seq", iterable)
        self.branch(block, 0, node.body)
        return block

    def with_block(self, node: ast.With) -> Optional[Block]:
        if len(node.items) != 1:
            return None
        item = node.items[0]
        call = item.context_expr
        if not isinstance(call, ast.Call) or source_of(call.func) != "open":
            return None
        if not isinstance(item.optional_vars, ast.Name):
            return None
        block = Block(SPECS["file_with"])
        self.slot(block, "path", call.args[0] if call.args else None)
        block.values["var"] = item.optional_vars.id
        mode = "r"
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = str(call.args[1].value)[:1] or "r"
        block.fields["mode"] = mode if mode in ("r", "w", "a") else "r"
        self.branch(block, 0, node.body)
        return block

    # -- expressions -------------------------------------------------------- #

    def make(self, spec_id: str, slots: Dict[str, Any],
             fields: Optional[dict] = None) -> Block:
        block = Block(SPECS[spec_id])
        for key, node in slots.items():
            self.slot(block, key, node)
        for key, value in (fields or {}).items():
            block.fields[key] = value
        return block

    def slot(self, block: Block, name: str, node):
        if isinstance(node, ast.Constant) and not isinstance(node.value, bytes):
            self.literal(block, name, node.value)
            return
        self.attach(block, name, self.value(node))

    def literal(self, block: Block, name: str, raw):
        """Put a plain value in a slot, but only if it survives the trip back.

        A slot that expects a number would read the text 'hi' as the variable
        hi, so anything that would change meaning becomes a small code block
        instead.
        """
        kind = block.slot_kind(name)
        text = raw if isinstance(raw, str) else repr(raw)
        want = repr(raw)
        emitters = {"num": num_expr, "any": any_expr, "str": py_str}
        emitter = emitters.get(kind)
        if emitter is not None and same_literal(emitter(text), want):
            block.values[name] = text
        elif emitter is None:
            block.values[name] = text
        else:
            block.attach_slot(name, self.raw_expr(want))

    def attach(self, block: Block, name: str, value):
        if isinstance(value, Block):
            block.attach_slot(name, value)
        elif value is not None:
            block.values[name] = value

    def branch(self, block: Block, index: int, nodes: List[ast.stmt]):
        blocks = self.body(nodes)
        if blocks:
            block.attach_branch(index, blocks[0])
            chain_blocks(blocks)

    def raw_expr(self, text: str) -> Block:
        block = Block(SPECS["py_expr"])
        block.values["code"] = text
        return block

    def condition(self, node):
        value = self.value(node)
        if isinstance(value, Block) and value.spec.shape == "boolean":
            return value
        if isinstance(value, Block):
            return value
        return self.raw_expr(source_of(node))

    def value(self, node):
        """A slot value: either a literal string or a reporter block."""
        if node is None:
            return ""

        if isinstance(node, ast.Constant):
            raw = node.value
            if isinstance(raw, str):
                if any_expr(raw) == py_str(raw):
                    return raw
                return self.raw_expr(repr(raw))
            if raw is True:
                return Block(SPECS["op_true"])
            if raw is False:
                return Block(SPECS["op_false"])
            if raw is None:
                return Block(SPECS["py_none"])
            return repr(raw)

        if isinstance(node, ast.Name):
            if node.id in self.params:
                spec = SPECS.get("param::%s::%s" % (self.func_name, node.id))
                if spec is not None:
                    return Block(spec)
            if node.id in self.project.lists:
                return Block(ensure_list_spec(node.id))
            if node.id in self.project.variables:
                return Block(ensure_var_spec(node.id))
            return self.raw_expr(node.id)

        if isinstance(node, ast.BinOp):
            spec_id = BIN_OPS.get(type(node.op))
            if spec_id:
                return self.make(spec_id, {"a": node.left, "b": node.right})

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            symbol = CMP_OPS.get(type(node.ops[0]))
            if symbol:
                return self.make("op_compare",
                                 {"a": node.left, "b": node.comparators[0]},
                                 {"op": symbol})
            if isinstance(node.ops[0], ast.In):
                right = node.comparators[0]
                if isinstance(right, ast.Name) and right.id in self.project.lists:
                    return self.make("list_contains", {"item": node.left},
                                     {"list": right.id})
                return self.make("text_contains", {"s": right, "sub": node.left})

        if isinstance(node, ast.BoolOp):
            spec_id = "op_and" if isinstance(node.op, ast.And) else "op_or"
            block = self.make(spec_id, {"a": node.values[0],
                                        "b": node.values[1]})
            for extra in node.values[2:]:
                outer = self.make(spec_id, {})
                outer.attach_slot("a", block)
                self.slot(outer, "b", extra)
                block = outer
            return block

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.make("op_not", {"a": node.operand})

        if isinstance(node, ast.JoinedStr):
            text = source_of(node)
            if len(text) > 2 and text[0] in "fF":
                try:
                    inner = ast.literal_eval(text[1:])
                    block = Block(SPECS["text_fstring"])
                    block.values["t"] = inner
                    return block
                except Exception:
                    pass
            return self.raw_expr(text)

        if isinstance(node, ast.Call):
            block = self.call_value(node)
            if block is not None:
                return block

        if isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Name) and base.id in self.project.lists:
                index = node.slice
                if isinstance(index, ast.Constant) and \
                        isinstance(index.value, int) and index.value >= 0:
                    block = Block(SPECS["list_item"])
                    block.fields["list"] = base.id
                    block.values["i"] = str(index.value + 1)
                    return block
                if isinstance(index, ast.UnaryOp) and \
                        isinstance(index.op, ast.USub):
                    block = Block(SPECS["list_last"])
                    block.fields["list"] = base.id
                    return block

        return self.raw_expr(source_of(node))

    def call_value(self, node: ast.Call) -> Optional[Block]:
        name = source_of(node.func)
        args = node.args
        simple = {"str": "str", "int": "int", "float": "float", "bool": "bool",
                  "list": "list"}
        if name == "len" and len(args) == 1:
            target = args[0]
            if isinstance(target, ast.Name) and target.id in self.project.lists:
                return self.make("list_length", {}, {"list": target.id})
            if isinstance(target, ast.Constant) and \
                    isinstance(target.value, str):
                return self.make("text_length", {"s": target})
            # len() of something we cannot type-check stays exact
            return None
        if name == "input":
            block = Block(SPECS["text_ask"])
            if args:
                self.slot(block, "prompt", args[0])
            return block
        if name in simple and len(args) == 1:
            return self.make("op_convert", {"v": args[0]}, {"t": simple[name]})
        if name == "round":
            if len(args) == 1:
                return self.make("op_round", {"a": args[0]})
            if len(args) == 2:
                return self.make("op_round_to", {"a": args[0], "n": args[1]})
        if name == "abs" and len(args) == 1:
            return self.make("op_math", {"a": args[0]}, {"fn": "abs"})
        if name in MATH_CALLS and len(args) == 1:
            return self.make("op_math", {"a": args[0]},
                             {"fn": MATH_CALLS[name]})
        if name in ("min", "max") and len(args) == 2:
            return self.make("op_min_max", {"a": args[0], "b": args[1]},
                             {"fn": name})
        if name == "random.randint" and len(args) == 2:
            return self.make("op_random", {"a": args[0], "b": args[1]})
        if name == "random.random":
            return Block(SPECS["op_random_float"])
        if name == "random.choice" and len(args) == 1:
            return self.make("op_random_choice", {"seq": args[0]})
        if name == "range":
            if len(args) == 2:
                block = Block(SPECS["list_range"])
                self.slot(block, "a", args[0])
                stop = args[1]
                if isinstance(stop, ast.Constant) and isinstance(stop.value, int):
                    block.values["b"] = str(stop.value - 1)
                else:
                    block.values["b"] = source_of(stop) + " - 1"
                return block
        if name in ("sum", "max", "min") and len(args) == 1 and \
                isinstance(args[0], ast.Name) and args[0].id in self.project.lists:
            return self.make("list_sum", {}, {"fn": name, "list": args[0].id})
        custom = self.project.function_by_name(name)
        if custom and custom.get("returns"):
            spec = SPECS.get("func::%s" % name)
            if spec is not None:
                block = Block(spec)
                for i, arg in enumerate(args[:len(custom["params"])]):
                    self.slot(block, "p%d" % i, arg)
                return block
        return None


def chain_blocks(blocks: List[Block]):
    """Stack a list of blocks under each other."""
    for a, b in zip(blocks, blocks[1:]):
        a.next = b
        b.prev = a


def link_blocks(head: Block, blocks: List[Block]):
    chain_blocks(blocks)
    if blocks:
        head.next = blocks[0]
        blocks[0].prev = head


def import_python_source(project: "Project", source: str,
                         name: str = "imported"):
    """Convert Python text into a new tab of blocks. Returns (file, importer)."""
    importer = PythonImporter(project)
    spyfile = importer.convert(source, name)
    project.sync_specs()
    return spyfile, importer


def third_party_packages(modules: List[str]) -> List[str]:
    """pip names for the imports that are not part of Python itself."""
    out = []
    for module in modules:
        top = module.split(".")[0]
        if top in STDLIB_NAMES:
            continue
        out.append(DIST_FOR_MODULE.get(top, top))
    return dedupe(out)


# =========================================================================== #
#  SECTION 7 - the renderer: measuring, placing and drawing blocks
# =========================================================================== #

def short_code(text: str, limit: int = 46) -> str:
    """One tidy line for a slot, however much code is really in there."""
    if "\n" in text:
        lines = [l for l in text.split("\n") if l.strip()]
        first = lines[0] if lines else ""
        extra = len(lines) - 1
        text = first if extra <= 0 else "%s   (+%d more lines)" % (first, extra)
    if len(text) > limit + 14:
        text = text[:limit] + "..."
    return text


class Layout:
    """Where everything ended up after the last re-flow."""

    def __init__(self):
        self.size: Dict[str, dict] = {}
        self.rect: Dict[str, Tuple[float, float, float, float]] = {}
        self.tokens: Dict[str, list] = {}
        self.slot: Dict[Tuple[str, str], Tuple[float, float, float, float]] = {}
        self.mouth: Dict[Tuple[str, int], Tuple[float, float, float, float]] = {}

    def clear(self):
        self.size.clear()
        self.rect.clear()
        self.tokens.clear()
        self.slot.clear()
        self.mouth.clear()


class Renderer:
    """Draws blocks onto any tkinter canvas."""

    def __init__(self, project: "Project", scale: float = 1.0):
        self.project = project
        self.set_scale(scale)

    def set_scale(self, scale: float):
        self.scale = max(0.5, min(2.0, float(scale)))
        self.m = Metrics(self.scale)
        self.font = tkfont.Font(family=FONT_FAMILY, size=self.m.font_size,
                                weight="bold")
        self.slot_font = tkfont.Font(family=FONT_FAMILY, size=self.m.font_size)
        self.text_h = self.font.metrics("linespace")

    # -- helpers ------------------------------------------------------------ #

    def field_text(self, b: Block, tok) -> str:
        name, options, default = tok[1], tok[2], tok[3]
        val = b.fields.get(name, default)
        opts = self.options(tok)
        if opts and val not in opts:
            val = opts[0]
            b.fields[name] = val
        return str(val)

    def options(self, tok) -> List[str]:
        options = tok[2]
        if isinstance(options, str) and options.startswith("@"):
            return self.project.options_for(options)
        return list(options)

    def pad_for(self, spec: BlockSpec) -> float:
        return self.m.pad_x * 0.8 if spec.is_value else self.m.pad_x

    # -- measuring ---------------------------------------------------------- #

    def measure(self, L: Layout, b: Block) -> Tuple[float, float]:
        m, spec = self.m, b.spec
        pad = self.pad_for(spec)
        rows_info = []
        widest = 0.0
        for ri, row in enumerate(spec.rows):
            toks = []
            content_w = 0.0
            content_h = self.text_h
            for i, tok in enumerate(row):
                if tok[0] == "t":
                    w = self.font.measure(tok[1])
                    h = self.text_h
                elif tok[0] == "i":
                    name, kind = tok[1], tok[2]
                    v = b.values.get(name)
                    if isinstance(v, Block):
                        w, h = self.measure(L, v)
                    else:
                        text = short_code("" if v is None else str(v))
                        tw = self.slot_font.measure(text)
                        if kind == "bool":
                            w = max(m.bool_min_w, tw + 2 * m.slot_pad + m.slot_h)
                        elif kind in ("raw", "fstr"):
                            w = max(m.slot_min_w * 1.6, tw + 2 * m.slot_pad)
                        else:
                            w = max(m.slot_min_w, tw + 2 * m.slot_pad)
                        h = m.slot_h
                else:
                    text = self.field_text(b, tok)
                    w = self.slot_font.measure(text) + 2 * m.slot_pad + m.arrow_w
                    h = m.slot_h
                toks.append([tok, w, h])
                content_w += w
                content_h = max(content_h, h)
            if toks:
                content_w += m.gap * (len(toks) - 1)
            row_w = pad * 2 + content_w
            if spec.is_value:
                row_h = max(m.slot_h, content_h + 6 * m.z)
                if spec.shape == "boolean":
                    row_w += row_h * 0.6
            else:
                row_h = max(m.min_h, content_h + 2 * m.pad_y)
            if ri == 0 and spec.is_hat:
                row_h += m.hat_h
            rows_info.append({"toks": toks, "w": row_w, "h": row_h})
            widest = max(widest, row_w)

        min_w = m.slot_min_w * 1.2 if spec.is_value else m.min_w
        w = max(min_w, widest)

        mouths: List[float] = []
        if spec.is_c:
            for i in range(spec.mouths):
                mouths.append(max(m.mouth_min_h,
                                  self.measure_stack(L, b.branches[i])))
            h = sum(r["h"] for r in rows_info) + sum(mouths) + m.footer_h
        else:
            h = rows_info[0]["h"] if rows_info else m.min_h

        L.size[b.id] = {"w": w, "h": h, "rows": rows_info, "mouths": mouths}
        return w, h

    def measure_stack(self, L: Layout, first: Optional[Block]) -> float:
        total = 0.0
        b = first
        while b is not None:
            _, h = self.measure(L, b)
            total += h
            b = b.next
        return total

    # -- placing ------------------------------------------------------------ #

    def place(self, L: Layout, b: Block, x: float, y: float):
        m, spec = self.m, b.spec
        s = L.size.get(b.id)
        if s is None:
            self.measure(L, b)
            s = L.size[b.id]
        L.rect[b.id] = (x, y, s["w"], s["h"])
        pad = self.pad_for(spec)
        placed = []
        cy = y
        for ri, row in enumerate(s["rows"]):
            hat_extra = m.hat_h if (ri == 0 and spec.is_hat) else 0.0
            top = cy + hat_extra
            avail = row["h"] - hat_extra
            cx = x + pad
            if spec.shape == "boolean":
                cx += row["h"] * 0.3
            for entry in row["toks"]:
                tok, tw, th = entry
                ty = top + (avail - th) / 2.0
                if tok[0] == "i":
                    v = b.values.get(tok[1])
                    if isinstance(v, Block):
                        self.place(L, v, cx, ty)
                    else:
                        L.slot[(b.id, tok[1])] = (cx, ty, tw, th)
                elif tok[0] == "d":
                    L.slot[(b.id, "@" + tok[1])] = (cx, ty, tw, th)
                placed.append((ri, tok, cx, ty, tw, th))
                cx += tw + m.gap
            cy += row["h"]
            if spec.is_c and ri < len(s["mouths"]):
                mh = s["mouths"][ri]
                L.mouth[(b.id, ri)] = (x + m.indent, cy, s["w"] - m.indent, mh)
                child = b.branches[ri]
                if child is not None:
                    self.place_stack(L, child, x + m.indent, cy)
                cy += mh
        L.tokens[b.id] = placed

    def place_stack(self, L: Layout, first: Optional[Block], x: float, y: float):
        b = first
        while b is not None:
            self.place(L, b, x, y)
            y += L.size[b.id]["h"]
            b = b.next

    def layout_script(self, L: Layout, root: Block):
        self.measure_stack(L, root)
        self.place_stack(L, root, root.x, root.y)

    # -- drawing ------------------------------------------------------------ #

    def draw_stack(self, cv: tk.Canvas, L: Layout, first: Optional[Block],
                   root_tag: str):
        b = first
        while b is not None:
            self.draw(cv, L, b, root_tag)
            b = b.next

    def draw(self, cv: tk.Canvas, L: Layout, b: Block, root_tag: str):
        m, spec = self.m, b.spec
        x, y, w, h = L.rect[b.id]
        s = L.size[b.id]
        tags = ("block", "blk:" + b.id, root_tag)
        color, dark = spec.color(), spec.dark()

        if spec.shape == "reporter":
            cv.create_polygon(m.pill(x, y, w, h), fill=color, outline=dark,
                              width=1, tags=tags)
        elif spec.shape == "boolean":
            cv.create_polygon(m.hexagon(x, y, w, h), fill=color, outline=dark,
                              width=1, tags=tags)
        else:
            if spec.is_c:
                segs = []
                for ri, row in enumerate(s["rows"]):
                    segs.append(("bar", row["h"]))
                    segs.append(("mouth", s["mouths"][ri]))
                segs.append(("bar", m.footer_h))
            else:
                segs = [("bar", h)]
            pts = m.outline(x, y, w, segs,
                            hat=spec.is_hat,
                            top_notch=spec.has_prev,
                            bottom_bump=spec.has_next)
            cv.create_polygon(pts, fill=color, outline=dark, width=1,
                              tags=tags, joinstyle="round")

        # ---- the parts of each row ----
        for (ri, tok, tx, ty, tw, th) in L.tokens.get(b.id, []):
            cy = ty + th / 2.0
            if tok[0] == "t":
                cv.create_text(tx, cy, text=tok[1], anchor="w", fill="#FFFFFF",
                               font=self.font, tags=tags)
            elif tok[0] == "i":
                name, kind = tok[1], tok[2]
                v = b.values.get(name)
                if isinstance(v, Block):
                    self.draw(cv, L, v, root_tag)
                else:
                    stag = tags + ("slot:%s:%s" % (b.id, name),)
                    text = short_code("" if v is None else str(v))
                    if kind == "bool":
                        cv.create_polygon(m.hexagon(tx, ty, tw, th),
                                          fill=dark, outline=hexdark(dark, 0.9),
                                          width=1, tags=stag)
                        if text:
                            cv.create_text(tx + tw / 2.0, cy, text=text,
                                           anchor="center", fill="#FFFFFF",
                                           font=self.slot_font, tags=stag)
                    else:
                        fill = UI["slot"] if kind not in ("raw",) else "#F2F6FF"
                        cv.create_polygon(m.pill(tx, ty, tw, th), fill=fill,
                                          outline=dark, width=1, tags=stag)
                        cv.create_text(tx + tw / 2.0, cy, text=text,
                                       anchor="center", fill=UI["slot_text"],
                                       font=self.slot_font, tags=stag)
            else:
                name = tok[1]
                ftag = tags + ("fld:%s:%s" % (b.id, name),)
                text = self.field_text(b, tok)
                cv.create_polygon(m.pill(tx, ty, tw, th), fill=dark,
                                  outline=hexdark(dark, 0.85), width=1, tags=ftag)
                cv.create_text(tx + m.slot_pad, cy, text=text, anchor="w",
                               fill="#FFFFFF", font=self.slot_font, tags=ftag)
                ax = tx + tw - m.arrow_w * 0.75
                cv.create_polygon([ax - 4 * m.z, cy - 2 * m.z,
                                   ax + 4 * m.z, cy - 2 * m.z,
                                   ax, cy + 3 * m.z],
                                  fill="#FFFFFF", outline="", tags=ftag)

        # ---- the blocks living inside a C shaped mouth ----
        for i, child in enumerate(b.branches):
            if child is not None:
                self.draw_stack(cv, L, child, root_tag)

    # -- geometry queries used by drag and drop ----------------------------- #

    def script_bbox(self, L: Layout, root: Block) -> Tuple[float, float, float, float]:
        x0 = y0 = 1e9
        x1 = y1 = -1e9
        for d in root.descendants():
            r = L.rect.get(d.id)
            if not r:
                continue
            x0, y0 = min(x0, r[0]), min(y0, r[1])
            x1, y1 = max(x1, r[0] + r[2]), max(y1, r[1] + r[3])
        if x0 > x1:
            return (root.x, root.y, 0.0, 0.0)
        return (x0, y0, x1 - x0, y1 - y0)


# =========================================================================== #
#  SECTION 8 - the workspace: drag, drop, snap, edit
# =========================================================================== #

# How near a block has to be before it snaps. Wide and short: being off to the
# side is fine, being at the wrong height is not.
SNAP_X = 115.0        # sideways slack for stacking
SNAP_Y = 46.0         # vertical slack for stacking
SNAP_SLOT_X = 30.0    # slack around an input slot
SNAP_SLOT_Y = 24.0


class WorkspaceView(ttk.Frame):
    """The big scrolling area where scripts are built."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.file: Optional[SpyFile] = None
        self.L = Layout()
        self.drag: Optional[dict] = None
        self.editor = None
        self.editor_win = None
        self.editor_target = None
        self.panning = False

        self.canvas = tk.Canvas(self, bg=UI["canvas_bg"], highlightthickness=0,
                                width=600, height=400,
                                scrollregion=(0, 0, 3000, 2400))
        self.hbar = ttk.Scrollbar(self, orient="horizontal",
                                  command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(self, orient="vertical",
                                  command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hbar.set,
                              yscrollcommand=self.vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self.on_wheel_h)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        self.canvas.bind("<Configure>", lambda e: self.draw_background())

    # -- basics ------------------------------------------------------------- #

    @property
    def renderer(self) -> Renderer:
        return self.app.renderer

    def cxy(self, ev) -> Tuple[float, float]:
        return (self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y))

    def root_to_canvas(self, xr: float, yr: float) -> Tuple[float, float]:
        return (self.canvas.canvasx(xr - self.canvas.winfo_rootx()),
                self.canvas.canvasy(yr - self.canvas.winfo_rooty()))

    def set_file(self, spyfile: SpyFile):
        self.close_editor()
        self.file = spyfile
        self.refresh()

    # -- painting ----------------------------------------------------------- #

    def draw_background(self):
        self.canvas.delete("grid")
        try:
            x0, y0, x1, y1 = [float(v) for v in
                              self.canvas.cget("scrollregion").split()]
        except Exception:
            x0, y0, x1, y1 = 0, 0, 3000, 2400
        step = 40 * self.renderer.scale
        gx = x0
        while gx < x1:
            self.canvas.create_line(gx, y0, gx, y1, fill=UI["grid"], tags="grid")
            gx += step
        gy = y0
        while gy < y1:
            self.canvas.create_line(x0, gy, x1, gy, fill=UI["grid"], tags="grid")
            gy += step
        self.canvas.tag_lower("grid")

    def refresh(self):
        cv = self.canvas
        cv.delete("block")
        cv.delete("snaphint")
        cv.delete("watermark")
        self.L.clear()
        if self.file is None:
            return
        for script in self.file.scripts:
            self.renderer.layout_script(self.L, script)
        for script in self.file.scripts:
            self.renderer.draw_stack(cv, self.L, script, "root:" + script.id)
        self.update_scrollregion()
        self.draw_background()
        cv.create_text(14, 8, anchor="nw", text=self.file.name + ".py",
                       fill="#C7CDD8", font=(FONT_FAMILY, 22, "bold"),
                       tags="watermark")
        cv.tag_lower("watermark")
        cv.tag_lower("grid")

    def update_scrollregion(self):
        x1 = y1 = 0.0
        for script in (self.file.scripts if self.file else []):
            bx, by, bw, bh = self.renderer.script_bbox(self.L, script)
            x1 = max(x1, bx + bw)
            y1 = max(y1, by + bh)
        w = max(2400.0, x1 + 600.0)
        h = max(1800.0, y1 + 500.0)
        self.canvas.configure(scrollregion=(0, 0, w, h))

    # -- hit testing -------------------------------------------------------- #

    def topmost(self, cx: float, cy: float) -> Optional[int]:
        items = self.canvas.find_overlapping(cx - 1, cy - 1, cx + 1, cy + 1)
        for item in reversed(items):
            if "block" in self.canvas.gettags(item):
                return item
        return None

    def tag_value(self, item: int, prefix: str) -> Optional[str]:
        for t in self.canvas.gettags(item):
            if t.startswith(prefix):
                return t[len(prefix):]
        return None

    def block_by_id(self, bid: str) -> Optional[Block]:
        if self.file is None:
            return None
        for script in self.file.scripts:
            for b in script.descendants():
                if b.id == bid:
                    return b
        return None

    # -- mouse -------------------------------------------------------------- #

    def on_press(self, ev):
        self.canvas.focus_set()
        self.close_editor()
        cx, cy = self.cxy(ev)
        item = self.topmost(cx, cy)
        if item is None:
            self.panning = True
            self.canvas.scan_mark(ev.x, ev.y)
            self.canvas.configure(cursor="fleur")
            return
        fld = self.tag_value(item, "fld:")
        if fld:
            bid, _, name = fld.partition(":")
            b = self.block_by_id(bid)
            if b is not None:
                self.open_dropdown(b, name, ev)
            return
        slot = self.tag_value(item, "slot:")
        if slot:
            bid, _, name = slot.partition(":")
            b = self.block_by_id(bid)
            if b is not None:
                self.open_editor(b, name)
            return
        bid = self.tag_value(item, "blk:")
        b = self.block_by_id(bid) if bid else None
        if b is not None:
            self.begin_drag(b, cx, cy)

    def on_motion(self, ev):
        if self.panning:
            self.canvas.scan_dragto(ev.x, ev.y, gain=1)
            return
        if not self.drag:
            return
        cx, cy = self.cxy(ev)
        self.drag_to(cx, cy)

    def on_release(self, ev):
        if self.panning:
            self.panning = False
            self.canvas.configure(cursor="")
            return
        if not self.drag:
            return
        self.finish_drag(ev.x_root, ev.y_root)

    def on_wheel(self, ev):
        self.canvas.yview_scroll(int(-1 * (ev.delta / 60)), "units")

    def on_wheel_h(self, ev):
        self.canvas.xview_scroll(int(-1 * (ev.delta / 60)), "units")

    def on_double(self, ev):
        cx, cy = self.cxy(ev)
        item = self.topmost(cx, cy)
        if item is None:
            return
        bid = self.tag_value(item, "blk:")
        b = self.block_by_id(bid) if bid else None
        if b is not None and b.spec.tip:
            self.app.status(b.spec.tip)

    def on_right(self, ev):
        cx, cy = self.cxy(ev)
        item = self.topmost(cx, cy)
        menu = tk.Menu(self, tearoff=0)
        if item is not None:
            bid = self.tag_value(item, "blk:") or \
                (self.tag_value(item, "slot:") or ":").split(":")[0] or \
                (self.tag_value(item, "fld:") or ":").split(":")[0]
            b = self.block_by_id(bid) if bid else None
            if b is not None:
                menu.add_command(label="Duplicate",
                                 command=lambda: self.duplicate(b))
                menu.add_command(label="Delete block",
                                 command=lambda: self.delete_block(b, False))
                menu.add_command(label="Delete this block and everything under it",
                                 command=lambda: self.delete_block(b, True))
                if b.spec.tip:
                    menu.add_separator()
                    menu.add_command(label="Help",
                                     command=lambda: messagebox.showinfo(
                                         "About this block", b.spec.tip))
                menu.tk_popup(ev.x_root, ev.y_root)
                return
        menu.add_command(label="Clean up blocks", command=self.cleanup)
        menu.add_command(label="Add comment", command=lambda: self.app.status(
            "Use the 'note' block from the Python category for comments."))
        menu.add_separator()
        menu.add_command(label="Delete all blocks in this tab",
                         command=self.delete_all)
        menu.tk_popup(ev.x_root, ev.y_root)

    # -- dragging ----------------------------------------------------------- #

    def begin_drag(self, block: Block, cx: float, cy: float):
        self.app.push_undo()
        if block.parent is not None or block.prev is not None:
            rect = self.L.rect.get(block.id)
            block.detach()
            if rect:
                block.x, block.y = rect[0], rect[1]
            self.file.scripts.append(block)
        self.refresh()
        self.drag = {
            "block": block,
            "off": (cx - block.x, cy - block.y),
            "targets": self.collect_targets(block),
            "moved": False,
            "hint": None,
        }
        self.canvas.tag_raise("root:" + block.id)

    def start_spawn(self, spec: BlockSpec, xr: float, yr: float,
                    off: Tuple[float, float] = (20.0, 16.0)):
        """Called by the palette when a block is dragged out of it."""
        if self.file is None:
            return
        self.app.push_undo()
        b = Block(spec)
        cx, cy = self.root_to_canvas(xr, yr)
        b.x, b.y = cx - off[0], cy - off[1]
        self.file.scripts.append(b)
        self.refresh()
        self.drag = {
            "block": b,
            "off": off,
            "targets": self.collect_targets(b),
            "moved": True,
            "hint": None,
        }
        self.canvas.tag_raise("root:" + b.id)

    def drag_root(self, xr: float, yr: float):
        cx, cy = self.root_to_canvas(xr, yr)
        self.drag_to(cx, cy)

    def drag_to(self, cx: float, cy: float):
        d = self.drag
        b = d["block"]
        nx, ny = cx - d["off"][0], cy - d["off"][1]
        dx, dy = nx - b.x, ny - b.y
        if abs(dx) > 1 or abs(dy) > 1:
            d["moved"] = True
        b.x, b.y = nx, ny
        self.canvas.move("root:" + b.id, dx, dy)
        self.show_hint(self.best_target(b))

    def finish_drag(self, xr: float, yr: float):
        d = self.drag
        if d is None:
            return
        b = d["block"]
        # work out where it lands before letting go of the drag state, because
        # best_target needs it
        target = self.best_target(b)
        self.drag = None
        self.canvas.delete("snaphint")
        if self.app.over_palette(xr, yr):
            self.file.remove_script(b)
            self.refresh()
            self.app.on_change()
            self.app.status("Block deleted.")
            return
        if target:
            self.apply_target(b, target)
        self.refresh()
        self.app.on_change()

    # -- snapping ----------------------------------------------------------- #

    def collect_targets(self, dragged: Block) -> List[dict]:
        """Every place the dragged block could be dropped."""
        out: List[dict] = []
        if self.file is None:
            return out
        moving = {d.id for d in dragged.descendants()}
        spec = dragged.spec
        for script in self.file.scripts:
            if script.id == dragged.id:
                continue
            for b in script.descendants():
                if b.id in moving:
                    continue
                rect = self.L.rect.get(b.id)
                if not rect:
                    continue
                x, y, w, h = rect
                if spec.is_value:
                    continue
                if spec.has_prev and b.spec.has_next:
                    if b.spec.is_c:
                        yy = y + self.L.size[b.id]["h"]
                    else:
                        yy = y + h
                    out.append({"kind": "after", "block": b, "x": x, "y": yy})
                if spec.has_prev and b.spec.is_c:
                    for i in range(b.spec.mouths):
                        mr = self.L.mouth.get((b.id, i))
                        if mr:
                            out.append({"kind": "into", "block": b, "index": i,
                                        "x": mr[0], "y": mr[1]})
            if spec.has_next and script.spec.has_prev:
                r = self.L.rect.get(script.id)
                if r:
                    out.append({"kind": "before", "block": script,
                                "x": r[0], "y": r[1]})
        if spec.is_value:
            for script in self.file.scripts:
                if script.id == dragged.id:
                    continue
                for b in script.descendants():
                    if b.id in moving:
                        continue
                    for row in b.spec.rows:
                        for tok in row:
                            if tok[0] != "i":
                                continue
                            r = self.L.slot.get((b.id, tok[1]))
                            if r and not isinstance(b.values.get(tok[1]), Block):
                                out.append({"kind": "slot", "block": b,
                                            "name": tok[1], "x": r[0], "y": r[1],
                                            "w": r[2], "h": r[3]})
        return out

    def chain_height(self, first: Optional[Block]) -> float:
        total = 0.0
        b = first
        while b is not None:
            total += self.L.size.get(b.id, {"h": 0.0})["h"]
            b = b.next
        return total

    def best_target(self, dragged: Block) -> Optional[dict]:
        """The connection a block would make if it were dropped right now.

        Scratch is generous about this: you can be well off to the side and it
        still snaps, as long as you are at roughly the right height.  So the
        catching area is a wide, short rectangle rather than a tight circle.
        """
        if not self.drag:
            return None
        z = self.renderer.scale
        size = self.L.size.get(dragged.id, {"w": 40.0, "h": 40.0})
        tail = self.chain_height(dragged)
        best, best_score = None, None
        for t in self.drag["targets"]:
            score = None
            if t["kind"] == "slot":
                # a point just inside the left edge of the reporter
                px = dragged.x + 10.0 * z
                py = dragged.y + size["h"] / 2.0
                pad_x, pad_y = SNAP_SLOT_X * z, SNAP_SLOT_Y * z
                if (t["x"] - pad_x <= px <= t["x"] + t["w"] + pad_x and
                        t["y"] - pad_y <= py <= t["y"] + t["h"] + pad_y):
                    score = math.hypot(px - (t["x"] + t["w"] / 2.0),
                                       py - (t["y"] + t["h"] / 2.0))
            else:
                # the notch at the top of what is being dragged, except when
                # joining above a script, where its bottom edge does the work
                bx = dragged.x
                by = dragged.y + (tail if t["kind"] == "before" else 0.0)
                dx, dy = abs(t["x"] - bx), abs(t["y"] - by)
                if dx <= SNAP_X * z and dy <= SNAP_Y * z:
                    score = dy + dx * 0.35
            if score is not None and (best_score is None or score < best_score):
                best, best_score = t, score
        return best

    def show_hint(self, target: Optional[dict]):
        self.canvas.delete("snaphint")
        if not target:
            return
        if target["kind"] == "slot":
            x, y, w, h = target["x"], target["y"], target["w"], target["h"]
            self.canvas.create_rectangle(x - 2, y - 2, x + w + 2, y + h + 2,
                                         outline="#FFFFFF", width=3,
                                         tags="snaphint")
        else:
            x, y = target["x"], target["y"]
            w = 88 * self.renderer.scale
            self.canvas.create_rectangle(x + 2, y - 3, x + w, y + 3,
                                         fill="#FFFFFF", outline="#B9D6FF",
                                         tags="snaphint")

    def apply_target(self, dragged: Block, target: dict):
        kind = target["kind"]
        tb = target["block"]
        if kind == "after":
            self.file.remove_script(dragged)
            tb.attach_next(dragged)
        elif kind == "into":
            self.file.remove_script(dragged)
            tb.attach_branch(target["index"], dragged)
        elif kind == "before":
            self.file.remove_script(tb)
            dragged.x, dragged.y = tb.x, tb.y - self.chain_height(dragged)
            dragged.last().attach_next(tb)
        elif kind == "slot":
            self.file.remove_script(dragged)
            old = tb.attach_slot(target["name"], dragged)
            if old is not None:
                old.parent = None
                old.parent_slot = None
                old.x, old.y = dragged.x + 30, dragged.y + 40
                self.file.scripts.append(old)

    # -- editing ------------------------------------------------------------ #

    def open_editor(self, block: Block, name: str):
        rect = self.L.slot.get((block.id, name))
        if rect is None:
            return
        x, y, w, h = rect
        kind = block.slot_kind(name)
        value = block.values.get(name)
        text = "" if value is None else str(value)
        if kind == "raw" and ("\n" in text or block.spec.id == "py_code"):
            self.edit_code(block, name)
            return
        var = tk.StringVar(value=text)
        ent = tk.Entry(self.canvas, textvariable=var, bd=0, relief="flat",
                       justify="center" if kind != "raw" else "left",
                       font=(FONT_FAMILY, self.renderer.m.font_size),
                       bg="#FFFFFF", fg=UI["slot_text"],
                       highlightthickness=2,
                       highlightcolor=block.spec.dark(),
                       highlightbackground=block.spec.dark())
        width = max(w, 70 * self.renderer.scale)
        win = self.canvas.create_window(x, y, anchor="nw", window=ent,
                                        width=width, height=h)
        ent.focus_set()
        ent.select_range(0, "end")
        ent.icursor("end")
        self.editor, self.editor_win = ent, win
        self.editor_target = (block, name, var)
        ent.bind("<Return>", lambda e: self.close_editor(True))
        ent.bind("<KP_Enter>", lambda e: self.close_editor(True))
        ent.bind("<Escape>", lambda e: self.close_editor(False))
        ent.bind("<FocusOut>", lambda e: self.close_editor(True))
        ent.bind("<Tab>", lambda e: self.close_editor(True))

    def close_editor(self, commit: bool = True):
        if self.editor is None:
            return
        ent, win, target = self.editor, self.editor_win, self.editor_target
        self.editor = self.editor_win = self.editor_target = None
        try:
            if commit and target:
                block, name, var = target
                new = var.get()
                if str(block.values.get(name, "")) != new:
                    self.app.push_undo()
                    block.values[name] = new
                    self.app.on_change()
            ent.destroy()
            self.canvas.delete(win)
        except Exception:
            pass
        self.refresh()

    def edit_code(self, block: Block, name: str):
        """A proper multi line editor for blocks that hold real Python."""
        top = tk.Toplevel(self)
        top.title("Edit the Python inside this block")
        top.configure(bg=UI["panel"])
        top.transient(self.winfo_toplevel())
        top.grab_set()
        tk.Label(top, text="This text is written into the .py file exactly as "
                           "it appears here.", bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 9)).pack(anchor="w", padx=16, pady=(12, 4))
        text = tk.Text(top, width=76, height=18, font=(MONO_FAMILY, 10),
                       bg="#FFFFFF", fg="#3A4356", relief="flat",
                       highlightthickness=1, highlightbackground=UI["border"],
                       insertbackground="#3A4356", padx=8, pady=6, wrap="none",
                       undo=True)
        text.pack(fill="both", expand=True, padx=16)
        text.insert("1.0", str(block.values.get(name, "")))
        text.focus_set()
        note = tk.Label(top, text="", bg=UI["panel"], fg=UI["danger"],
                        font=(FONT_FAMILY, 8))
        note.pack(anchor="w", padx=16)

        def save():
            new = text.get("1.0", "end-1c").rstrip()
            problem = check_syntax(textwrap_dedent(new))
            if problem and not messagebox.askyesno(
                    "Keep it anyway?",
                    "Python is not happy with this:\n\n%s\n\nSave it anyway?"
                    % problem, parent=top):
                note.configure(text=problem)
                return
            self.app.push_undo()
            block.values[name] = new or "pass"
            top.destroy()
            self.refresh()
            self.app.on_change()

        row = tk.Frame(top, bg=UI["panel"])
        row.pack(pady=12)
        tk.Button(row, text="Cancel", command=top.destroy, relief="flat", bd=0,
                  bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 9), padx=16,
                  pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text="Save", command=save, relief="flat", bd=0,
                  bg=UI["accent"], fg="#FFFFFF", activebackground="#3373CC",
                  activeforeground="#FFFFFF", font=(FONT_FAMILY, 9, "bold"),
                  padx=24, pady=6, cursor="hand2").pack(side="left", padx=6)
        top.bind("<Escape>", lambda e: top.destroy())

    def open_dropdown(self, block: Block, name: str, ev):
        tok = None
        for row in block.spec.rows:
            for t in row:
                if t[0] == "d" and t[1] == name:
                    tok = t
        if tok is None:
            return
        options = self.renderer.options(tok)
        source = tok[2] if isinstance(tok[2], str) else ""
        menu = tk.Menu(self, tearoff=0)

        def choose(value):
            self.app.push_undo()
            block.fields[name] = value
            self.refresh()
            self.app.on_change()

        for opt in options:
            menu.add_command(label=str(opt),
                             command=lambda v=opt: choose(v))
        if source == "@msgs":
            menu.add_separator()
            menu.add_command(label="New message...",
                             command=lambda: self.new_message(block, name))
        elif source == "@vars":
            menu.add_separator()
            menu.add_command(label="Make a Variable...",
                             command=lambda: self.app.make_variable(
                                 then=lambda v: choose(v)))
        elif source == "@lists":
            menu.add_separator()
            menu.add_command(label="Make a List...",
                             command=lambda: self.app.make_list(
                                 then=lambda v: choose(v)))
        menu.tk_popup(ev.x_root, ev.y_root)

    def new_message(self, block: Block, name: str):
        value = simpledialog.askstring("New message", "Message name:",
                                       parent=self)
        if not value:
            return
        if value not in self.app.project.messages:
            self.app.project.messages.append(value)
        self.app.push_undo()
        block.fields[name] = value
        self.refresh()
        self.app.on_change()

    # -- block level commands ----------------------------------------------- #

    def duplicate(self, block: Block):
        self.app.push_undo()
        clone = block.copy()
        clone.next = None
        clone.x, clone.y = block.x + 24, block.y + 24
        r = self.L.rect.get(block.id)
        if r:
            clone.x, clone.y = r[0] + 24, r[1] + 24
        self.file.scripts.append(clone)
        self.refresh()
        self.app.on_change()

    def delete_block(self, block: Block, with_following: bool):
        self.app.push_undo()
        following = block.next
        if not with_following and following is not None:
            following.prev = None
            block.next = None
        if block.parent is not None or block.prev is not None:
            parent, slot = block.parent, block.parent_slot
            prev = block.prev
            block.detach()
            if following is not None and not with_following:
                if prev is not None:
                    prev.attach_next(following)
                elif parent is not None and isinstance(slot, int):
                    parent.attach_branch(slot, following)
                else:
                    following.x, following.y = block.x, block.y + 10
                    self.file.scripts.append(following)
        else:
            self.file.remove_script(block)
            if following is not None and not with_following:
                r = self.L.rect.get(block.id)
                following.x = r[0] if r else block.x
                following.y = (r[1] if r else block.y)
                self.file.scripts.append(following)
        self.refresh()
        self.app.on_change()

    def delete_all(self):
        if not self.file or not self.file.scripts:
            return
        if messagebox.askyesno("Delete all",
                               "Remove every block from this tab?"):
            self.app.push_undo()
            self.file.scripts.clear()
            self.refresh()
            self.app.on_change()

    def cleanup(self):
        if not self.file:
            return
        self.app.push_undo()
        y = 40.0
        for script in sorted(self.file.scripts,
                             key=lambda s: (0 if s.spec.is_hat else 1, s.y)):
            script.x = 60.0
            script.y = y
            self.renderer.layout_script(self.L, script)
            _, _, _, bh = self.renderer.script_bbox(self.L, script)
            y += bh + 34 * self.renderer.scale
        self.refresh()
        self.app.on_change()

    def add_block_center(self, spec: BlockSpec):
        """Used when a palette block is clicked instead of dragged."""
        if self.file is None:
            return
        self.app.push_undo()
        b = Block(spec)
        b.x = self.canvas.canvasx(60)
        b.y = self.canvas.canvasy(60)
        occupied = True
        while occupied:
            occupied = False
            for s in self.file.scripts:
                if abs(s.x - b.x) < 12 and abs(s.y - b.y) < 12:
                    b.x += 26
                    b.y += 26
                    occupied = True
                    break
        self.file.scripts.append(b)
        self.refresh()
        self.app.on_change()


# =========================================================================== #
#  SECTION 9 - the palette: category strip + block drawer
# =========================================================================== #

class CategoryStrip(tk.Canvas):
    """The narrow column of coloured category buttons."""

    ITEM_H = 56

    def __init__(self, master, app: "App"):
        super().__init__(master, width=76, bg=UI["panel"], highlightthickness=0)
        self.app = app
        self.selected = "events"
        self.bind("<Button-1>", self.on_click)
        self.bind("<Configure>", lambda e: self.redraw())

    def on_click(self, ev):
        idx = int(self.canvasy(ev.y) // self.ITEM_H)
        if 0 <= idx < len(CAT_ORDER):
            self.select(CAT_ORDER[idx])

    def select(self, cat: str):
        self.selected = cat
        self.redraw()
        self.app.palette.show_category(cat)

    def redraw(self):
        self.delete("all")
        for i, cat in enumerate(CAT_ORDER):
            info = CATS[cat]
            y = i * self.ITEM_H
            if cat == self.selected:
                self.create_rectangle(0, y, 76, y + self.ITEM_H,
                                      fill="#F0F2F6", outline="")
                self.create_rectangle(0, y, 3, y + self.ITEM_H,
                                      fill=info["color"], outline="")
            self.create_oval(28, y + 8, 48, y + 28, fill=info["color"],
                             outline=info["dark"], width=1)
            self.create_text(38, y + 38, text=info["name"], anchor="center",
                             fill=UI["text"], font=(FONT_FAMILY, 8))


class PaletteView(ttk.Frame):
    """The scrolling drawer of blocks you can drag out."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.category = "events"
        self.L = Layout()
        self.protos: Dict[str, Block] = {}
        self.press: Optional[dict] = None
        self.widgets: List[tk.Widget] = []

        self._ph_text = "Search blocks..."
        top = tk.Frame(self, bg=UI["palette_bg"])
        top.pack(fill="x")
        self.search_var = tk.StringVar()
        ent = tk.Entry(top, textvariable=self.search_var, bd=0, relief="flat",
                       font=(FONT_FAMILY, 9), bg="#FFFFFF", fg=UI["text"],
                       highlightthickness=1, highlightbackground=UI["border"])
        ent.pack(fill="x", padx=8, pady=6, ipady=3)
        self.search_entry = ent

        body = tk.Frame(self, bg=UI["palette_bg"])
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=UI["palette_bg"], highlightthickness=0,
                                width=300)
        self.vbar = ttk.Scrollbar(body, orient="vertical",
                                  command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(body, orient="horizontal",
                                  command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vbar.set,
                              xscrollcommand=self.hbar.set)
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Shift-MouseWheel>",
                         lambda e: self.canvas.xview_scroll(
                             int(-1 * (e.delta / 60)), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))

        self._placeholder(ent, self._ph_text)
        self.search_var.trace_add("write", lambda *a: self.rebuild())

    def _placeholder(self, entry: tk.Entry, text: str):
        entry.insert(0, text)
        entry.configure(fg="#AAB0BC")
        self._ph_text = text

        def focus_in(_):
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(fg=UI["text"])

        def focus_out(_):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(fg="#AAB0BC")

        entry.bind("<FocusIn>", focus_in)
        entry.bind("<FocusOut>", focus_out)

    # -- content ------------------------------------------------------------ #

    @property
    def renderer(self) -> Renderer:
        return self.app.renderer

    def show_category(self, cat: str):
        self.category = cat
        self.canvas.yview_moveto(0.0)
        self.rebuild()

    def query(self) -> str:
        q = self.search_var.get().strip()
        if q == getattr(self, "_ph_text", ""):
            return ""
        return q.lower()

    def spec_ids(self) -> List[Tuple[str, Any]]:
        """The list of things to show: ('block', id) / ('header', text) /
        ('button', label, command)."""
        p = self.app.project
        q = self.query()
        items: List[Tuple[str, Any]] = []
        if q:
            for cat in CAT_ORDER:
                found = [bid for bid in PALETTE.get(cat, [])
                         if q in SPECS[bid].label().lower() or q in bid.lower()]
                extra = []
                if cat == "variables":
                    extra = [var_spec_id(v) for v in p.variables
                             if q in v.lower()]
                elif cat == "lists":
                    extra = [list_spec_id(v) for v in p.lists if q in v.lower()]
                elif cat == "functions":
                    extra = [i for i in SPECS
                             if i.startswith("func::") and q in i.lower()]
                elif cat == "packages":
                    extra = [i for i in PALETTE.get("packages", [])]
                    extra = [i for i in extra if q in SPECS[i].label().lower()]
                    found = []
                for bid in dedupe(found + extra):
                    if bid in SPECS:
                        items.append(("block", bid))
            return items

        cat = self.category
        if cat == "variables":
            items.append(("button", ("Make a Variable", self.app.make_variable)))
            for v in p.variables:
                items.append(("block", var_spec_id(v)))
            for bid in PALETTE["variables"]:
                items.append(("block", bid))
        elif cat == "lists":
            items.append(("button", ("Make a List", self.app.make_list)))
            for v in p.lists:
                items.append(("block", list_spec_id(v)))
            for bid in PALETTE["lists"]:
                items.append(("block", bid))
        elif cat == "functions":
            items.append(("button", ("Make a Block", self.app.make_function)))
            for fn in p.functions:
                items.append(("header", fn["name"]))
                items.append(("block", "func::%s::def" % fn["name"]))
                items.append(("block", "func::%s" % fn["name"]))
                for prm in fn.get("params", []):
                    if prm.strip():
                        items.append(("block", "param::%s::%s" % (fn["name"], prm)))
            if p.functions:
                items.append(("header", "returning a value"))
            for bid in PALETTE["functions"]:
                items.append(("block", bid))
        elif cat == "packages":
            items.append(("button", ("Manage packages",
                                     self.app.open_packages)))
            if not p.packs:
                items.append(("note", "Install a package to get new blocks."))
            for pack in p.packs:
                items.append(("header", "%s %s" % (pack.get("module", "?"),
                                                   pack.get("version", "")),
                              pack.get("dark", "")))
                for spec in pack.get("blocks", []):
                    if spec["id"] in SPECS:
                        items.append(("block", spec["id"]))
        else:
            for bid in PALETTE.get(cat, []):
                items.append(("block", bid))
        return items

    def proto(self, bid: str) -> Optional[Block]:
        spec = SPECS.get(bid)
        if spec is None:
            return None
        b = self.protos.get(bid)
        if b is None or b.spec is not spec:
            b = Block(spec)
            self.protos[bid] = b
        return b

    def rebuild(self):
        cv = self.canvas
        cv.delete("all")
        for w in self.widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self.widgets = []
        self.L.clear()
        y = 14.0
        widest = 260.0
        z = self.renderer.scale
        for item in self.spec_ids():
            kind = item[0]
            if kind == "header":
                tint = item[2] if len(item) > 2 and item[2] else UI["text"]
                y += 6 * z
                cv.create_oval(14, y + 4, 24, y + 14, fill=tint, outline="")
                cv.create_text(30, y, anchor="nw", text=str(item[1]),
                               fill=tint, font=(FONT_FAMILY, 9, "bold"))
                y += 26 * z
            elif kind == "note":
                cv.create_text(14, y, anchor="nw", text=str(item[1]),
                               width=220, fill="#8A93A5",
                               font=(FONT_FAMILY, 9))
                y += 34 * z
            elif kind == "button":
                label, cmd = item[1]
                btn = tk.Button(cv, text=label, command=cmd, relief="flat",
                                bg=CATS[self.category]["color"], fg="#FFFFFF",
                                activebackground=CATS[self.category]["dark"],
                                activeforeground="#FFFFFF", bd=0,
                                font=(FONT_FAMILY, 9, "bold"), cursor="hand2",
                                padx=10, pady=4)
                cv.create_window(14, y, anchor="nw", window=btn)
                self.widgets.append(btn)
                y += 44 * z
            else:
                b = self.proto(item[1])
                if b is None:
                    continue
                b.x, b.y = 16.0, y
                self.renderer.layout_script(self.L, b)
                self.renderer.draw_stack(cv, self.L, b, "pal:" + b.id)
                bx, _, bw, bh = self.renderer.script_bbox(self.L, b)
                widest = max(widest, bx + bw)
                y += bh + 14 * z
        cv.configure(scrollregion=(0, 0, widest + 20, max(400.0, y + 40)))

    # -- mouse -------------------------------------------------------------- #

    def on_wheel(self, ev):
        self.canvas.yview_scroll(int(-1 * (ev.delta / 60)), "units")

    def topmost_block(self, cx, cy) -> Optional[Block]:
        items = self.canvas.find_overlapping(cx - 1, cy - 1, cx + 1, cy + 1)
        for item in reversed(items):
            for t in self.canvas.gettags(item):
                if t.startswith("blk:"):
                    bid = t[4:]
                    for b in self.protos.values():
                        if b.id == bid:
                            return b
        return None

    def on_press(self, ev):
        cx = self.canvas.canvasx(ev.x)
        cy = self.canvas.canvasy(ev.y)
        b = self.topmost_block(cx, cy)
        if b is None:
            self.press = None
            return
        self.press = {"spec": b.spec,
                      "off": (cx - b.x, cy - b.y),
                      "start": (ev.x_root, ev.y_root),
                      "spawned": False}

    def on_motion(self, ev):
        if not self.press:
            return
        ws = self.app.workspace
        if self.press["spawned"]:
            ws.drag_root(ev.x_root, ev.y_root)
            return
        sx, sy = self.press["start"]
        if abs(ev.x_root - sx) < 4 and abs(ev.y_root - sy) < 4:
            return
        if ev.x_root >= ws.canvas.winfo_rootx() - 4:
            ws.start_spawn(self.press["spec"], ev.x_root, ev.y_root,
                           self.press["off"])
            self.press["spawned"] = True

    def on_release(self, ev):
        if not self.press:
            return
        press, self.press = self.press, None
        ws = self.app.workspace
        if press["spawned"]:
            ws.finish_drag(ev.x_root, ev.y_root)
        else:
            sx, sy = press["start"]
            if abs(ev.x_root - sx) < 6 and abs(ev.y_root - sy) < 6:
                ws.add_block_center(press["spec"])


# =========================================================================== #
#  SECTION 10 - pip packages become blocks
# =========================================================================== #

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # A bundled app lives next to its .exe / .app, not in the unpacking folder.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    if sys.platform == "darwin" and ".app/Contents/" in APP_DIR.replace("\\", "/"):
        APP_DIR = APP_DIR.split(".app/Contents/")[0]
        APP_DIR = os.path.dirname(APP_DIR)
else:
    try:
        APP_DIR = os.path.dirname(os.path.abspath(__file__))
    except NameError:                                # pragma: no cover
        APP_DIR = os.getcwd()

DEFAULT_FOLDER = APP_DIR
PACK_DIR = os.path.join(APP_DIR, "scratchpy_blocks")
ASSET_DIR = os.path.join(APP_DIR, "scratchpy_assets")


def find_python() -> str:
    """The interpreter used to run your programs and to drive pip.

    When ScratchPy is a bundled application, sys.executable is ScratchPy
    itself, so a real Python has to be found on the system instead.
    """
    if not FROZEN:
        return sys.executable
    import shutil
    for candidate in ("python3", "python", "py"):
        found = shutil.which(candidate)
        if found:
            return found
    return ""


PYTHON_EXE = find_python()

# A bundled app carries its own Python, so programs still run even when the
# computer has no Python installed - only pip needs a real installation.
CAN_RUN = bool(PYTHON_EXE) or FROZEN
NO_PYTHON_HINT = ("No Python interpreter was found on this computer. "
                  "ScratchPy can still build, save and run your code, but to "
                  "install packages with pip you need Python from python.org.")


def run_command(path: str, interpreter: str = "") -> List[str]:
    """How to start one of the generated files."""
    interpreter = interpreter or PYTHON_EXE
    if interpreter:
        return [interpreter, "-u", path]
    if FROZEN:
        return [sys.executable, "--exec", path]
    return []


# --------------------------------------------------------------------------- #
#  Settings - a small json file that lives next to the application
# --------------------------------------------------------------------------- #

SETTINGS_PATH = os.path.join(APP_DIR, "scratchpy_settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "use_venv": False,
    "venv_dir": ".venv",
    "autosave": True,
    "confirm_uninstall": True,
    "zoom": 1.0,
}


class Settings:
    """Remembers your preferences between sessions."""

    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self.data: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if key in DEFAULT_SETTINGS:
                        self.data[key] = value
        except Exception:
            pass

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1)
        except Exception:
            pass

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.data.get(key, DEFAULT_SETTINGS.get(key, fallback))

    def set(self, key: str, value: Any):
        self.data[key] = value
        self.save()


def venv_python_path(folder: str) -> str:
    """The interpreter inside a virtual environment folder."""
    if IS_WINDOWS:
        return os.path.join(folder, "Scripts", "python.exe")
    return os.path.join(folder, "bin", "python")


def venv_folder_for(settings: Settings, project_folder: str) -> str:
    where = str(settings.get("venv_dir") or ".venv")
    if os.path.isabs(where):
        return where
    return os.path.join(project_folder, where)

NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def popen_kwargs() -> dict:
    kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
              errors="replace", bufsize=1)
    if IS_WINDOWS:
        kw["creationflags"] = NO_WINDOW
    return kw


# --------------------------------------------------------------------------- #
#  A child process does the importing so a broken package cannot take the
#  editor down with it.
# --------------------------------------------------------------------------- #

INTROSPECT_SRC = r'''
import importlib, inspect, json, sys

name = sys.argv[1]
out = {"module": name, "version": "", "items": [], "error": ""}
try:
    mod = importlib.import_module(name)
except Exception as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
    print(json.dumps(out))
    raise SystemExit(0)

out["version"] = str(getattr(mod, "__version__", "") or "")
names = getattr(mod, "__all__", None) or dir(mod)
seen = set()
for attr in names:
    if not isinstance(attr, str) or attr.startswith("_") or attr in seen:
        continue
    if not attr.isidentifier():
        continue
    seen.add(attr)
    try:
        obj = getattr(mod, attr)
    except Exception:
        continue
    entry = {"name": attr, "kind": "", "params": [], "doc": ""}
    try:
        entry["doc"] = (inspect.getdoc(obj) or "").strip().split("\n")[0][:110]
    except Exception:
        entry["doc"] = ""
    if inspect.isclass(obj):
        entry["kind"] = "class"
    elif inspect.isroutine(obj):
        entry["kind"] = "func"
    elif isinstance(obj, (int, float, str, bool)) and not isinstance(obj, bool):
        entry["kind"] = "const"
    elif isinstance(obj, bool):
        entry["kind"] = "const"
    elif inspect.ismodule(obj):
        continue
    else:
        continue
    if entry["kind"] in ("func", "class"):
        try:
            sig = inspect.signature(obj)
        except Exception:
            sig = None
        if sig is not None:
            for prm in sig.parameters.values():
                if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
                    continue
                if prm.name in ("self", "cls"):
                    continue
                item = {"name": prm.name, "default": None, "required": True}
                if prm.default is not inspect.Parameter.empty:
                    item["required"] = False
                    try:
                        item["default"] = repr(prm.default)
                    except Exception:
                        item["default"] = None
                entry["params"].append(item)
        else:
            entry["params"] = None
    out["items"].append(entry)
print(json.dumps(out))
'''

MODULES_SRC = r'''
import json, sys
try:
    from importlib.metadata import packages_distributions
except Exception:
    packages_distributions = None
dist = sys.argv[1].lower().replace("_", "-")
mods = []
if packages_distributions:
    try:
        for mod, dists in packages_distributions().items():
            for d in dists:
                if d.lower().replace("_", "-") == dist:
                    mods.append(mod)
    except Exception:
        pass
print(json.dumps(sorted(set(m for m in mods if not m.startswith("_")))))
'''

SAFE_DEFAULT = re.compile(r"^[^()%|,]{0,24}$")


def clean_default(text: Optional[str]) -> Optional[str]:
    """Only keep a parameter default if it survives our mark-up parser."""
    if text is None:
        return None
    if not SAFE_DEFAULT.match(text):
        return None
    return text


def blocks_from_introspection(data: dict, limit: int = 70) -> List[dict]:
    """Turn the description of a module into a list of block definitions."""
    module = data.get("module", "")
    blocks: List[dict] = []
    items = data.get("items", [])
    order = {"func": 0, "class": 1, "const": 2}
    items = sorted(items, key=lambda e: (order.get(e.get("kind"), 3),
                                         e.get("name", "")))
    for entry in items[:limit]:
        name = entry.get("name")
        kind = entry.get("kind")
        qual = "%s.%s" % (module, name)
        bid = "pkg::%s::%s" % (module, name)
        if kind == "const":
            blocks.append({"id": bid, "shape": "reporter", "rows": [qual],
                           "code": qual, "imports": [module],
                           "tip": entry.get("doc", "")})
            continue
        params = entry.get("params")
        row = qual if kind == "func" else "new " + qual
        args: List[str] = []
        if params is None:
            row += " with %r(args,)"
            args.append("{args}")
        else:
            used = 0
            for prm in params:
                if used >= 4:
                    break
                pname = prm.get("name", "")
                if not pname.isidentifier():
                    continue
                slot = "p%d" % used
                if prm.get("required"):
                    row += " %s %%a(%s,)" % (pname, slot)
                    args.append("{%s}" % slot)
                else:
                    dft = clean_default(prm.get("default"))
                    if dft is None:
                        continue
                    row += " %s %%a(%s,%s)" % (pname, slot, dft)
                    args.append("%s={%s}" % (pname, slot))
                used += 1
        blocks.append({"id": bid, "shape": "reporter", "rows": [row],
                       "code": "%s(%s)" % (qual, ", ".join(args)),
                       "imports": [module], "tip": entry.get("doc", "")})
    return blocks


#  Every package gets its own colour so its blocks stand out in the palette.
PACK_COLORS: List[Tuple[str, str]] = [
    ("#0FBD8C", "#0B8E69"),   # teal
    ("#FF6680", "#FF3355"),   # pink
    ("#4C97FF", "#3373CC"),   # blue
    ("#FFAB19", "#CF8B17"),   # amber
    ("#9966FF", "#774DCB"),   # violet
    ("#59C059", "#389438"),   # green
    ("#CF63CF", "#A63FA6"),   # magenta
    ("#5CB1D6", "#2E8EB8"),   # sky
    ("#FF8C1A", "#DB6E00"),   # orange
    ("#00B4A0", "#008C7D"),   # jade
    ("#E4572E", "#B33F1F"),   # rust
    ("#7C83FD", "#5A61E0"),   # periwinkle
    ("#B58B00", "#8A6900"),   # gold
    ("#3AA6A6", "#2A8080"),   # lagoon
]


def pick_pack_color(module: str, taken: List[str]) -> Tuple[str, str]:
    """A stable colour per package that avoids clashing with the others."""
    start = sum(ord(ch) for ch in module) % len(PACK_COLORS)
    for step in range(len(PACK_COLORS)):
        pair = PACK_COLORS[(start + step) % len(PACK_COLORS)]
        if pair[0] not in taken:
            return pair
    return PACK_COLORS[start]


def register_pack(pack: dict):
    """Make every block of a package pack available to the palette."""
    meta = {"pack": pack.get("module", ""),
            "color": pack.get("color", ""),
            "dark": pack.get("dark", "")}
    for bdef in pack.get("blocks", []):
        bid = bdef.get("id")
        if not bid:
            continue
        unregister(bid)
        B(bid, "packages", bdef.get("shape", "reporter"),
          bdef.get("rows", [bid]), bdef.get("code", ""),
          imports=tuple(bdef.get("imports", ())),
          maps=bdef.get("maps"),
          tip=bdef.get("tip", ""), listed=True, dynamic=True,
          meta=dict(meta))


def unregister_pack(pack: dict):
    for bdef in pack.get("blocks", []):
        unregister(bdef.get("id", ""))


# --------------------------------------------------------------------------- #
#  Hand written blocks for the most loved libraries - these are used instead of
#  (well, as well as) the automatically generated ones.
# --------------------------------------------------------------------------- #

def _blk(bid, shape, rows, code, imports, tip=""):
    return {"id": bid, "shape": shape, "rows": rows if isinstance(rows, list)
            else [rows], "code": code, "imports": list(imports), "tip": tip}


CURATED: Dict[str, List[dict]] = {
    "requests": [
        _blk("pkg::requests::get_text", "reporter",
             "web page at %s(url,https://example.com)",
             "requests.get({url}, timeout=30).text", ["requests"],
             "Download a page and give back its text."),
        _blk("pkg::requests::get_json", "reporter",
             "JSON from %s(url,https://api.github.com)",
             "requests.get({url}, timeout=30).json()", ["requests"],
             "Download a web API answer as a record."),
        _blk("pkg::requests::status", "reporter",
             "status code of %s(url,https://example.com)",
             "requests.get({url}, timeout=30).status_code", ["requests"]),
        _blk("pkg::requests::post", "reporter",
             "post %a(data) to %s(url,https://example.com)",
             "requests.post({url}, json={data}, timeout=30).text", ["requests"]),
        _blk("pkg::requests::download", "stack",
             "download %s(url,https://example.com/a.png) to %s(path,file.png)",
             'open({path}, "wb").write(requests.get({url}, timeout=60).content)',
             ["requests"]),
    ],
    "numpy": [
        _blk("pkg::numpy::array", "reporter", "numpy array from %a(v)",
             "numpy.array({v})", ["numpy"]),
        _blk("pkg::numpy::zeros", "reporter", "numpy zeros %n(n,10)",
             "numpy.zeros(int({n}))", ["numpy"]),
        _blk("pkg::numpy::arange", "reporter",
             "numpy numbers %n(a,0) to %n(b,10) step %n(s,1)",
             "numpy.arange({a}, {b}, {s})", ["numpy"]),
        _blk("pkg::numpy::linspace", "reporter",
             "numpy %n(n,50) points from %n(a,0) to %n(b,1)",
             "numpy.linspace({a}, {b}, int({n}))", ["numpy"]),
        _blk("pkg::numpy::stat", "reporter",
             "numpy %d(fn,mean|median|std|sum|min|max) of %a(v)",
             "numpy.{fn}({v})", ["numpy"]),
        _blk("pkg::numpy::random", "reporter", "numpy random %n(n,5) numbers",
             "numpy.random.rand(int({n}))", ["numpy"]),
    ],
    "pandas": [
        _blk("pkg::pandas::read_csv", "reporter",
             "pandas table from %s(path,data.csv)",
             "pandas.read_csv({path})", ["pandas"]),
        _blk("pkg::pandas::from_rows", "reporter",
             "pandas table from %a(rows)", "pandas.DataFrame({rows})",
             ["pandas"]),
        _blk("pkg::pandas::head", "reporter",
             "first %n(n,5) rows of %r(df,table)", "{df}.head(int({n}))",
             ["pandas"]),
        _blk("pkg::pandas::column", "reporter",
             "column %s(col,name) of %r(df,table)", "{df}[{col}]", ["pandas"]),
        _blk("pkg::pandas::describe", "reporter", "summary of %r(df,table)",
             "{df}.describe()", ["pandas"]),
        _blk("pkg::pandas::to_csv", "stack",
             "save table %r(df,table) to %s(path,out.csv)",
             "{df}.to_csv({path}, index=False)", ["pandas"]),
    ],
    "matplotlib": [
        _blk("pkg::matplotlib::plot", "stack", "plot line %a(y) ",
             "plt.plot({y})", ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::plotxy", "stack", "plot %a(x) against %a(y)",
             "plt.plot({x}, {y})", ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::bar", "stack", "bar chart %a(x) with %a(y)",
             "plt.bar({x}, {y})", ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::scatter", "stack", "scatter %a(x) against %a(y)",
             "plt.scatter({x}, {y})", ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::title", "stack", "chart title %s(t,My chart)",
             "plt.title({t})", ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::show", "stack", "show the chart", "plt.show()",
             ["import matplotlib.pyplot as plt"]),
        _blk("pkg::matplotlib::save", "stack", "save chart to %s(path,chart.png)",
             "plt.savefig({path})", ["import matplotlib.pyplot as plt"]),
    ],
    "PIL": [
        _blk("pkg::PIL::open", "reporter", "open image %s(path,photo.png)",
             "Image.open({path})", ["from PIL import Image"]),
        _blk("pkg::PIL::new", "reporter",
             "new image %n(w,200) by %n(h,200) coloured %s(c,white)",
             'Image.new("RGB", (int({w}), int({h})), {c})',
             ["from PIL import Image"]),
        _blk("pkg::PIL::resize", "reporter",
             "%r(img,image) resized to %n(w,100) by %n(h,100)",
             "{img}.resize((int({w}), int({h})))", ["from PIL import Image"]),
        _blk("pkg::PIL::rotate", "reporter", "%r(img,image) turned %n(deg,90)",
             "{img}.rotate({deg})", ["from PIL import Image"]),
        _blk("pkg::PIL::save", "stack", "save %r(img,image) to %s(path,out.png)",
             "{img}.save({path})", ["from PIL import Image"]),
        _blk("pkg::PIL::show", "stack", "show %r(img,image)", "{img}.show()",
             ["from PIL import Image"]),
    ],
    "pygame": [
        _blk("pkg::pygame::init", "stack",
             "start a game window %n(w,640) by %n(h,480)",
             "pygame.init()\nscreen = pygame.display.set_mode((int({w}), int({h})))\nclock = pygame.time.Clock()",
             ["pygame"]),
        _blk("pkg::pygame::fill", "stack", "fill the screen with %s(c,black)",
             "screen.fill(pygame.Color({c}))", ["pygame"]),
        _blk("pkg::pygame::rect", "stack",
             "draw %s(c,red) box at %n(x,100) %n(y,100) size %n(w,50) %n(h,50)",
             "pygame.draw.rect(screen, pygame.Color({c}), (int({x}), int({y}), int({w}), int({h})))",
             ["pygame"]),
        _blk("pkg::pygame::circle", "stack",
             "draw %s(c,blue) circle at %n(x,100) %n(y,100) radius %n(r,20)",
             "pygame.draw.circle(screen, pygame.Color({c}), (int({x}), int({y})), int({r}))",
             ["pygame"]),
        _blk("pkg::pygame::flip", "stack", "update the game window",
             "pygame.display.flip()\nclock.tick(60)\nfor _event in pygame.event.get():\n    if _event.type == pygame.QUIT:\n        pygame.quit()\n        raise SystemExit",
             ["pygame"]),
        _blk("pkg::pygame::key", "boolean",
             "key %d(k,left|right|up|down|space) pressed",
             "pygame.key.get_pressed()[pygame.K_{k}]", ["pygame"]),
    ],
    "turtle": [
        _blk("pkg::turtle::forward", "stack", "move turtle %n(n,100) steps",
             "turtle.forward({n})", ["turtle"]),
        _blk("pkg::turtle::right", "stack", "turn turtle right %n(n,90) degrees",
             "turtle.right({n})", ["turtle"]),
        _blk("pkg::turtle::left", "stack", "turn turtle left %n(n,90) degrees",
             "turtle.left({n})", ["turtle"]),
        _blk("pkg::turtle::color", "stack", "set pen colour to %s(c,red)",
             "turtle.pencolor({c})", ["turtle"]),
        _blk("pkg::turtle::pen", "stack", "pen %d(a,up|down)", "turtle.pen{a}()",
             ["turtle"]),
        _blk("pkg::turtle::done", "stack", "finish drawing", "turtle.done()",
             ["turtle"]),
    ],
}

# pip name -> the name you actually import
IMPORT_ALIASES = {
    "pillow": "PIL", "beautifulsoup4": "bs4", "opencv-python": "cv2",
    "scikit-learn": "sklearn", "pyyaml": "yaml", "python-dateutil": "dateutil",
    "attrs": "attr", "msgpack-python": "msgpack", "pycryptodome": "Crypto",
    "protobuf": "google.protobuf", "python-docx": "docx",
    "python-pptx": "pptx", "discord.py": "discord", "matplotlib": "matplotlib",
}

POPULAR = ["requests", "numpy", "pandas", "matplotlib", "pillow", "pygame",
           "rich", "flask", "beautifulsoup4", "openpyxl", "tqdm", "pyfiglet",
           "colorama", "scipy", "sympy", "emoji", "qrcode", "pyttsx3"]

STDLIB_MODULES = ["turtle", "math", "random", "statistics", "datetime", "json",
                  "os", "sys", "re", "csv", "time", "collections", "itertools",
                  "hashlib", "urllib.request", "sqlite3", "tkinter", "socket",
                  "webbrowser", "zipfile", "shutil", "glob", "uuid", "base64"]


class PackageManager:
    """Runs pip and turns modules into blocks. All work happens off the UI thread."""

    def __init__(self, app: "App"):
        self.app = app
        os.makedirs(PACK_DIR, exist_ok=True)

    # -- process plumbing --------------------------------------------------- #

    def run(self, args: List[str], log: Callable[[str], None],
            done: Callable[[int], None]):
        if not args or not args[0]:
            log(NO_PYTHON_HINT)
            done(-1)
            return

        def worker():
            code = -1
            try:
                proc = subprocess.Popen(args, **popen_kwargs())
                for line in proc.stdout:
                    log(line.rstrip("\n"))
                proc.wait()
                code = proc.returncode
            except Exception as exc:
                log("! %s" % exc)
            self.app.ui(lambda: done(code))
        threading.Thread(target=worker, daemon=True).start()

    def capture(self, args: List[str], done: Callable[[str], None]):
        if not args or not args[0]:
            self.app.ui(lambda: self.app.status(NO_PYTHON_HINT))
            done("")
            return

        def worker():
            text = ""
            try:
                kw = popen_kwargs()
                kw["stderr"] = subprocess.PIPE
                proc = subprocess.Popen(args, **kw)
                text, _err = proc.communicate(timeout=180)
            except Exception as exc:
                text = ""
                self.app.ui(lambda: self.app.status("Command failed: %s" % exc))
            self.app.ui(lambda: done(text or ""))
        threading.Thread(target=worker, daemon=True).start()

    # -- pip ---------------------------------------------------------------- #

    def python(self) -> str:
        """The interpreter pip and introspection should use right now."""
        try:
            return self.app.interpreter()
        except Exception:
            return PYTHON_EXE

    def pip(self, *args) -> List[str]:
        return [self.python(), "-m", "pip"] + list(args)

    def make_venv(self, folder: str, log, done):
        """Create a fresh virtual environment (needs a real Python)."""
        if not PYTHON_EXE:
            log("A virtual environment needs Python installed from python.org.")
            done(-1)
            return
        log("$ python -m venv " + folder)
        self.run([PYTHON_EXE, "-m", "venv", folder], log, done)

    def list_installed(self, done: Callable[[List[dict]], None]):
        def parse(text: str):
            try:
                start = text.index("[")
                data = json.loads(text[start:])
            except Exception:
                data = []
            done(data)
        self.capture(self.pip("list", "--format=json",
                              "--disable-pip-version-check"), parse)

    def install(self, spec: str, log, done):
        self.run(self.pip("install", "--disable-pip-version-check",
                          "--no-input", spec), log, done)

    def uninstall(self, name: str, log, done):
        self.run(self.pip("uninstall", "-y", "--disable-pip-version-check",
                          name), log, done)

    # -- module -> blocks --------------------------------------------------- #

    def modules_of(self, dist: str, done: Callable[[List[str]], None]):
        def parse(text: str):
            mods: List[str] = []
            try:
                mods = json.loads(text.strip().splitlines()[-1])
            except Exception:
                mods = []
            if not mods:
                guess = IMPORT_ALIASES.get(dist.lower())
                mods = [guess or dist.lower().replace("-", "_")]
            done(mods)
        self.capture([self.python(), "-c", MODULES_SRC, dist], parse)

    def introspect(self, module: str, done: Callable[[dict], None]):
        def parse(text: str):
            data = {}
            for line in reversed(text.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        break
                    except Exception:
                        continue
            if not data:
                data = {"module": module, "items": [],
                        "error": "could not import %s" % module}
            done(data)
        self.capture([self.python(), "-c", INTROSPECT_SRC, module], parse)

    def build_pack(self, module: str, data: dict, dist: str = "") -> dict:
        curated = CURATED.get(module.split(".")[0], [])
        auto = blocks_from_introspection(data)
        seen = set()
        blocks = []
        for b in list(curated) + auto:
            if b["id"] in seen:
                continue
            seen.add(b["id"])
            blocks.append(b)
        taken = [p.get("color", "") for p in self.app.project.packs
                 if p.get("module") != module]
        color, dark = pick_pack_color(module, taken)
        return {"module": module, "dist": dist or module,
                "version": data.get("version", ""), "blocks": blocks,
                "color": color, "dark": dark}

    def save_pack(self, pack: dict):
        try:
            os.makedirs(PACK_DIR, exist_ok=True)
            with open(pack_cache_path(pack.get("module", "pack")), "w",
                      encoding="utf-8") as fh:
                json.dump(pack, fh, indent=1)
        except Exception:
            pass


def pack_cache_path(module: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_.-]", "_", module or "pack")
    return os.path.join(PACK_DIR, name + ".json")


def load_cached_packs() -> List[dict]:
    """Packs built in an earlier session, so your blocks are still there."""
    out: List[dict] = []
    try:
        for name in sorted(os.listdir(PACK_DIR)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(PACK_DIR, name), "r",
                          encoding="utf-8") as fh:
                    pack = json.load(fh)
                if pack.get("blocks") and pack.get("module"):
                    out.append(pack)
            except Exception:
                continue
    except Exception:
        pass
    return out


def forget_cached_pack(module: str):
    try:
        os.remove(pack_cache_path(module))
    except Exception:
        pass


# =========================================================================== #
#  SECTION 11 - running the generated program
# =========================================================================== #

class Runner:
    """Starts the generated file and pumps its output into the console."""

    def __init__(self, app: "App"):
        self.app = app
        self.proc: Optional[subprocess.Popen] = None
        self.q: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.reading = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, path: str, cwd: str):
        if self.running:
            self.stop()
        kw = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  stdin=subprocess.PIPE, text=True, encoding="utf-8",
                  errors="replace", bufsize=1, cwd=cwd)
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        kw["env"] = env
        command = run_command(path, self.app.interpreter())
        if not command:
            self.q.put(("err", NO_PYTHON_HINT))
            return
        try:
            self.proc = subprocess.Popen(command, **kw)
        except Exception as exc:
            self.q.put(("err", "Could not start Python: %s" % exc))
            return
        threading.Thread(target=self._read, args=(self.proc.stdout, "out"),
                         daemon=True).start()
        threading.Thread(target=self._read, args=(self.proc.stderr, "err"),
                         daemon=True).start()
        threading.Thread(target=self._wait, daemon=True).start()

    def _read(self, pipe, tag):
        try:
            for line in iter(pipe.readline, ""):
                self.q.put((tag, line.rstrip("\n")))
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _wait(self):
        proc = self.proc
        if proc is None:
            return
        code = proc.wait()
        self.q.put(("sys", "--- finished with exit code %d ---" % code))
        self.app.ui(self.app.on_run_finished)

    def send(self, text: str):
        if self.running and self.proc.stdin:
            try:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
                self.q.put(("in", text))
            except Exception as exc:
                self.q.put(("err", "Could not send input: %s" % exc))

    def stop(self):
        if not self.running:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID",
                                str(self.proc.pid)],
                               capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                self.proc.terminate()
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.q.put(("sys", "--- stopped ---"))


class ConsolePane(ttk.Frame):
    """Output, errors and a place to type answers to input()."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        bar = tk.Frame(self, bg=UI["console_bg"])
        bar.pack(fill="x")
        tk.Label(bar, text="  Console", bg=UI["console_bg"], fg="#9AA6BC",
                 font=(FONT_FAMILY, 9, "bold")).pack(side="left", pady=3)
        tk.Button(bar, text="Clear", command=self.clear, relief="flat",
                  bg=UI["console_bg"], fg="#9AA6BC", bd=0, cursor="hand2",
                  activebackground="#2A3346", activeforeground="#FFFFFF",
                  font=(FONT_FAMILY, 8)).pack(side="right", padx=6)

        self.text = tk.Text(self, height=9, bg=UI["console_bg"],
                            fg=UI["console_fg"], insertbackground="#FFFFFF",
                            font=(MONO_FAMILY, 10), relief="flat", wrap="word",
                            state="disabled", padx=8, pady=6,
                            highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="top", fill="both", expand=True)

        self.text.tag_configure("out", foreground=UI["console_fg"])
        self.text.tag_configure("err", foreground=UI["console_err"])
        self.text.tag_configure("sys", foreground=UI["console_sys"])
        self.text.tag_configure("in", foreground="#FFD866")

        entry_row = tk.Frame(self, bg=UI["console_bg"])
        entry_row.pack(fill="x", side="bottom")
        tk.Label(entry_row, text=" input >", bg=UI["console_bg"], fg="#7FD1B9",
                 font=(MONO_FAMILY, 9)).pack(side="left")
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(entry_row, textvariable=self.entry_var, bd=0,
                              relief="flat", bg="#2A3346", fg="#FFFFFF",
                              insertbackground="#FFFFFF",
                              font=(MONO_FAMILY, 10))
        self.entry.pack(side="left", fill="x", expand=True, padx=6, pady=4,
                        ipady=2)
        self.entry.bind("<Return>", self.send)

    def send(self, _=None):
        text = self.entry_var.get()
        self.entry_var.set("")
        self.app.runner.send(text)

    def write(self, tag: str, line: str):
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


# =========================================================================== #
#  SECTION 12 - side panels: generated code, files, packages
# =========================================================================== #

PY_KEYWORDS = set(pykeyword.kwlist) | {"self", "True", "False", "None"}


class CodePane(ttk.Frame):
    """A live preview of the Python that the blocks make."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        bar = tk.Frame(self, bg=UI["panel"])
        bar.pack(fill="x")
        for label, cmd in (("Copy", self.copy),
                           ("Save .py", lambda: app.export_current()),
                           ("Open folder", lambda: app.open_folder())):
            tk.Button(bar, text=label, command=cmd, relief="flat", bd=0,
                      bg="#EEF1F6", fg=UI["text"], cursor="hand2",
                      font=(FONT_FAMILY, 8), padx=8,
                      activebackground="#DDE3EC").pack(side="left", padx=4,
                                                       pady=4)
        self.status = tk.Label(bar, text="", bg=UI["panel"], fg="#8A93A5",
                               font=(FONT_FAMILY, 8))
        self.status.pack(side="right", padx=8)

        self.text = tk.Text(self, bg="#FFFFFF", fg="#3A4356", relief="flat",
                            font=(MONO_FAMILY, 10), wrap="none", padx=8, pady=6,
                            highlightthickness=0, state="disabled")
        vs = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side="right", fill="y")
        hs.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)

        self.text.tag_configure("kw", foreground="#0033B3")
        self.text.tag_configure("str", foreground="#067D17")
        self.text.tag_configure("com", foreground="#8C8C8C")
        self.text.tag_configure("num", foreground="#1750EB")
        self.text.tag_configure("def", foreground="#7A3E9D")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))
        self.app.status("Generated code copied to the clipboard.")

    def show(self, source: str, note: str = ""):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", source)
        self.highlight()
        self.text.configure(state="disabled")
        self.status.configure(text=note)

    def highlight(self):
        for tag in ("kw", "str", "com", "num", "def"):
            self.text.tag_remove(tag, "1.0", "end")
        source = self.text.get("1.0", "end-1c")
        for i, line in enumerate(source.split("\n"), start=1):
            for m in re.finditer(r"\b[A-Za-z_][A-Za-z_0-9]*\b", line):
                if m.group(0) in PY_KEYWORDS:
                    self.text.tag_add("kw", "%d.%d" % (i, m.start()),
                                      "%d.%d" % (i, m.end()))
            for m in re.finditer(r"\b\d+(\.\d+)?\b", line):
                self.text.tag_add("num", "%d.%d" % (i, m.start()),
                                  "%d.%d" % (i, m.end()))
            for m in re.finditer(r"(\"[^\"]*\"|'[^']*')", line):
                self.text.tag_add("str", "%d.%d" % (i, m.start()),
                                  "%d.%d" % (i, m.end()))
            hash_pos = line.find("#")
            if hash_pos >= 0 and line.count('"') % 2 == 0:
                self.text.tag_add("com", "%d.%d" % (i, hash_pos),
                                  "%d.end" % i)


class FilesPane(ttk.Frame):
    """Files, variables and lists."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app

        tk.Label(self, text="Files", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=8, pady=(8, 2))
        self.files = tk.Listbox(self, height=6, bd=0, relief="flat",
                                highlightthickness=1, font=(FONT_FAMILY, 9),
                                highlightbackground=UI["border"],
                                selectbackground=UI["accent"],
                                selectforeground="#FFFFFF",
                                activestyle="none", exportselection=False)
        self.files.pack(fill="x", padx=8)
        self.files.bind("<<ListboxSelect>>", self.on_select)
        row = tk.Frame(self, bg=UI["panel"])
        row.pack(fill="x", padx=6, pady=4)
        for label, cmd in (("New", app.new_file), ("Rename", app.rename_file),
                           ("Delete", app.delete_file)):
            tk.Button(row, text=label, command=cmd, relief="flat", bd=0,
                      bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 8),
                      cursor="hand2", padx=8,
                      activebackground="#DDE3EC").pack(side="left", padx=2)

        tk.Label(self, text="Variables", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=8,
                                                     pady=(10, 2))
        self.vars = tk.Listbox(self, height=6, bd=0, relief="flat",
                               highlightthickness=1, font=(FONT_FAMILY, 9),
                               highlightbackground=UI["border"],
                               selectbackground=CATS["variables"]["color"],
                               selectforeground="#FFFFFF",
                               activestyle="none", exportselection=False)
        self.vars.pack(fill="x", padx=8)
        row2 = tk.Frame(self, bg=UI["panel"])
        row2.pack(fill="x", padx=6, pady=4)
        for label, cmd in (("New variable", app.make_variable),
                           ("Start value", app.set_var_value),
                           ("Delete", app.delete_variable)):
            tk.Button(row2, text=label, command=cmd, relief="flat", bd=0,
                      bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 8),
                      cursor="hand2", padx=6,
                      activebackground="#DDE3EC").pack(side="left", padx=2)

        tk.Label(self, text="Lists", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=8,
                                                     pady=(10, 2))
        self.lists = tk.Listbox(self, height=5, bd=0, relief="flat",
                                highlightthickness=1, font=(FONT_FAMILY, 9),
                                highlightbackground=UI["border"],
                                selectbackground=CATS["lists"]["color"],
                                selectforeground="#FFFFFF",
                                activestyle="none", exportselection=False)
        self.lists.pack(fill="x", padx=8)
        row3 = tk.Frame(self, bg=UI["panel"])
        row3.pack(fill="x", padx=6, pady=4)
        for label, cmd in (("New list", app.make_list),
                           ("Delete", app.delete_list)):
            tk.Button(row3, text=label, command=cmd, relief="flat", bd=0,
                      bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 8),
                      cursor="hand2", padx=8,
                      activebackground="#DDE3EC").pack(side="left", padx=2)

        tk.Label(self, text="Custom blocks", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", padx=8,
                                                     pady=(10, 2))
        self.funcs = tk.Listbox(self, height=4, bd=0, relief="flat",
                                highlightthickness=1, font=(FONT_FAMILY, 9),
                                highlightbackground=UI["border"],
                                selectbackground=CATS["functions"]["color"],
                                selectforeground="#FFFFFF",
                                activestyle="none", exportselection=False)
        self.funcs.pack(fill="x", padx=8, pady=(0, 4))
        row4 = tk.Frame(self, bg=UI["panel"])
        row4.pack(fill="x", padx=6, pady=(0, 10))
        for label, cmd in (("Make a block", app.make_function),
                           ("Delete", app.delete_function)):
            tk.Button(row4, text=label, command=cmd, relief="flat", bd=0,
                      bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 8),
                      cursor="hand2", padx=8,
                      activebackground="#DDE3EC").pack(side="left", padx=2)

    def on_select(self, _=None):
        sel = self.files.curselection()
        if sel:
            self.app.select_file(sel[0])

    def refresh(self):
        p = self.app.project
        cur = self.app.file_index
        self.files.delete(0, "end")
        for f in p.files:
            self.files.insert("end", "  " + f.name + ".py")
        if 0 <= cur < len(p.files):
            self.files.selection_clear(0, "end")
            self.files.selection_set(cur)
        self.vars.delete(0, "end")
        for v in p.variables:
            self.vars.insert("end", "  %s = %s" % (v, p.var_init.get(v, "0")))
        self.lists.delete(0, "end")
        for v in p.lists:
            self.lists.insert("end", "  " + v)
        self.funcs.delete(0, "end")
        for fn in p.functions:
            params = ", ".join(fn.get("params", []))
            self.funcs.insert("end", "  %s(%s)" % (fn["name"], params))


class PackagesPane(ttk.Frame):
    """The pip dashboard: install anything, get blocks for it."""

    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.installed: List[dict] = []
        # Everything is packed into one column; the parts are then handed to
        # grid so the list of packages is the only thing that stretches.
        self.grid_rowconfigure(8, weight=1)
        self.grid_columnconfigure(0, weight=1)

        env = tk.Frame(self, bg="#EEF4FF")
        env.grid(row=0, column=0, sticky="ew")
        self.env_label = tk.Label(env, text="", bg="#EEF4FF", fg="#3373CC",
                                  font=(FONT_FAMILY, 8), anchor="w")
        self.env_label.pack(side="left", padx=8, pady=4)
        tk.Button(env, text="Settings", command=app.open_settings,
                  relief="flat", bd=0, bg="#DCE8FF", fg="#3373CC",
                  font=(FONT_FAMILY, 8, "bold"), cursor="hand2", padx=8,
                  activebackground="#C7DBFF").pack(side="right", padx=6)

        top = tk.Frame(self, bg=UI["panel"])
        top.grid(row=1, column=0, sticky="ew", padx=8, pady=(8, 4))
        tk.Label(top, text="Install a package from PyPI", bg=UI["panel"],
                 fg=UI["text"], font=(FONT_FAMILY, 9, "bold")).pack(anchor="w")
        entry_row = tk.Frame(self, bg=UI["panel"])
        entry_row.grid(row=2, column=0, sticky="ew", padx=8)
        self.pkg_var = tk.StringVar()
        ent = tk.Entry(entry_row, textvariable=self.pkg_var, bd=0, relief="flat",
                       font=(FONT_FAMILY, 9), bg="#FFFFFF",
                       highlightthickness=1, highlightbackground=UI["border"])
        ent.pack(side="left", fill="x", expand=True, ipady=3)
        ent.bind("<Return>", lambda e: self.install())
        tk.Button(entry_row, text="Install", command=self.install, relief="flat",
                  bd=0, bg=CATS["packages"]["color"], fg="#FFFFFF",
                  activebackground=CATS["packages"]["dark"],
                  activeforeground="#FFFFFF", cursor="hand2",
                  font=(FONT_FAMILY, 9, "bold"), padx=12).pack(side="left",
                                                               padx=(6, 0))

        quick = tk.Frame(self, bg=UI["panel"])
        quick.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 2))
        tk.Label(quick, text="Popular:", bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 8)).pack(side="left")
        for name in POPULAR[:8]:
            tk.Button(quick, text=name, relief="flat", bd=0, bg="#EEF1F6",
                      fg=UI["text"], font=(FONT_FAMILY, 8), cursor="hand2",
                      activebackground="#DDE3EC",
                      command=lambda n=name: self.pkg_var.set(n)).pack(
                          side="left", padx=2)

        # everything below here is pinned to the bottom, so the pip log is
        # always visible however short the panel gets
        mid = tk.Frame(self, bg=UI["panel"])
        mid.grid(row=4, column=0, sticky="ew", padx=8, pady=(8, 0))
        tk.Label(mid, text="Installed packages", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(mid, height=5, bd=0, relief="flat",
                                  highlightthickness=1, font=(FONT_FAMILY, 9),
                                  highlightbackground=UI["border"],
                                  selectbackground=CATS["packages"]["color"],
                                  selectforeground="#FFFFFF",
                                  activestyle="none", exportselection=False)
        self.listbox.pack(fill="x", pady=2)
        btns = tk.Frame(self, bg=UI["panel"])
        btns.grid(row=5, column=0, sticky="ew", padx=6, pady=(6, 2))
        buttons = (("Add blocks", self.add_blocks, CATS["packages"]["color"]),
                   ("Remove blocks", self.remove_blocks, None),
                   ("Uninstall", self.uninstall, None),
                   ("Refresh list", self.refresh_list, None))
        for index, (label, cmd, tint) in enumerate(buttons):
            tk.Button(btns, text=label, command=cmd, relief="flat", bd=0,
                      bg=tint or "#EEF1F6",
                      fg="#FFFFFF" if tint else UI["text"],
                      font=(FONT_FAMILY, 8, "bold" if tint else "normal"),
                      cursor="hand2", pady=3,
                      activebackground=CATS["packages"]["dark"] if tint
                      else "#DDE3EC",
                      activeforeground="#FFFFFF" if tint else UI["text"]).grid(
                          row=index // 2, column=index % 2, sticky="ew",
                          padx=2, pady=2)
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        low = tk.Frame(self, bg=UI["panel"])
        low.grid(row=6, column=0, sticky="ew", padx=8, pady=(6, 2))
        tk.Label(low, text="Blocks from a module you already have:",
                 bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 8)).pack(anchor="w")
        mod_row = tk.Frame(self, bg=UI["panel"])
        mod_row.grid(row=7, column=0, sticky="ew", padx=8)
        self.mod_var = tk.StringVar()
        self.mod_box = ttk.Combobox(mod_row, textvariable=self.mod_var,
                                    values=STDLIB_MODULES, height=14)
        self.mod_box.pack(side="left", fill="x", expand=True)
        tk.Button(mod_row, text="Make blocks", command=self.add_module_blocks,
                  relief="flat", bd=0, bg="#EEF1F6", fg=UI["text"],
                  font=(FONT_FAMILY, 8), cursor="hand2", padx=8,
                  activebackground="#DDE3EC").pack(side="left", padx=4)

        self.log = tk.Text(self, height=4, bg="#20262F", fg="#C9D4E4",
                           font=(MONO_FAMILY, 9), relief="flat", wrap="word",
                           state="disabled", padx=6, pady=4,
                           highlightthickness=0)
        self.log.grid(row=8, column=0, sticky="nsew", padx=8, pady=(8, 4))

        self.packs_label = tk.Label(self, text="", bg=UI["panel"], fg="#8A93A5",
                                    font=(FONT_FAMILY, 8), anchor="w",
                                    justify="left", wraplength=320)
        self.packs_label.grid(row=9, column=0, sticky="ew", padx=8, pady=(0, 8))

    # -- logging ------------------------------------------------------------ #

    def write(self, line: str):
        def do():
            self.log.configure(state="normal")
            self.log.insert("end", line + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            # the console is always visible, so pip output goes there as well
            self.app.console.write("out", line)
        self.app.ui(do)

    def refresh_interpreter(self):
        self.env_label.configure(
            text="Installing into: " + self.app.interpreter_label(short=True))

    def refresh_packs_label(self):
        self.refresh_interpreter()
        packs = self.app.project.packs
        if packs:
            names = ", ".join(p.get("module", "?") for p in packs)
            count = sum(len(p.get("blocks", [])) for p in packs)
            self.packs_label.configure(
                text="%d blocks loaded from: %s" % (count, names))
        else:
            self.packs_label.configure(text="No package blocks yet.")
        self.colour_list()

    def colour_list(self):
        """Tint each installed package that has blocks with its own colour."""
        packs = {}
        for pack in self.app.project.packs:
            for key in (pack.get("dist", ""), pack.get("module", "")):
                if key:
                    packs[key.lower().replace("_", "-")] = pack
        for index in range(self.listbox.size()):
            entry = self.listbox.get(index).strip().split()
            if not entry:
                continue
            pack = packs.get(entry[0].lower().replace("_", "-"))
            if pack:
                self.listbox.itemconfig(index, foreground=pack.get("dark",
                                                                   "#0B8E69"))
            else:
                self.listbox.itemconfig(index, foreground=UI["text"])

    # -- actions ------------------------------------------------------------ #

    def refresh_list(self):
        self.refresh_interpreter()
        self.write("Reading the list of installed packages...")

        def done(data):
            self.installed = data
            self.listbox.delete(0, "end")
            for item in sorted(data, key=lambda d: d.get("name", "").lower()):
                self.listbox.insert("end", "  %s  %s" % (item.get("name", "?"),
                                                         item.get("version", "")))
            self.write("Found %d packages." % len(data))
            names = sorted(set([d.get("name", "") for d in data] +
                               STDLIB_MODULES))
            self.mod_box.configure(values=names)
            self.colour_list()
        self.app.packages.list_installed(done)

    def selected_name(self) -> Optional[str]:
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.listbox.get(sel[0]).split()[0]

    def install(self):
        name = self.pkg_var.get().strip()
        if not name:
            return
        self.write("$ pip install %s" % name)
        self.app.status("Installing %s ..." % name)

        def done(code):
            if code == 0:
                self.write("Installed. Building blocks...")
                self.app.status("%s installed - making blocks" % name)
                self.after(50, lambda: self.make_blocks_for_dist(name))
            else:
                self.write("pip finished with code %s" % code)
                self.app.status("Install of %s failed - see the log." % name)
            self.refresh_list()
        self.app.packages.install(name, self.write, done)

    def uninstall(self):
        name = self.selected_name()
        if not name:
            self.write("Select a package in the list first.")
            self.app.status("Pick a package in the list, then press Uninstall.")
            return
        if self.app.settings.get("confirm_uninstall"):
            if not messagebox.askyesno(
                    "Uninstall",
                    "Remove %s from %s?\n\nIts blocks will disappear too."
                    % (name, self.app.interpreter_label())):
                return
        self.write("$ pip uninstall -y %s" % name)

        def done(code):
            self.write("Done (code %s)." % code)
            removed = 0
            for pack in list(self.app.project.packs):
                keys = (pack.get("dist", ""), pack.get("module", ""))
                if any(k.lower().replace("_", "-") ==
                       name.lower().replace("_", "-") for k in keys if k):
                    unregister_pack(pack)
                    forget_cached_pack(pack.get("module", ""))
                    self.app.project.packs.remove(pack)
                    removed += len(pack.get("blocks", []))
            if removed:
                self.write("Removed %d blocks that belonged to it." % removed)
            self.app.on_change()
            self.app.refresh_all()
            self.refresh_list()
        self.app.packages.uninstall(name, self.write, done)

    def add_blocks(self):
        name = self.selected_name()
        if not name:
            self.write("Select a package in the list first.")
            return
        self.make_blocks_for_dist(name)

    def remove_blocks(self):
        """Take a package's blocks out of the palette (keeps the package)."""
        packs = self.app.project.packs
        if not packs:
            self.write("There are no package blocks to remove.")
            return
        menu = tk.Menu(self, tearoff=0)

        def drop(pack):
            unregister_pack(pack)
            forget_cached_pack(pack.get("module", ""))
            if pack in self.app.project.packs:
                self.app.project.packs.remove(pack)
            self.write("Removed the blocks for %s." % pack.get("module"))
            self.app.on_change()
            self.app.refresh_all()

        for pack in packs:
            menu.add_command(label="%s (%d blocks)" % (pack.get("module", "?"),
                                                       len(pack.get("blocks", []))),
                             command=lambda p=pack: drop(p))
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def add_module_blocks(self):
        module = self.mod_var.get().strip()
        if not module:
            return
        self.write("Looking inside %s ..." % module)
        self.make_blocks_for_module(module, module)

    def make_blocks_for_dist(self, dist: str, then: Optional[Callable] = None):
        self.write("Working out what %s installs..." % dist)

        def got(mods):
            if not mods:
                self.write("Could not find the module for %s." % dist)
                if then:
                    then()
                return
            main = sorted(mods, key=len)[0]
            self.write("Reading the module %s ..." % main)
            self.make_blocks_for_module(main, dist, then)
        self.app.packages.modules_of(dist, got)

    def make_blocks_for_module(self, module: str, dist: str,
                               then: Optional[Callable] = None):
        def got(data):
            if data.get("error"):
                self.write("! " + str(data["error"]))
                if module.split(".")[0] not in CURATED:
                    if then:
                        then()
                    return
            pack = self.app.packages.build_pack(module, data, dist)
            if not pack["blocks"]:
                self.write("No usable functions found in %s." % module)
                if then:
                    then()
                return
            for old in list(self.app.project.packs):
                if old.get("module") == module:
                    unregister_pack(old)
                    self.app.project.packs.remove(old)
            self.app.project.packs.append(pack)
            register_pack(pack)
            self.app.packages.save_pack(pack)
            self.write("Added %d blocks for %s." % (len(pack["blocks"]), module))
            self.app.status("New blocks for %s are in the Packages category."
                            % module)
            self.app.categories.select("packages")
            self.app.on_change()
            self.app.refresh_all()
            self.refresh_packs_label()
            if then:
                then()
        self.app.packages.introspect(module, got)


# =========================================================================== #
#  SECTION 13 - the application window
# =========================================================================== #

class App:
    """Glues every panel together."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings()
        self.project = Project()
        self.renderer = Renderer(self.project,
                                 float(self.settings.get("zoom", 1.0)))
        self.packages = PackageManager(self)
        self.runner = Runner(self)
        self.file_index = 0
        self.undo_stack: List[str] = []
        self.redo_stack: List[str] = []
        self._preview_job = None
        self._autosave_job = None
        self.project_mtime = 0.0
        self._watch_tick = 0.0
        # background threads never touch tkinter directly - they drop a
        # callable in here and pump() runs it on the main thread.
        self.ui_queue: "queue.Queue[Callable]" = queue.Queue()

        root.title(APP_NAME)
        root.geometry("1420x880")
        root.minsize(1040, 660)
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("TFrame", background=UI["panel"])
            style.configure("TNotebook", background=UI["panel"], borderwidth=0)
            style.configure("TNotebook.Tab", padding=(14, 6),
                            font=(FONT_FAMILY, 9))
            style.configure("TPanedwindow", background=UI["border"])
        except Exception:
            pass

        self.build_topbar()
        self.build_toolbar()
        self.build_body()
        self.build_menu()
        self.bind_keys()

        self.set_project(demo_project(), remember=False)
        self.console.write("sys", "%s %s - press the green flag to run your "
                                  "blocks." % (APP_NAME, APP_VERSION))
        self.console.write("sys", "Python files are written to %s"
                           % self.project.folder())
        self.pump()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # -- construction ------------------------------------------------------- #

    def build_topbar(self):
        bar = tk.Frame(self.root, bg=UI["topbar"], height=46)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        logo = tk.Canvas(bar, width=34, height=34, bg=UI["topbar"],
                         highlightthickness=0)
        logo.pack(side="left", padx=(12, 6), pady=6)
        logo.create_oval(2, 2, 32, 32, fill="#FFFFFF", outline="")
        logo.create_polygon(11, 9, 25, 17, 11, 25, fill=UI["topbar"],
                            outline="")
        tk.Label(bar, text=APP_NAME, bg=UI["topbar"], fg="#FFFFFF",
                 font=(FONT_FAMILY, 13, "bold")).pack(side="left")
        self.title_var = tk.StringVar(value="Untitled")
        tk.Label(bar, textvariable=self.title_var, bg=UI["topbar"],
                 fg="#E4D9FF", font=(FONT_FAMILY, 10)).pack(side="left",
                                                            padx=14)
        self.menu_holder = tk.Frame(bar, bg=UI["topbar"])
        self.menu_holder.pack(side="right", padx=10)

    def build_toolbar(self):
        bar = tk.Frame(self.root, bg=UI["toolbar"], height=46)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=UI["border"], height=1).pack(fill="x")

        flag = tk.Canvas(bar, width=34, height=34, bg=UI["toolbar"],
                         highlightthickness=0, cursor="hand2")
        flag.create_line(10, 6, 10, 28, fill="#4CBF56", width=2)
        flag.create_polygon(11, 7, 27, 12, 11, 18, fill="#4CBF56", outline="")
        flag.pack(side="left", padx=(12, 2), pady=6)
        flag.bind("<Button-1>", lambda e: self.run())
        self.flag_widget = flag

        stop = tk.Canvas(bar, width=30, height=34, bg=UI["toolbar"],
                         highlightthickness=0, cursor="hand2")
        stop.create_polygon(9, 12, 13, 8, 19, 8, 23, 12, 23, 18, 19, 22,
                            13, 22, 9, 18, fill="#EC5959", outline="")
        stop.pack(side="left", padx=2, pady=6)
        stop.bind("<Button-1>", lambda e: self.stop())

        def tool(label, cmd, width=None):
            b = tk.Button(bar, text=label, command=cmd, relief="flat", bd=0,
                          bg=UI["toolbar"], fg=UI["text"], cursor="hand2",
                          activebackground="#E9EDF3", font=(FONT_FAMILY, 9),
                          padx=10, pady=4)
            b.pack(side="left", padx=2, pady=8)
            return b

        tk.Frame(bar, bg=UI["border"], width=1).pack(side="left", fill="y",
                                                     padx=8, pady=10)
        tool("Save", self.save)
        tool("Open", self.open_project)
        tool("Import .py", self.import_python_file)
        tool("Export .py", self.export_all)
        folder_btn = tool("Code folder", self.open_folder)
        folder_btn.configure(fg="#3373CC")
        tk.Frame(bar, bg=UI["border"], width=1).pack(side="left", fill="y",
                                                     padx=8, pady=10)
        tool("Clean up", lambda: self.workspace.cleanup())
        tool("Undo", self.undo)
        tool("Settings", self.open_settings)
        tk.Frame(bar, bg=UI["border"], width=1).pack(side="left", fill="y",
                                                     padx=8, pady=10)
        tool("-", lambda: self.zoom(-0.1))
        self.zoom_label = tk.Label(bar, text="100%", bg=UI["toolbar"],
                                   fg=UI["text"], font=(FONT_FAMILY, 8))
        self.zoom_label.pack(side="left")
        tool("+", lambda: self.zoom(0.1))

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(bar, textvariable=self.status_var, bg=UI["toolbar"],
                 fg="#8A93A5", font=(FONT_FAMILY, 9),
                 anchor="e").pack(side="right", padx=14)

    def build_body(self):
        outer = self.outer = ttk.PanedWindow(self.root, orient="vertical")
        outer.pack(fill="both", expand=True)

        upper = self.upper = ttk.PanedWindow(outer, orient="horizontal")
        outer.add(upper, weight=5)

        left = tk.Frame(upper, bg=UI["panel"])
        self.categories = CategoryStrip(left, self)
        self.categories.pack(side="left", fill="y")
        self.palette = PaletteView(left, self)
        self.palette.pack(side="left", fill="both", expand=True)
        upper.add(left, weight=0)

        center = tk.Frame(upper, bg=UI["panel"])
        self.tabbar = tk.Frame(center, bg="#EEF1F6", height=34)
        self.tabbar.pack(fill="x")
        self.workspace = WorkspaceView(center, self)
        self.workspace.pack(fill="both", expand=True)
        upper.add(center, weight=4)

        right = ttk.Notebook(upper, width=360)
        self.code_pane = CodePane(right, self)
        self.files_pane = FilesPane(right, self)
        self.packages_pane = PackagesPane(right, self)
        right.add(self.code_pane, text="Python code")
        right.add(self.files_pane, text="Files and data")
        right.add(self.packages_pane, text="Packages")
        upper.add(right, weight=1)
        self.right_nb = right

        self.console = ConsolePane(outer, self)
        outer.add(self.console, weight=0)
        self.root.after(150, self.init_sashes)
        self.root.bind("<Map>", lambda e: self.root.after(60, self.init_sashes))

    def init_sashes(self):
        """Give the panes a sensible starting size (weights only affect resizing)."""
        if getattr(self, "_sashes_done", False):
            return
        try:
            self.root.update_idletasks()
            height = self.outer.winfo_height()
            width = self.upper.winfo_width()
            if height < 200 or width < 400:
                return
            self.outer.sashpos(0, int(height * 0.80))
            self.upper.sashpos(0, 396)
            self.upper.sashpos(1, max(560, width - 372))
            self._sashes_done = True
        except Exception:
            pass

    def build_menu(self):
        specs = [
            ("File", [("New project", self.new_project),
                      ("Open project...", self.open_project),
                      ("Import a Python file as blocks...",
                       self.import_python_file),
                      ("Save", self.save),
                      ("Save as...", self.save_as),
                      None,
                      ("Export all Python files", self.export_all),
                      ("Open project folder", self.open_folder),
                      None,
                      ("Exit", self.on_close)]),
            ("Edit", [("Undo", self.undo),
                      ("Clean up blocks", lambda: self.workspace.cleanup()),
                      ("Delete all blocks in this tab",
                       lambda: self.workspace.delete_all()),
                      None,
                      ("New file", self.new_file),
                      ("Make a variable", self.make_variable),
                      ("Make a list", self.make_list),
                      ("Make a block", self.make_function)]),
            ("Run", [("Run this file", self.run),
                     ("Stop", self.stop),
                     None,
                     ("Show generated code", lambda: self.show_tab(0))]),
            ("Packages", [("Package dashboard", self.open_packages),
                          ("Blocks for a module...", self.quick_module),
                          None,
                          ("Refresh installed list",
                           lambda: self.packages_pane.refresh_list())]),
            ("AI", [("Connect an AI assistant (MCP)...", self.show_mcp),
                    None,
                    ("Import a Python file as blocks...",
                     self.import_python_file),
                    ("Paste Python and turn it into blocks...",
                     self.paste_python)]),
            ("Help", [("Quick guide", self.help_guide),
                      ("About", self.about)]),
        ]
        for title, items in specs:
            mb = tk.Menubutton(self.menu_holder, text=title, bg=UI["topbar"],
                               fg="#FFFFFF", activebackground=UI["topbar_dark"],
                               activeforeground="#FFFFFF", relief="flat", bd=0,
                               font=(FONT_FAMILY, 9), padx=10, pady=6,
                               cursor="hand2")
            menu = tk.Menu(mb, tearoff=0, font=(FONT_FAMILY, 9))
            for item in items:
                if item is None:
                    menu.add_separator()
                else:
                    menu.add_command(label=item[0], command=item[1])
            mb.configure(menu=menu)
            mb.pack(side="left")

    def bind_keys(self):
        r = self.root
        r.bind("<Control-s>", lambda e: self.save())
        r.bind("<Control-S>", lambda e: self.save_as())
        r.bind("<Control-o>", lambda e: self.open_project())
        r.bind("<Control-n>", lambda e: self.new_project())
        r.bind("<Control-z>", lambda e: self.undo())
        r.bind("<Control-e>", lambda e: self.export_all())
        r.bind("<Control-i>", lambda e: self.import_python_file())
        r.bind("<Control-comma>", lambda e: self.open_settings())
        r.bind("<F5>", lambda e: self.run())
        r.bind("<Escape>", lambda e: self.stop())
        r.bind("<Control-plus>", lambda e: self.zoom(0.1))
        r.bind("<Control-minus>", lambda e: self.zoom(-0.1))

    # -- small helpers ------------------------------------------------------ #

    def ui(self, fn: Callable):
        """Ask for ``fn`` to be run on the user interface thread."""
        self.ui_queue.put(fn)

    # -- which Python are we using? ----------------------------------------- #

    def venv_folder(self) -> str:
        return venv_folder_for(self.settings, self.project.folder())

    def venv_python(self) -> str:
        return venv_python_path(self.venv_folder())

    def venv_ready(self) -> bool:
        return os.path.exists(self.venv_python())

    def interpreter(self) -> str:
        """The interpreter used to run programs and to drive pip."""
        if self.settings.get("use_venv") and self.venv_ready():
            return self.venv_python()
        return PYTHON_EXE

    def interpreter_label(self, short: bool = False) -> str:
        if self.settings.get("use_venv"):
            if self.venv_ready():
                folder = self.venv_folder()
                return ("venv " + os.path.basename(folder)) if short \
                    else "venv: " + folder
            return "venv is switched on but has not been created yet"
        if PYTHON_EXE:
            if short:
                return "Python %s" % platform.python_version()
            return PYTHON_EXE
        if FROZEN:
            return "the Python built into ScratchPy Studio"
        return "none found"

    def open_settings(self):
        SettingsDialog(self.root, self)

    def status(self, message: str):
        self.status_var.set(message)

    def show_tab(self, index: int):
        try:
            self.right_nb.select(index)
        except Exception:
            pass

    def open_packages(self):
        self.show_tab(2)
        if not self.packages_pane.installed:
            self.packages_pane.refresh_list()

    def quick_module(self):
        name = simpledialog.askstring(
            "Blocks from a module",
            "Which module should become blocks?\n"
            "(anything you can import: turtle, math, requests, numpy ...)",
            parent=self.root)
        if name:
            self.show_tab(2)
            self.packages_pane.make_blocks_for_module(name.strip(),
                                                      name.strip())

    def over_palette(self, xr: float, yr: float) -> bool:
        for widget in (self.palette, self.categories):
            try:
                x0 = widget.winfo_rootx()
                y0 = widget.winfo_rooty()
                if x0 <= xr <= x0 + widget.winfo_width() and \
                        y0 <= yr <= y0 + widget.winfo_height():
                    return True
            except Exception:
                pass
        return False

    @property
    def current_file(self) -> SpyFile:
        if not self.project.files:
            self.project.files.append(SpyFile("main"))
        self.file_index = max(0, min(self.file_index,
                                     len(self.project.files) - 1))
        return self.project.files[self.file_index]

    # -- undo --------------------------------------------------------------- #

    def push_undo(self):
        try:
            self.undo_stack.append(self.project.snapshot())
        except Exception:
            return
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            self.status("Nothing left to undo.")
            return
        snap = self.undo_stack.pop()
        try:
            data = json.loads(snap)
        except Exception:
            return
        idx = self.file_index
        self.project = Project.from_json(data)
        self.renderer.project = self.project
        self.file_index = min(idx, len(self.project.files) - 1)
        self.refresh_all()
        self.status("Undone.")

    # -- project level ------------------------------------------------------ #

    def set_project(self, project: Project, remember: bool = True):
        known = {p.get("module") for p in project.packs}
        for pack in load_cached_packs():
            if pack.get("module") not in known:
                project.packs.append(pack)
        project.sync_specs()
        self.project = project
        self.renderer.project = project
        self.file_index = 0
        self.undo_stack.clear()
        self.refresh_all()
        self.update_title()

    def update_title(self):
        star = "*" if self.project.dirty else ""
        self.title_var.set("%s%s   -   %s" % (self.project.name, star,
                                              self.project.folder()))
        self.root.title("%s - %s%s" % (APP_NAME, self.project.name, star))

    def refresh_all(self):
        self.rebuild_tabbar()
        self.workspace.set_file(self.current_file)
        self.palette.rebuild()
        self.files_pane.refresh()
        self.packages_pane.refresh_packs_label()
        self.update_preview()
        self.update_title()

    def on_change(self):
        self.project.dirty = True
        self.update_title()
        self.files_pane.refresh()
        self.schedule_preview()
        self.schedule_autosave()

    def schedule_autosave(self):
        if not self.settings.get("autosave") or not self.project.path:
            return
        if getattr(self, "_autosave_job", None):
            try:
                self.root.after_cancel(self._autosave_job)
            except Exception:
                pass
        self._autosave_job = self.root.after(4000, self.autosave)

    def autosave(self):
        self._autosave_job = None
        if not self.settings.get("autosave") or not self.project.path:
            return
        if not self.project.dirty:
            return
        try:
            with open(self.project.path, "w", encoding="utf-8") as fh:
                json.dump(self.project.to_json(), fh, indent=1)
            self.write_files()
            self.project.dirty = False
            self.project_mtime = self.file_mtime(self.project.path)
            self.update_title()
            self.status("Saved automatically.")
        except Exception as exc:
            self.status("Autosave failed: %s" % exc)

    def schedule_preview(self):
        if self._preview_job:
            try:
                self.root.after_cancel(self._preview_job)
            except Exception:
                pass
        self._preview_job = self.root.after(250, self.update_preview)

    def update_preview(self):
        self._preview_job = None
        try:
            source = generate_file(self.project, self.current_file)
        except Exception:
            source = "# Something went wrong while generating code:\n\n" + \
                     traceback.format_exc()
        problem = check_syntax(source)
        note = "OK" if not problem else "problem: " + problem
        self.code_pane.show(source, note)

    # -- files -------------------------------------------------------------- #

    def rebuild_tabbar(self):
        for w in self.tabbar.winfo_children():
            w.destroy()
        for i, f in enumerate(self.project.files):
            selected = (i == self.file_index)
            b = tk.Button(self.tabbar, text=" %s.py " % f.name,
                          relief="flat", bd=0,
                          bg="#FFFFFF" if selected else "#EEF1F6",
                          fg=UI["text"] if selected else "#8A93A5",
                          font=(FONT_FAMILY, 9,
                                "bold" if selected else "normal"),
                          cursor="hand2", padx=10, pady=5,
                          command=lambda n=i: self.select_file(n))
            b.pack(side="left", padx=(2, 0), pady=(3, 0))
            b.bind("<Button-3>", lambda e, n=i: self.tab_menu(e, n))
        tk.Button(self.tabbar, text=" + ", relief="flat", bd=0, bg="#EEF1F6",
                  fg=UI["text"], font=(FONT_FAMILY, 11), cursor="hand2",
                  command=self.new_file, padx=8).pack(side="left", padx=4,
                                                      pady=(3, 0))

    def tab_menu(self, ev, index: int):
        self.select_file(index)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Rename", command=self.rename_file)
        menu.add_command(label="Duplicate", command=self.duplicate_file)
        menu.add_command(label="Delete", command=self.delete_file)
        menu.tk_popup(ev.x_root, ev.y_root)

    def select_file(self, index: int):
        if not (0 <= index < len(self.project.files)):
            return
        self.workspace.close_editor()
        self.file_index = index
        self.rebuild_tabbar()
        self.workspace.set_file(self.current_file)
        self.files_pane.refresh()
        self.update_preview()

    def new_file(self):
        name = simpledialog.askstring("New file", "Name of the new file:",
                                      initialvalue=self.project.unique_file_name(),
                                      parent=self.root)
        if not name:
            return
        name = safe_module_name(name)
        if self.project.file_by_name(name):
            messagebox.showwarning("New file", "That name is already used.")
            return
        self.push_undo()
        self.project.files.append(SpyFile(name))
        self.file_index = len(self.project.files) - 1
        self.on_change()
        self.refresh_all()

    def rename_file(self):
        f = self.current_file
        name = simpledialog.askstring("Rename file", "New name:",
                                      initialvalue=f.name, parent=self.root)
        if not name:
            return
        self.push_undo()
        f.name = safe_module_name(name)
        self.on_change()
        self.refresh_all()

    def paste_python(self):
        """Paste a program straight in and watch it become blocks."""
        top = tk.Toplevel(self.root)
        top.title("Paste Python")
        top.configure(bg=UI["panel"])
        top.transient(self.root)
        top.grab_set()
        tk.Label(top, text="Paste any Python program here", bg=UI["panel"],
                 fg=UI["text"], font=(FONT_FAMILY, 12, "bold")).pack(
                     padx=16, pady=(14, 2), anchor="w")
        tk.Label(top, text="It becomes blocks in a new tab. Anything unusual "
                           "is kept exactly as it is.",
                 bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 9)).pack(padx=16, anchor="w")
        text = tk.Text(top, width=84, height=22, font=(MONO_FAMILY, 10),
                       bg="#FFFFFF", fg="#3A4356", relief="flat", padx=8,
                       pady=6, wrap="none", undo=True, highlightthickness=1,
                       highlightbackground=UI["border"])
        text.pack(fill="both", expand=True, padx=16, pady=8)
        text.focus_set()
        name_row = tk.Frame(top, bg=UI["panel"])
        name_row.pack(fill="x", padx=16)
        tk.Label(name_row, text="Tab name", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9)).pack(side="left")
        name_var = tk.StringVar(value="pasted")
        tk.Entry(name_row, textvariable=name_var, bd=0, relief="flat",
                 font=(FONT_FAMILY, 9), bg="#FFFFFF", highlightthickness=1,
                 highlightbackground=UI["border"], width=24).pack(
                     side="left", padx=8, ipady=3)

        def go():
            source = text.get("1.0", "end-1c")
            if not source.strip():
                top.destroy()
                return
            top.destroy()
            self.import_source(source, name_var.get().strip() or "pasted")

        row = tk.Frame(top, bg=UI["panel"])
        row.pack(pady=12)
        tk.Button(row, text="Cancel", command=top.destroy, relief="flat", bd=0,
                  bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 9), padx=16,
                  pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text="Make blocks", command=go, relief="flat", bd=0,
                  bg=CATS["python"]["color"], fg="#FFFFFF",
                  activebackground=CATS["python"]["dark"],
                  activeforeground="#FFFFFF", font=(FONT_FAMILY, 9, "bold"),
                  padx=22, pady=6, cursor="hand2").pack(side="left", padx=6)
        top.bind("<Escape>", lambda e: top.destroy())

    def import_python_file(self):
        """Open any .py file and turn the whole thing into blocks."""
        path = filedialog.askopenfilename(
            title="Choose a Python file to turn into blocks",
            initialdir=self.project.folder(),
            filetypes=[("Python files", "*.py *.pyw"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
        except Exception as exc:
            messagebox.showerror("Import", "Could not read the file:\n%s" % exc)
            return
        self.import_source(source, os.path.splitext(os.path.basename(path))[0])

    def import_source(self, source: str, name: str = "imported"):
        self.push_undo()
        try:
            spyfile, importer = import_python_source(
                self.project, source, self.project.unique_file_name(
                    safe_module_name(name)))
        except SyntaxError as exc:
            messagebox.showerror(
                "Import",
                "That file is not valid Python:\n\nline %s: %s"
                % (exc.lineno, exc.msg))
            return
        except Exception as exc:
            messagebox.showerror("Import", "Could not convert it:\n%s" % exc)
            return

        self.project.files.append(spyfile)
        self.file_index = len(self.project.files) - 1
        self.on_change()
        self.refresh_all()

        blocks = sum(1 for s in spyfile.scripts for _ in s.descendants())
        self.console.write("sys", "--- imported %s.py ---" % spyfile.name)
        self.console.write("out", "%d blocks made, %d kept as plain Python."
                           % (blocks, importer.raw))
        for note in dedupe(importer.notes)[:6]:
            self.console.write("out", "  note: " + note)
        self.status("Imported into the '%s' tab." % spyfile.name)

        wanted = third_party_packages(importer.packages)
        missing = [p for p in wanted
                   if not any(pack.get("dist", "").lower() == p.lower() or
                              pack.get("module", "").lower() == p.lower()
                              for pack in self.project.packs)]
        if missing:
            self.offer_packages(missing)

    def offer_packages(self, names: List[str]):
        listing = ", ".join(names)
        self.console.write("sys", "This program uses: " + listing)
        if not messagebox.askyesno(
                "Packages needed",
                "This program uses:\n\n    %s\n\n"
                "Install them and make blocks for them now?" % listing):
            return
        self.show_tab(2)
        self.install_queue = list(names)
        self.install_next()

    def install_next(self):
        if not getattr(self, "install_queue", None):
            self.status("All packages are ready.")
            return
        name = self.install_queue.pop(0)
        pane = self.packages_pane
        pane.write("$ pip install %s" % name)
        self.status("Installing %s ..." % name)

        def done(code):
            if code == 0:
                pane.make_blocks_for_dist(name, then=self.install_next)
            else:
                pane.write("Could not install %s (code %s)." % (name, code))
                self.install_next()
        self.packages.install(name, pane.write, done)

    def duplicate_file(self):
        f = self.current_file
        self.push_undo()
        clone = SpyFile.from_json(f.to_json())
        clone.name = self.project.unique_file_name(f.name)
        self.project.files.append(clone)
        self.file_index = len(self.project.files) - 1
        self.on_change()
        self.refresh_all()

    def delete_file(self):
        if len(self.project.files) <= 1:
            messagebox.showinfo("Delete file", "A project needs at least one file.")
            return
        f = self.current_file
        if not messagebox.askyesno("Delete file", "Delete %s.py?" % f.name):
            return
        self.push_undo()
        self.project.files.remove(f)
        self.file_index = max(0, self.file_index - 1)
        self.on_change()
        self.refresh_all()

    # -- variables, lists, blocks ------------------------------------------- #

    def ask_name(self, title: str, prompt: str) -> Optional[str]:
        name = simpledialog.askstring(title, prompt, parent=self.root)
        if not name:
            return None
        name = name.strip()
        if not name:
            return None
        return re.sub(r"[^0-9A-Za-z_ ]", "", name)[:32].strip() or None

    def make_variable(self, then: Optional[Callable[[str], None]] = None):
        name = self.ask_name("New variable", "Name of the new variable:")
        if not name:
            return
        if name not in self.project.variables:
            self.push_undo()
            self.project.variables.append(name)
            self.project.var_init.setdefault(name, "0")
            ensure_var_spec(name)
            self.on_change()
        self.palette.rebuild()
        self.files_pane.refresh()
        if then:
            then(name)

    def set_var_value(self):
        sel = self.files_pane.vars.curselection()
        if not sel:
            self.status("Pick a variable in the list first.")
            return
        name = self.project.variables[sel[0]]
        current = self.project.var_init.get(name, "0")
        value = simpledialog.askstring(
            "Start value",
            "What should %s be when the program starts?\n"
            "(a number, \"some text\", [] for a list...)" % name,
            initialvalue=current, parent=self.root)
        if value is None:
            return
        self.push_undo()
        self.project.var_init[name] = value.strip() or "0"
        self.on_change()

    def delete_variable(self):
        sel = self.files_pane.vars.curselection()
        if not sel:
            self.status("Pick a variable in the list first.")
            return
        name = self.project.variables[sel[0]]
        if not messagebox.askyesno("Delete variable", "Delete '%s'?" % name):
            return
        self.push_undo()
        self.project.variables.remove(name)
        self.project.var_init.pop(name, None)
        unregister(var_spec_id(name))
        self.on_change()
        self.refresh_all()

    def make_list(self, then: Optional[Callable[[str], None]] = None):
        name = self.ask_name("New list", "Name of the new list:")
        if not name:
            return
        if name not in self.project.lists:
            self.push_undo()
            self.project.lists.append(name)
            ensure_list_spec(name)
            self.on_change()
        self.palette.rebuild()
        self.files_pane.refresh()
        if then:
            then(name)

    def delete_list(self):
        sel = self.files_pane.lists.curselection()
        if not sel:
            self.status("Pick a list in the list box first.")
            return
        name = self.project.lists[sel[0]]
        if not messagebox.askyesno("Delete list", "Delete '%s'?" % name):
            return
        self.push_undo()
        self.project.lists.remove(name)
        unregister(list_spec_id(name))
        self.on_change()
        self.refresh_all()

    def make_function(self):
        dialog = FunctionDialog(self.root, self)
        self.root.wait_window(dialog.top)
        result = dialog.result
        if not result:
            return
        self.push_undo()
        existing = self.project.function_by_name(result["name"])
        if existing:
            existing.update(result)
        else:
            self.project.functions.append(result)
        ensure_func_specs(result)
        spec = SPECS.get("func::%s::def" % result["name"])
        if spec is not None:
            block = Block(spec)
            block.x, block.y = 90.0, 90.0
            used = {(round(s.x), round(s.y)) for s in self.current_file.scripts}
            while (round(block.x), round(block.y)) in used:
                block.x += 30
                block.y += 30
            self.current_file.scripts.append(block)
        self.categories.select("functions")
        self.on_change()
        self.refresh_all()

    def delete_function(self):
        sel = self.files_pane.funcs.curselection()
        if not sel:
            self.status("Pick a custom block first.")
            return
        fn = self.project.functions[sel[0]]
        if not messagebox.askyesno("Delete block",
                                   "Delete the block '%s'?" % fn["name"]):
            return
        self.push_undo()
        self.project.functions.remove(fn)
        unregister("func::%s::def" % fn["name"])
        unregister("func::%s" % fn["name"])
        for prm in fn.get("params", []):
            unregister("param::%s::%s" % (fn["name"], prm))
        self.on_change()
        self.refresh_all()

    # -- zoom --------------------------------------------------------------- #

    def zoom(self, delta: float):
        self.renderer.set_scale(self.renderer.scale + delta)
        self.zoom_label.configure(text="%d%%" % round(self.renderer.scale * 100))
        self.palette.rebuild()
        self.workspace.refresh()

    # -- saving and loading ------------------------------------------------- #

    def new_project(self):
        if not self.confirm_discard():
            return
        self.set_project(Project())
        self.status("New project.")

    def confirm_discard(self) -> bool:
        if not self.project.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Save changes?", "Save the changes to %s first?" % self.project.name)
        if answer is None:
            return False
        if answer:
            return bool(self.save())
        return True

    def open_project(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open a ScratchPy project", initialdir=self.project.folder(),
            filetypes=[("ScratchPy project", "*" + PROJECT_EXT),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            project = Project.from_json(data)
            project.path = path
            self.set_project(project)
            self.status("Opened %s" % os.path.basename(path))
        except Exception as exc:
            messagebox.showerror("Open project", "Could not open it:\n%s" % exc)

    def save(self):
        if not self.project.path:
            return self.save_as()
        self.workspace.close_editor(True)
        try:
            with open(self.project.path, "w", encoding="utf-8") as fh:
                json.dump(self.project.to_json(), fh, indent=1)
            self.project.dirty = False
            self.project_mtime = self.file_mtime(self.project.path)
            self.update_title()
            self.status("Saved %s" % os.path.basename(self.project.path))
            self.write_files()
            return True
        except Exception as exc:
            messagebox.showerror("Save", "Could not save:\n%s" % exc)
            return False

    def save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save project", defaultextension=PROJECT_EXT,
            initialdir=self.project.folder(),
            initialfile=self.project.name + PROJECT_EXT,
            filetypes=[("ScratchPy project", "*" + PROJECT_EXT)])
        if not path:
            return False
        self.project.path = path
        return self.save()

    def file_mtime(self, path: str) -> float:
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0.0

    def write_files(self) -> Dict[str, str]:
        """Write every tab out as a real .py file next to the project."""
        self.workspace.close_editor(True)
        folder = self.project.folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
        written: Dict[str, str] = {}
        for f in self.project.files:
            source = generate_file(self.project, f)
            path = os.path.join(folder, safe_module_name(f.name) + ".py")
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(source)
                written[f.name] = path
            except Exception as exc:
                self.console.write("err", "Could not write %s: %s" % (path, exc))
        return written

    def export_all(self):
        written = self.write_files()
        if written:
            self.status("Wrote %d Python file(s) to %s" %
                        (len(written), self.project.folder()))
            self.console.write("sys", "Saved: " + ", ".join(written.values()))
        return written

    def export_current(self):
        written = self.write_files()
        path = written.get(self.current_file.name)
        if path:
            self.status("Saved " + path)

    def open_folder(self):
        """Show the folder that the generated .py files are written into."""
        self.write_files()
        folder = self.project.folder()
        self.reveal(folder)
        self.status("Your Python files are in " + folder)

    def reveal(self, folder: str):
        try:
            os.makedirs(folder, exist_ok=True)
            if IS_WINDOWS:
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showinfo("Folder", folder + "\n\n" + str(exc))

    # -- running ------------------------------------------------------------ #

    def run(self):
        self.workspace.close_editor()
        if not CAN_RUN:
            self.console.write("err", NO_PYTHON_HINT)
            self.status("No Python interpreter found.")
            return
        written = self.write_files()
        path = written.get(self.current_file.name)
        if not path:
            self.console.write("err", "Could not write the Python file.")
            return
        source = generate_file(self.project, self.current_file)
        problem = check_syntax(source)
        if problem:
            self.console.write("err", "The generated code has a problem: %s"
                               % problem)
            self.console.write("sys", "Look at the Python code tab to see it.")
        self.console.write("sys", "--- running %s ---" % os.path.basename(path))
        self.flag_widget.configure(bg="#DFF5E1")
        self.status("Running...")
        self.runner.start(path, self.project.folder())

    def stop(self):
        if self.runner.running:
            self.runner.stop()
            self.status("Stopped.")

    def on_run_finished(self):
        self.flag_widget.configure(bg=UI["toolbar"])
        self.status("Finished.")

    def pump(self):
        """Runs on the main thread: console output and background callbacks."""
        try:
            for _ in range(400):
                tag, line = self.runner.q.get_nowait()
                self.console.write(tag, line)
        except queue.Empty:
            pass
        except Exception:
            pass
        while True:
            try:
                job = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                job()
            except Exception:
                self.console.write("err", traceback.format_exc(limit=3))
        now = time.time()
        if now - self._watch_tick > 1.5:
            self._watch_tick = now
            self.check_external_change()
        self.root.after(60, self.pump)

    # -- someone else edited the project (the MCP server, or an editor) ------ #

    def check_external_change(self):
        path = self.project.path
        if not path:
            return
        mtime = self.file_mtime(path)
        if not mtime:
            return
        if not self.project_mtime:
            self.project_mtime = mtime
            return
        if mtime <= self.project_mtime + 0.001:
            return
        self.project_mtime = mtime
        if self.project.dirty and not messagebox.askyesno(
                "The project changed",
                "%s was changed by something else (an AI assistant?).\n\n"
                "Load the new version? Your unsaved changes here would be lost."
                % os.path.basename(path)):
            return
        self.reload_project()

    def reload_project(self):
        path = self.project.path
        index = self.file_index
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            project = Project.from_json(data)
        except Exception as exc:
            self.status("Could not reload: %s" % exc)
            return
        project.path = path
        self.project = project
        self.renderer.project = project
        self.file_index = min(index, len(project.files) - 1)
        self.project.dirty = False
        self.refresh_all()
        self.console.write("sys", "--- reloaded %s (changed outside the "
                                  "editor) ---" % os.path.basename(path))
        self.status("Reloaded after an outside change.")

    def show_mcp(self):
        MCPDialog(self.root, self)

    # -- help --------------------------------------------------------------- #

    def help_guide(self):
        messagebox.showinfo(
            "Quick guide",
            "1. Drag blocks from the left onto the canvas.\n"
            "2. Snap them under a 'when green flag clicked' hat.\n"
            "3. Click a white oval to type a value, or drop a round\n"
            "   block into it.\n"
            "4. Press the green flag to run. Output appears in the\n"
            "   console at the bottom, and the real Python is in the\n"
            "   'Python code' tab.\n"
            "5. 'Code folder' at the top opens the folder those .py\n"
            "   files are written into.\n\n"
            "Bringing code in\n"
            "   'Import .py' turns any Python file into blocks, and\n"
            "   offers to install the packages it needs.\n\n"
            "Packages\n"
            "   The Packages tab installs anything from PyPI and turns\n"
            "   it into blocks, each library in its own colour. Settings\n"
            "   can keep them in a venv beside your project.\n\n"
            "AI\n"
            "   The AI menu shows how to connect an assistant over MCP.\n\n"
            "Drag a block back onto the palette to delete it.\n"
            "Right click the canvas for clean up and delete options.")

    def about(self):
        messagebox.showinfo(
            "About " + APP_NAME,
            "%s %s\n\nA Scratch style editor that writes real Python.\n"
            "Everything lives in one file, using nothing but the\n"
            "Python standard library.\n\nRunning on Python %s" %
            (APP_NAME, APP_VERSION, platform.python_version()))

    def on_close(self):
        self.workspace.close_editor(True)
        if self.runner.running:
            self.runner.stop()
        if self.settings.get("autosave") and self.project.path and \
                self.project.dirty:
            self.autosave()
        self.settings.set("zoom", round(self.renderer.scale, 2))
        if not self.confirm_discard():
            return
        self.root.destroy()


class MCPDialog:
    """Shows how to plug an AI assistant into this project."""

    def __init__(self, parent, app: "App"):
        self.app = app
        top = self.top = tk.Toplevel(parent)
        top.title("Connect an AI assistant")
        top.configure(bg=UI["panel"])
        top.transient(parent)
        top.grab_set()

        tk.Label(top, text="Let an AI build blocks with you", bg=UI["panel"],
                 fg=UI["text"], font=(FONT_FAMILY, 14, "bold")).pack(
                     padx=22, pady=(16, 2), anchor="w")
        tk.Label(top, text="ScratchPy can act as an MCP server. An assistant "
                           "that speaks MCP\n(Claude Desktop, Claude Code and "
                           "others) can then read your blocks,\nwrite new ones "
                           "from Python, install packages and run your program.",
                 bg=UI["panel"], fg="#8A93A5", justify="left",
                 font=(FONT_FAMILY, 9)).pack(padx=22, anchor="w")

        saved = app.project.path
        if not saved:
            tk.Label(top, text="Save your project first so the assistant has a "
                               "file to work on.",
                     bg="#FFF4E0", fg="#9A6B00", font=(FONT_FAMILY, 9, "bold"),
                     anchor="w").pack(fill="x", padx=22, pady=8, ipady=6)

        tk.Label(top, text="Configuration", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 10, "bold")).pack(padx=22, pady=(12, 2),
                                                      anchor="w")
        self.text = tk.Text(top, width=58, height=12, font=(MONO_FAMILY, 9),
                            bg="#1E2430", fg="#DDE3EC", relief="flat",
                            padx=10, pady=8, wrap="char",
                            highlightthickness=0)
        self.text.pack(padx=22, fill="both", expand=True)
        self.text.insert("1.0", mcp_config_snippet(saved))
        self.text.configure(state="disabled")

        tk.Label(top, text="Or from a terminal:", bg=UI["panel"],
                 fg=UI["text"], font=(FONT_FAMILY, 9, "bold")).pack(
                     padx=22, pady=(10, 0), anchor="w")
        command = "%s %s --mcp %s" % (
            os.path.basename(sys.executable),
            "" if FROZEN else os.path.basename(os.path.abspath(__file__)),
            saved or "yourproject.spy")
        tk.Label(top, text="  " + " ".join(command.split()), bg="#F2F4F8",
                 fg="#3A4356", font=(MONO_FAMILY, 9), anchor="w",
                 justify="left", wraplength=520).pack(
                     fill="x", padx=22, pady=(2, 0), ipady=6)
        tk.Label(top, text="While it is connected, anything the assistant "
                           "changes appears here\nwithin a second or two.",
                 bg=UI["panel"], fg="#8A93A5", justify="left",
                 font=(FONT_FAMILY, 8)).pack(padx=22, pady=(8, 0), anchor="w")

        row = tk.Frame(top, bg=UI["panel"])
        row.pack(pady=14)
        tk.Button(row, text="Copy the configuration", command=self.copy,
                  relief="flat", bd=0, bg=UI["accent"], fg="#FFFFFF",
                  activebackground="#3373CC", activeforeground="#FFFFFF",
                  font=(FONT_FAMILY, 9, "bold"), padx=18, pady=6,
                  cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text="Close", command=top.destroy, relief="flat", bd=0,
                  bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 9), padx=18,
                  pady=6, cursor="hand2").pack(side="left", padx=6)
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            top.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() -
                                        top.winfo_width()) // 2
            y = parent.winfo_rooty() + 70
            top.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass

    def copy(self):
        self.top.clipboard_clear()
        self.top.clipboard_append(self.text.get("1.0", "end-1c"))
        self.app.status("MCP configuration copied to the clipboard.")


class SettingsDialog:
    """Preferences, including the virtual environment switch."""

    def __init__(self, parent, app: "App"):
        self.app = app
        top = self.top = tk.Toplevel(parent)
        top.title("Settings")
        top.configure(bg=UI["panel"])
        top.transient(parent)
        top.resizable(False, False)
        top.grab_set()

        tk.Label(top, text="Settings", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 14, "bold")).pack(padx=24, pady=(18, 2),
                                                      anchor="w")
        tk.Label(top, text="These are remembered for next time.",
                 bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 9)).pack(padx=24, anchor="w")

        box = tk.LabelFrame(top, text=" Virtual environment (venv) ",
                            bg=UI["panel"], fg=UI["text"], bd=1,
                            relief="solid", font=(FONT_FAMILY, 9, "bold"),
                            labelanchor="nw")
        box.pack(fill="x", padx=24, pady=(14, 6), ipady=6)

        self.use_venv = tk.BooleanVar(value=bool(app.settings.get("use_venv")))
        tk.Checkbutton(box, text="Keep this project's packages in a venv",
                       variable=self.use_venv, bg=UI["panel"], fg=UI["text"],
                       activebackground=UI["panel"], bd=0, highlightthickness=0,
                       font=(FONT_FAMILY, 10), command=self.refresh).pack(
                           anchor="w", padx=10, pady=(6, 0))
        tk.Label(box, text="A venv keeps pip installs beside your project "
                           "instead of\nchanging the Python installed on this "
                           "computer.",
                 bg=UI["panel"], fg="#8A93A5", justify="left",
                 font=(FONT_FAMILY, 8)).pack(anchor="w", padx=32)

        row = tk.Frame(box, bg=UI["panel"])
        row.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(row, text="Folder", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9)).pack(side="left")
        self.venv_dir = tk.StringVar(value=str(app.settings.get("venv_dir")))
        tk.Entry(row, textvariable=self.venv_dir, bd=0, relief="flat",
                 font=(FONT_FAMILY, 9), bg="#FFFFFF", highlightthickness=1,
                 highlightbackground=UI["border"]).pack(side="left", fill="x",
                                                        expand=True, padx=8,
                                                        ipady=3)
        tk.Button(row, text="Browse", command=self.browse, relief="flat", bd=0,
                  bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 8),
                  cursor="hand2", padx=8).pack(side="left")

        row2 = tk.Frame(box, bg=UI["panel"])
        row2.pack(fill="x", padx=10, pady=(0, 4))
        self.make_btn = tk.Button(row2, text="Create the venv now",
                                  command=self.create, relief="flat", bd=0,
                                  bg=CATS["packages"]["color"], fg="#FFFFFF",
                                  activebackground=CATS["packages"]["dark"],
                                  activeforeground="#FFFFFF", cursor="hand2",
                                  font=(FONT_FAMILY, 9, "bold"), padx=12,
                                  pady=3)
        self.make_btn.pack(side="left")
        tk.Button(row2, text="Open the folder", command=self.open_venv,
                  relief="flat", bd=0, bg="#EEF1F6", fg=UI["text"],
                  font=(FONT_FAMILY, 8), cursor="hand2",
                  padx=8).pack(side="left", padx=6)

        self.state = tk.Label(box, text="", bg=UI["panel"], fg="#8A93A5",
                              font=(FONT_FAMILY, 8), justify="left",
                              wraplength=420)
        self.state.pack(anchor="w", padx=10, pady=(4, 2))

        box2 = tk.LabelFrame(top, text=" While you work ", bg=UI["panel"],
                             fg=UI["text"], bd=1, relief="solid",
                             font=(FONT_FAMILY, 9, "bold"), labelanchor="nw")
        box2.pack(fill="x", padx=24, pady=6, ipady=6)
        self.autosave = tk.BooleanVar(value=bool(app.settings.get("autosave")))
        tk.Checkbutton(box2, text="Save the project automatically",
                       variable=self.autosave, bg=UI["panel"], fg=UI["text"],
                       activebackground=UI["panel"], bd=0, highlightthickness=0,
                       font=(FONT_FAMILY, 10)).pack(anchor="w", padx=10,
                                                    pady=(6, 0))
        self.confirm = tk.BooleanVar(
            value=bool(app.settings.get("confirm_uninstall")))
        tk.Checkbutton(box2, text="Ask before uninstalling a package",
                       variable=self.confirm, bg=UI["panel"], fg=UI["text"],
                       activebackground=UI["panel"], bd=0, highlightthickness=0,
                       font=(FONT_FAMILY, 10)).pack(anchor="w", padx=10)

        buttons = tk.Frame(top, bg=UI["panel"])
        buttons.pack(pady=(10, 18))
        tk.Button(buttons, text="Cancel", command=top.destroy, relief="flat",
                  bd=0, bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 9),
                  padx=16, pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(buttons, text="Save", command=self.ok, relief="flat", bd=0,
                  bg=UI["accent"], fg="#FFFFFF", activebackground="#3373CC",
                  activeforeground="#FFFFFF", font=(FONT_FAMILY, 9, "bold"),
                  padx=26, pady=6, cursor="hand2").pack(side="left", padx=6)

        self.refresh()
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            top.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() -
                                        top.winfo_width()) // 2
            top.geometry("+%d+%d" % (max(0, x), parent.winfo_rooty() + 90))
        except Exception:
            pass

    # -- helpers ------------------------------------------------------------ #

    def folder(self) -> str:
        where = self.venv_dir.get().strip() or ".venv"
        if os.path.isabs(where):
            return where
        return os.path.join(self.app.project.folder(), where)

    def refresh(self):
        exists = os.path.exists(venv_python_path(self.folder()))
        if not self.use_venv.get():
            text = "Off. Packages are installed into %s" % (
                PYTHON_EXE or "the Python built into this app")
        elif exists:
            text = "Ready: " + venv_python_path(self.folder())
        else:
            text = ("Not made yet - press 'Create the venv now'. "
                    "It will appear at " + self.folder())
        self.state.configure(text=text)
        self.make_btn.configure(text="Repair the venv" if exists
                                else "Create the venv now")

    def browse(self):
        chosen = filedialog.askdirectory(title="Where should the venv live?",
                                         parent=self.top)
        if chosen:
            self.venv_dir.set(chosen)
            self.refresh()

    def open_venv(self):
        folder = self.folder()
        if not os.path.isdir(folder):
            messagebox.showinfo("venv", "There is no venv there yet.",
                                parent=self.top)
            return
        self.app.reveal(folder)

    def create(self):
        folder = self.folder()
        self.app.show_tab(2)
        self.app.packages_pane.write("Creating a virtual environment...")

        def done(code):
            self.app.packages_pane.write(
                "venv ready." if code == 0 else "venv failed (code %s)." % code)
            try:
                self.refresh()
            except Exception:
                pass
            self.app.packages_pane.refresh_list()
        self.app.packages.make_venv(folder, self.app.packages_pane.write, done)

    def ok(self):
        s = self.app.settings
        s.set("use_venv", bool(self.use_venv.get()))
        s.set("venv_dir", self.venv_dir.get().strip() or ".venv")
        s.set("autosave", bool(self.autosave.get()))
        s.set("confirm_uninstall", bool(self.confirm.get()))
        self.top.destroy()
        if s.get("use_venv") and not self.app.venv_ready():
            if messagebox.askyesno(
                    "Virtual environment",
                    "The venv does not exist yet. Create it now?"):
                self.app.show_tab(2)
                self.app.packages_pane.write("Creating a virtual environment...")
                self.app.packages.make_venv(
                    self.app.venv_folder(), self.app.packages_pane.write,
                    lambda code: self.app.packages_pane.refresh_list())
        self.app.status("Using " + self.app.interpreter_label())
        self.app.packages_pane.refresh_interpreter()
        self.app.packages_pane.refresh_list()


class FunctionDialog:
    """'Make a Block' - name, parameters and whether it reports a value."""

    def __init__(self, parent, app: "App"):
        self.app = app
        self.result: Optional[dict] = None
        top = self.top = tk.Toplevel(parent)
        top.title("Make a Block")
        top.configure(bg=UI["panel"])
        top.transient(parent)
        top.resizable(False, False)
        top.grab_set()

        tk.Label(top, text="Make a Block", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 13, "bold")).pack(padx=20, pady=(16, 4))
        tk.Label(top, text="Give your block a name and, if you like, some\n"
                           "inputs. It becomes a real Python function.",
                 bg=UI["panel"], fg="#8A93A5",
                 font=(FONT_FAMILY, 9)).pack(padx=20)

        body = tk.Frame(top, bg=UI["panel"])
        body.pack(padx=20, pady=12, fill="x")
        tk.Label(body, text="Block name", bg=UI["panel"], fg=UI["text"],
                 font=(FONT_FAMILY, 9)).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value="my block")
        tk.Entry(body, textvariable=self.name_var, bd=0, relief="flat",
                 font=(FONT_FAMILY, 10), bg="#FFFFFF", highlightthickness=1,
                 highlightbackground=UI["border"], width=28).grid(
                     row=0, column=1, padx=8, pady=4, ipady=3)
        tk.Label(body, text="Inputs (comma separated)", bg=UI["panel"],
                 fg=UI["text"], font=(FONT_FAMILY, 9)).grid(row=1, column=0,
                                                            sticky="w")
        self.params_var = tk.StringVar(value="")
        tk.Entry(body, textvariable=self.params_var, bd=0, relief="flat",
                 font=(FONT_FAMILY, 10), bg="#FFFFFF", highlightthickness=1,
                 highlightbackground=UI["border"], width=28).grid(
                     row=1, column=1, padx=8, pady=4, ipady=3)
        self.returns_var = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text="This block reports a value",
                       variable=self.returns_var, bg=UI["panel"],
                       fg=UI["text"], activebackground=UI["panel"],
                       font=(FONT_FAMILY, 9), bd=0,
                       highlightthickness=0).grid(row=2, column=1, sticky="w",
                                                  pady=6)

        row = tk.Frame(top, bg=UI["panel"])
        row.pack(pady=(0, 16))
        tk.Button(row, text="Cancel", command=self.cancel, relief="flat", bd=0,
                  bg="#EEF1F6", fg=UI["text"], font=(FONT_FAMILY, 9),
                  padx=16, pady=6, cursor="hand2").pack(side="left", padx=6)
        tk.Button(row, text="OK", command=self.ok, relief="flat", bd=0,
                  bg=CATS["functions"]["color"], fg="#FFFFFF",
                  activebackground=CATS["functions"]["dark"],
                  activeforeground="#FFFFFF", font=(FONT_FAMILY, 9, "bold"),
                  padx=22, pady=6, cursor="hand2").pack(side="left", padx=6)
        top.bind("<Return>", lambda e: self.ok())
        top.bind("<Escape>", lambda e: self.cancel())
        try:
            top.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() - top.winfo_width()) // 2
            y = parent.winfo_rooty() + 160
            top.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass

    def ok(self):
        name = re.sub(r"[^0-9A-Za-z_ ]", "", self.name_var.get()).strip()
        if not name:
            messagebox.showwarning("Make a Block", "Please type a name.",
                                   parent=self.top)
            return
        params = []
        for part in self.params_var.get().split(","):
            part = re.sub(r"[^0-9A-Za-z_ ]", "", part).strip()
            if part:
                params.append(part)
        self.result = {"name": name, "params": params,
                       "returns": bool(self.returns_var.get())}
        self.top.destroy()

    def cancel(self):
        self.result = None
        self.top.destroy()


# =========================================================================== #
#  SECTION 14 - the example project you see on the very first run
# =========================================================================== #

def mk(bid: str, values: Optional[dict] = None,
       fields: Optional[dict] = None) -> Block:
    b = Block(SPECS[bid])
    for k, v in (values or {}).items():
        if isinstance(v, Block):
            b.attach_slot(k, v)
        else:
            b.values[k] = v
    for k, v in (fields or {}).items():
        b.fields[k] = v
    return b


def link(*blocks: Block) -> Block:
    """Stack blocks under each other and return the first one."""
    for a, b in zip(blocks, blocks[1:]):
        a.last().attach_next(b)
    return blocks[0]


def demo_project() -> Project:
    p = Project()
    p.variables = ["score", "name"]
    p.var_init = {"score": "0", "name": '""'}
    p.lists = ["things"]
    p.messages = ["message1"]
    p.sync_specs()

    hello = mk("text_print", {"msg": "Hello! Drag some blocks around."})
    ask = mk("text_ask", {"prompt": "What is your name?"})
    set_name = mk("var_set", {"val": ask}, {"var": "name"})
    greet = mk("text_fprint", {"msg": "Nice to meet you, {name}!"})
    reset = mk("var_set", {"val": "0"}, {"var": "score"})

    loop = mk("control_repeat", {"times": "3"})
    loop.attach_branch(0, link(
        mk("var_change", {"val": "1"}, {"var": "score"}),
        mk("text_fprint", {"msg": "Round {score} of 3"}),
        mk("control_wait", {"secs": "0.3"}),
    ))

    test = mk("op_compare", {"a": mk(var_spec_id("score")), "b": "2"},
              {"op": ">"})
    check = mk("control_if", {"cond": test})
    check.attach_branch(0, mk("text_print", {"msg": "That is a high score!"}))

    hat = mk("event_start")
    link(hat, hello, set_name, greet, reset, loop, check)
    hat.x, hat.y = 90.0, 70.0
    p.files[0].scripts.append(hat)

    note = mk("py_comment", {"text": "drag me under the hat to use me"})
    note.x, note.y = 520.0, 470.0
    p.files[0].scripts.append(note)

    p.dirty = False
    return p


# =========================================================================== #
#  SECTION 14b - the MCP server: let an AI assistant use ScratchPy
# =========================================================================== #
#
#  Started with:   python scratchpy_studio.py --mcp [project.spy]
#
#  It speaks the Model Context Protocol over stdin/stdout (newline delimited
#  JSON-RPC), so Claude Desktop, Claude Code and anything else that speaks MCP
#  can look at a project, add blocks by writing Python, read the generated
#  code and run it.  The editor notices when the file changes underneath it and
#  reloads, so you can watch the AI work in real time.
# =========================================================================== #

MCP_PROTOCOL = "2024-11-05"


def describe_row(block: Block, row) -> str:
    """One line of a block, with the values that are really in it."""
    parts = []
    for tok in row:
        if tok[0] == "t":
            parts.append(tok[1])
        elif tok[0] == "i":
            value = block.values.get(tok[1])
            if isinstance(value, Block):
                parts.append("(" + describe_block(value) + ")")
            else:
                parts.append("[%s]" % short_code(str(value or ""), 40))
        else:
            chosen = str(block.fields.get(tok[1], ""))
            parts.append(chosen if ("<" in chosen or ">" in chosen)
                         else "<%s>" % chosen)
    return " ".join(parts)


def describe_block(block: Block) -> str:
    return " ".join(describe_row(block, row) for row in block.spec.rows)


def mcp_schema(props: dict, required: Optional[List[str]] = None) -> dict:
    return {"type": "object", "properties": props,
            "required": required or []}


class MCPServer:
    """A small, dependency free MCP server around a ScratchPy project."""

    def __init__(self, path: Optional[str] = None):
        self.path = os.path.abspath(path) if path else None
        self.project = Project()
        if self.path and os.path.exists(self.path):
            self.load()
        elif self.path:
            self.save()

    # -- project on disk ---------------------------------------------------- #

    def load(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            self.project = Project.from_json(json.load(fh))
        self.project.path = self.path

    def save(self):
        if not self.path:
            raise RuntimeError("No project is open. Call open_project first.")
        self.project.path = self.path
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.project.to_json(), fh, indent=1)
        folder = self.project.folder()
        os.makedirs(folder, exist_ok=True)
        written = []
        for spyfile in self.project.files:
            code = generate_file(self.project, spyfile)
            target = os.path.join(folder, safe_module_name(spyfile.name) + ".py")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(code)
            written.append(target)
        return written

    def python(self) -> str:
        """Respect the venv switch, exactly like the editor does."""
        try:
            settings = Settings()
            if settings.get("use_venv"):
                exe = venv_python_path(
                    venv_folder_for(settings, self.project.folder()))
                if os.path.exists(exe):
                    return exe
        except Exception:
            pass
        return PYTHON_EXE

    def need_file(self, name: Optional[str]) -> SpyFile:
        if not self.project.files:
            self.project.files.append(SpyFile("main"))
        if not name:
            return self.project.files[0]
        found = self.project.file_by_name(safe_module_name(name))
        if found is None:
            raise RuntimeError("There is no tab called %r. Tabs: %s"
                               % (name, ", ".join(f.name for f in
                                                  self.project.files)))
        return found

    # -- the tools ---------------------------------------------------------- #

    def tools(self) -> List[dict]:
        return [
            {"name": "open_project",
             "description": "Open (or create) a ScratchPy .spy project file and "
                            "make it the one every other tool works on.",
             "inputSchema": mcp_schema(
                 {"path": {"type": "string",
                           "description": "Full path to a .spy file."}},
                 ["path"])},
            {"name": "project_overview",
             "description": "What is in the project: tabs, variables, lists, "
                            "custom blocks and package block packs.",
             "inputSchema": mcp_schema({})},
            {"name": "read_blocks",
             "description": "A readable outline of every script in a tab, so you "
                            "can see how the blocks are arranged.",
             "inputSchema": mcp_schema(
                 {"file": {"type": "string",
                           "description": "Tab name. Defaults to the first."}})},
            {"name": "read_code",
             "description": "The Python that a tab's blocks generate.",
             "inputSchema": mcp_schema(
                 {"file": {"type": "string"}})},
            {"name": "write_python",
             "description": "THE MAIN TOOL FOR BUILDING. Give it ordinary Python "
                            "and it is converted into Scratch style blocks in a "
                            "new or replaced tab. Anything unusual is preserved "
                            "verbatim inside code blocks.",
             "inputSchema": mcp_schema(
                 {"source": {"type": "string",
                             "description": "The Python program."},
                  "file": {"type": "string",
                           "description": "Tab name to create or replace."},
                  "replace": {"type": "boolean",
                              "description": "Replace a tab of the same name "
                                             "instead of making a new one."}},
                 ["source"])},
            {"name": "import_python_file",
             "description": "Turn an existing .py file on disk into blocks.",
             "inputSchema": mcp_schema(
                 {"path": {"type": "string"}}, ["path"])},
            {"name": "delete_file",
             "description": "Remove a tab from the project.",
             "inputSchema": mcp_schema({"file": {"type": "string"}}, ["file"])},
            {"name": "set_variable",
             "description": "Create a variable or list, or change its starting "
                            "value.",
             "inputSchema": mcp_schema(
                 {"name": {"type": "string"},
                  "value": {"type": "string",
                            "description": "Python literal for the start value, "
                                           "e.g. 0 or \"hello\"."},
                  "kind": {"type": "string", "enum": ["variable", "list"]}},
                 ["name"])},
            {"name": "run",
             "description": "Run a tab's generated Python and return what it "
                            "printed.",
             "inputSchema": mcp_schema(
                 {"file": {"type": "string"},
                  "stdin": {"type": "string",
                            "description": "Text to feed to input()."},
                  "timeout": {"type": "number"}})},
            {"name": "list_block_types",
             "description": "Every kind of block ScratchPy knows, with the "
                            "Python each one produces. Useful for knowing what "
                            "will convert cleanly.",
             "inputSchema": mcp_schema(
                 {"category": {"type": "string",
                               "description": "events, control, operators, "
                                              "text, variables, lists, files, "
                                              "functions, python, packages"}})},
            {"name": "list_packages",
             "description": "Python packages installed in the environment "
                            "ScratchPy is using.",
             "inputSchema": mcp_schema({})},
            {"name": "install_package",
             "description": "pip install a package and turn it into blocks.",
             "inputSchema": mcp_schema(
                 {"name": {"type": "string"}}, ["name"])},
            {"name": "add_package_blocks",
             "description": "Make blocks for a module that is already installed "
                            "(works for standard library modules too).",
             "inputSchema": mcp_schema(
                 {"module": {"type": "string"}}, ["module"])},
            {"name": "remove_package_blocks",
             "description": "Take a package's blocks back out of the project.",
             "inputSchema": mcp_schema(
                 {"module": {"type": "string"}}, ["module"])},
        ]

    def call(self, name: str, args: dict) -> str:
        handler = getattr(self, "tool_" + name, None)
        if handler is None:
            raise RuntimeError("Unknown tool: " + name)
        return handler(args or {})

    # -- tool implementations ----------------------------------------------- #

    def tool_open_project(self, args: dict) -> str:
        self.path = os.path.abspath(args["path"])
        if os.path.exists(self.path):
            self.load()
            return "Opened %s with %d tab(s)." % (self.path,
                                                  len(self.project.files))
        self.project = Project()
        self.save()
        return "Created a new project at " + self.path

    def tool_project_overview(self, args: dict) -> str:
        p = self.project
        lines = ["project: %s" % (self.path or "(not saved yet)"),
                 "folder for the generated .py files: %s" % p.folder(), ""]
        for spyfile in p.files:
            scripts = len(spyfile.scripts)
            blocks = sum(1 for s in spyfile.scripts for _ in s.descendants())
            lines.append("tab %-16s %d script(s), %d block(s)"
                         % (spyfile.name, scripts, blocks))
        lines.append("")
        lines.append("variables: " + (", ".join(
            "%s=%s" % (v, p.var_init.get(v, "0")) for v in p.variables) or "-"))
        lines.append("lists: " + (", ".join(p.lists) or "-"))
        lines.append("custom blocks: " + (", ".join(
            "%s(%s)" % (f["name"], ", ".join(f.get("params", [])))
            for f in p.functions) or "-"))
        lines.append("package blocks: " + (", ".join(
            "%s (%d)" % (pack.get("module"), len(pack.get("blocks", [])))
            for pack in p.packs) or "-"))
        return "\n".join(lines)

    def outline(self, block: Optional[Block], depth: int, out: List[str]):
        current = block
        pad = "    " * depth
        while current is not None:
            rows = current.spec.rows or [[]]
            for index, row in enumerate(rows):
                out.append(pad + describe_row(current, row))
                if index < len(current.branches):
                    child = current.branches[index]
                    if child is not None:
                        self.outline(child, depth + 1, out)
                    else:
                        out.append(pad + "    (nothing in here yet)")
            if current.spec.is_c:
                out.append(pad + "end")
            current = current.next

    def tool_read_blocks(self, args: dict) -> str:
        spyfile = self.need_file(args.get("file"))
        out = ["tab: " + spyfile.name]
        for imp in spyfile.header_imports:
            out.append("module import: " + imp)
        for index, script in enumerate(spyfile.scripts, start=1):
            out.append("")
            out.append("script %d at (%d, %d):" % (index, script.x, script.y))
            self.outline(script, 1, out)
        if spyfile.header_code:
            out.append("")
            out.append("plain Python kept at the top of the file: %d chunk(s)"
                       % len(spyfile.header_code))
        return "\n".join(out)

    def tool_read_code(self, args: dict) -> str:
        spyfile = self.need_file(args.get("file"))
        return generate_file(self.project, spyfile)

    def tool_write_python(self, args: dict) -> str:
        source = args["source"]
        name = safe_module_name(args.get("file") or "main")
        existing = self.project.file_by_name(name)
        if existing is not None and args.get("replace", True):
            self.project.files.remove(existing)
        elif existing is not None:
            name = self.project.unique_file_name(name)
        spyfile, importer = import_python_source(self.project, source, name)
        self.project.files.append(spyfile)
        self.save()
        blocks = sum(1 for s in spyfile.scripts for _ in s.descendants())
        note = ""
        if importer.notes:
            note = "\nnotes: " + "; ".join(dedupe(importer.notes)[:5])
        packages = third_party_packages(importer.packages)
        if packages:
            note += "\npackages this needs: " + ", ".join(packages)
        return ("Built %d blocks in the '%s' tab (%d chunks kept as plain "
                "Python).%s" % (blocks, spyfile.name, importer.raw, note))

    def tool_import_python_file(self, args: dict) -> str:
        path = args["path"]
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        return self.tool_write_python(
            {"source": source,
             "file": os.path.splitext(os.path.basename(path))[0]})

    def tool_delete_file(self, args: dict) -> str:
        spyfile = self.need_file(args["file"])
        self.project.files.remove(spyfile)
        if not self.project.files:
            self.project.files.append(SpyFile("main"))
        self.save()
        return "Removed the tab " + spyfile.name

    def tool_set_variable(self, args: dict) -> str:
        name = str(args["name"]).strip()
        kind = args.get("kind", "variable")
        if kind == "list":
            if name not in self.project.lists:
                self.project.lists.append(name)
            ensure_list_spec(name)
        else:
            if name not in self.project.variables:
                self.project.variables.append(name)
            self.project.var_init[name] = str(args.get("value", "0")) or "0"
            ensure_var_spec(name)
        self.save()
        return "%s %s is ready." % (kind, name)

    def tool_run(self, args: dict) -> str:
        spyfile = self.need_file(args.get("file"))
        self.save()
        folder = self.project.folder()
        target = os.path.join(folder, safe_module_name(spyfile.name) + ".py")
        command = run_command(target, self.python())
        if not command:
            return "No Python interpreter is available to run this."
        kw = dict(capture_output=True, text=True, cwd=folder,
                  timeout=float(args.get("timeout") or 30),
                  input=args.get("stdin") or "")
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        try:
            res = subprocess.run(command, **kw)
        except subprocess.TimeoutExpired:
            return "The program was still running after the time limit."
        out = (res.stdout or "").rstrip()
        err = (res.stderr or "").rstrip()
        parts = ["exit code %d" % res.returncode]
        if out:
            parts.append("output:\n" + out)
        if err:
            parts.append("errors:\n" + err)
        return "\n\n".join(parts)

    def tool_list_block_types(self, args: dict) -> str:
        wanted = args.get("category")
        out = []
        for category in CAT_ORDER:
            if wanted and category != wanted:
                continue
            ids = PALETTE.get(category, [])
            if not ids:
                continue
            out.append("== %s ==" % CATS[category]["name"])
            for bid in ids:
                spec = SPECS.get(bid)
                if spec is None:
                    continue
                code = spec.code.replace("\n", " ; ")
                out.append("  %-22s %-46s -> %s"
                           % (bid, spec.raw_rows[0][:46], code[:70]))
        return "\n".join(out) or "nothing here"

    def tool_list_packages(self, args: dict) -> str:
        if not PYTHON_EXE:
            return "No Python interpreter was found."
        kw = dict(capture_output=True, text=True, timeout=120)
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        res = subprocess.run([self.python(), "-m", "pip", "list",
                              "--disable-pip-version-check"], **kw)
        return (res.stdout or res.stderr or "").strip()

    def tool_install_package(self, args: dict) -> str:
        name = args["name"]
        if not PYTHON_EXE:
            return "No Python interpreter was found, so pip cannot run."
        kw = dict(capture_output=True, text=True, timeout=900)
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        res = subprocess.run([self.python(), "-m", "pip", "install",
                              "--disable-pip-version-check", "--no-input",
                              name], **kw)
        tail = (res.stdout or "").strip().split("\n")[-4:]
        if res.returncode != 0:
            return "pip failed:\n" + (res.stderr or res.stdout or "")[-1500:]
        module = IMPORT_ALIASES.get(name.lower(), name.replace("-", "_"))
        blocks = self.tool_add_package_blocks({"module": module})
        return "\n".join(tail) + "\n" + blocks

    def tool_add_package_blocks(self, args: dict) -> str:
        module = args["module"]
        if not PYTHON_EXE:
            return "No Python interpreter was found."
        kw = dict(capture_output=True, text=True, timeout=300)
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        res = subprocess.run([self.python(), "-c", INTROSPECT_SRC, module], **kw)
        data = {}
        for line in reversed((res.stdout or "").strip().split("\n")):
            if line.strip().startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
        if not data or (data.get("error") and
                        module.split(".")[0] not in CURATED):
            return "Could not read the module %s. %s" % (module,
                                                         data.get("error", ""))
        curated = CURATED.get(module.split(".")[0], [])
        auto = blocks_from_introspection(data)
        seen, blocks = set(), []
        for b in list(curated) + auto:
            if b["id"] not in seen:
                seen.add(b["id"])
                blocks.append(b)
        taken = [p.get("color", "") for p in self.project.packs]
        color, dark = pick_pack_color(module, taken)
        pack = {"module": module, "dist": module,
                "version": data.get("version", ""), "blocks": blocks,
                "color": color, "dark": dark}
        for old in list(self.project.packs):
            if old.get("module") == module:
                unregister_pack(old)
                self.project.packs.remove(old)
        self.project.packs.append(pack)
        register_pack(pack)
        try:
            os.makedirs(PACK_DIR, exist_ok=True)
            with open(pack_cache_path(module), "w", encoding="utf-8") as fh:
                json.dump(pack, fh, indent=1)
        except Exception:
            pass
        self.save()
        return "Added %d blocks for %s." % (len(blocks), module)

    def tool_remove_package_blocks(self, args: dict) -> str:
        module = args["module"]
        for pack in list(self.project.packs):
            if pack.get("module") == module or pack.get("dist") == module:
                unregister_pack(pack)
                forget_cached_pack(pack.get("module", ""))
                self.project.packs.remove(pack)
                self.save()
                return "Removed the blocks for " + module
        return "There are no blocks for " + module

    # -- the protocol ------------------------------------------------------- #

    def handle(self, message: dict) -> Optional[dict]:
        method = message.get("method", "")
        mid = message.get("id")
        params = message.get("params") or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        if method == "initialize":
            return ok({"protocolVersion": MCP_PROTOCOL,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "scratchpy-studio",
                                      "version": APP_VERSION}})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": self.tools()})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                text = self.call(name, args)
                return ok({"content": [{"type": "text", "text": str(text)}],
                           "isError": False})
            except Exception as exc:
                detail = "%s: %s" % (type(exc).__name__, exc)
                return ok({"content": [{"type": "text", "text": detail}],
                           "isError": True})
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "Unknown method " + method}}

    def serve(self, stream_in=None, stream_out=None) -> int:
        stream_in = stream_in or sys.stdin
        stream_out = stream_out or sys.stdout
        for line in stream_in:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except Exception:
                continue
            try:
                reply = self.handle(message)
            except Exception:
                reply = {"jsonrpc": "2.0", "id": message.get("id"),
                         "error": {"code": -32603,
                                   "message": traceback.format_exc(limit=3)}}
            if reply is not None:
                stream_out.write(json.dumps(reply) + "\n")
                stream_out.flush()
        return 0


def mcp_config_snippet(project_path: Optional[str] = None) -> str:
    """The lines to paste into an MCP client's configuration."""
    command = sys.executable if not FROZEN else sys.executable
    args = [os.path.abspath(__file__)] if not FROZEN else []
    args.append("--mcp")
    if project_path:
        args.append(project_path)
    return json.dumps({"mcpServers": {"scratchpy": {
        "command": command, "args": args}}}, indent=2)


# =========================================================================== #
#  SECTION 15 - the application icon, drawn from scratch (no image libraries)
# =========================================================================== #
#
#  The logo is three snapped-together Scratch blocks on a purple to blue
#  rounded square.  It is drawn with the very same outline code that draws the
#  blocks in the editor, then written out as .png, .ico and .icns.
# =========================================================================== #

import zlib
import struct

ICON_BG_TOP = (0x8B, 0x5C, 0xF6)
ICON_BG_BOTTOM = (0x4C, 0x97, 0xFF)
ICON_BLOCKS = [("#FFBF00", "#CC9900"), ("#FF8C1A", "#DB6E00"),
               ("#59C059", "#389438")]


class Raster:
    """A tiny RGBA image with an anti-aliased polygon filler."""

    def __init__(self, size: int):
        self.n = size
        self.buf = bytearray(size * size * 4)

    # -- drawing ------------------------------------------------------------ #

    def blend(self, x: int, y: int, rgb, alpha: float):
        if alpha <= 0.0:
            return
        if alpha > 1.0:
            alpha = 1.0
        i = (y * self.n + x) * 4
        buf = self.buf
        da = buf[i + 3] / 255.0
        out = alpha + da * (1.0 - alpha)
        if out <= 0.0:
            return
        for k in range(3):
            src = rgb[k]
            dst = buf[i + k]
            buf[i + k] = int(round((src * alpha + dst * da * (1.0 - alpha)) / out))
        buf[i + 3] = int(round(out * 255.0))

    def span(self, cover: List[float], x0: float, x1: float, weight: float):
        if x1 <= x0:
            return
        x0 = max(0.0, x0)
        x1 = min(float(self.n), x1)
        if x1 <= x0:
            return
        first, last = int(x0), int(x1)
        if first == last:
            cover[first] += (x1 - x0) * weight
            return
        cover[first] += (first + 1 - x0) * weight
        for x in range(first + 1, min(last, self.n)):
            cover[x] += weight
        if last < self.n:
            cover[last] += (x1 - last) * weight

    def polygon(self, pts: List[Tuple[float, float]], rgb, sub: int = 5,
                alpha: float = 1.0):
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.floor(min(ys))))
        y1 = min(self.n - 1, int(math.ceil(max(ys))))
        cover = [0.0] * self.n
        weight = 1.0 / sub
        count = len(pts)
        for y in range(y0, y1 + 1):
            touched = False
            for k in range(sub):
                sy = y + (k + 0.5) / sub
                xs = []
                for i in range(count):
                    ax, ay = pts[i]
                    bx, by = pts[(i + 1) % count]
                    if (ay <= sy < by) or (by <= sy < ay):
                        xs.append(ax + (sy - ay) * (bx - ax) / (by - ay))
                if not xs:
                    continue
                xs.sort()
                for i in range(0, len(xs) - 1, 2):
                    self.span(cover, xs[i], xs[i + 1], weight)
                    touched = True
            if not touched:
                continue
            for x in range(self.n):
                if cover[x] > 0.0:
                    self.blend(x, y, rgb, cover[x] * alpha)
                    cover[x] = 0.0

    def rounded_gradient(self, radius: float, top, bottom):
        """The background tile: a rounded square with a vertical gradient."""
        n = self.n
        half = n / 2.0
        inner = half - radius
        for y in range(n):
            t = y / float(n - 1)
            rgb = tuple(int(round(top[i] + (bottom[i] - top[i]) * t))
                        for i in range(3))
            py = y + 0.5 - half
            for x in range(n):
                px = x + 0.5 - half
                dx = max(abs(px) - inner, 0.0)
                dy = max(abs(py) - inner, 0.0)
                dist = math.hypot(dx, dy) - radius
                alpha = 0.5 - dist
                if alpha >= 1.0:
                    self.blend(x, y, rgb, 1.0)
                elif alpha > 0.0:
                    self.blend(x, y, rgb, alpha)

    # -- output ------------------------------------------------------------- #

    def scaled(self, size: int) -> "Raster":
        """Box filtered resize - keeps the small icons clean."""
        out = Raster(size)
        ratio = self.n / float(size)
        for y in range(size):
            sy0, sy1 = y * ratio, (y + 1) * ratio
            for x in range(size):
                sx0, sx1 = x * ratio, (x + 1) * ratio
                r = g = b = a = 0.0
                total = 0.0
                for sy in range(int(sy0), min(self.n, int(math.ceil(sy1)))):
                    for sx in range(int(sx0), min(self.n, int(math.ceil(sx1)))):
                        i = (sy * self.n + sx) * 4
                        alpha = self.buf[i + 3] / 255.0
                        r += self.buf[i] * alpha
                        g += self.buf[i + 1] * alpha
                        b += self.buf[i + 2] * alpha
                        a += alpha
                        total += 1.0
                if total <= 0:
                    continue
                j = (y * size + x) * 4
                if a > 0:
                    out.buf[j] = int(round(r / a))
                    out.buf[j + 1] = int(round(g / a))
                    out.buf[j + 2] = int(round(b / a))
                out.buf[j + 3] = int(round(255.0 * a / total))
        return out

    def png(self) -> bytes:
        n = self.n
        raw = bytearray()
        for y in range(n):
            raw.append(0)
            raw.extend(self.buf[y * n * 4:(y + 1) * n * 4])

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" +
                chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
                chunk(b"IEND", b""))


def draw_icon(size: int = 512) -> Raster:
    """The ScratchPy logo: three snapped blocks on a purple-blue tile."""
    img = Raster(size)
    s = size / 512.0
    img.rounded_gradient(114 * s, ICON_BG_TOP, ICON_BG_BOTTOM)

    metrics = Metrics(scale=3.1 * s)
    metrics.hat_h = 26 * s
    metrics.hat_w = 128 * s
    widths = [292 * s, 268 * s, 238 * s]
    heights = [88 * s + metrics.hat_h, 88 * s, 88 * s]
    left = 112 * s
    top = 104 * s

    shapes = []
    y = top
    for i in range(len(ICON_BLOCKS)):
        flat = metrics.outline(left, y, widths[i], [("bar", heights[i])],
                               hat=(i == 0), top_notch=(i > 0),
                               bottom_bump=(i < len(ICON_BLOCKS) - 1))
        shapes.append([(flat[k], flat[k + 1]) for k in range(0, len(flat), 2)])
        y += heights[i]

    for pts in shapes:
        img.polygon([(px + 4 * s, py + 7 * s) for px, py in pts],
                    (0x24, 0x1C, 0x4E), sub=4, alpha=0.30)
    for pts, (fill, edge) in zip(shapes, ICON_BLOCKS):
        img.polygon([(px + 1.5 * s, py + 1.5 * s) for px, py in pts],
                    tuple(int(edge[j:j + 2], 16) for j in (1, 3, 5)), sub=5)
        img.polygon(pts, tuple(int(fill[j:j + 2], 16) for j in (1, 3, 5)),
                    sub=5)
    return img


ICON_SIZES_ICO = [16, 24, 32, 48, 64, 128, 256]
ICON_SIZES_ICNS = [("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128),
                   ("ic08", 256), ("ic09", 512)]


def write_icon_files(folder: str, base: str = "scratchpy_icon",
                     master: Optional[Raster] = None) -> Dict[str, str]:
    """Write icon.png, icon.ico and icon.icns. Returns the paths."""
    os.makedirs(folder, exist_ok=True)
    master = master or draw_icon(512)
    out: Dict[str, str] = {}
    cache: Dict[int, Raster] = {512: master}

    def at(size: int) -> Raster:
        if size not in cache:
            cache[size] = master.scaled(size)
        return cache[size]

    png_path = os.path.join(folder, base + ".png")
    with open(png_path, "wb") as fh:
        fh.write(master.png())
    out["png"] = png_path

    small_path = os.path.join(folder, base + "_64.png")
    with open(small_path, "wb") as fh:
        fh.write(at(64).png())
    out["png64"] = small_path

    images = [(size, at(size).png()) for size in ICON_SIZES_ICO]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0,
                               size if size < 256 else 0, 0, 0, 1, 32,
                               len(data), offset)
        blobs += data
        offset += len(data)
    ico_path = os.path.join(folder, base + ".ico")
    with open(ico_path, "wb") as fh:
        fh.write(header + entries + blobs)
    out["ico"] = ico_path

    body = b""
    for kind, size in ICON_SIZES_ICNS:
        data = at(size).png()
        body += kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    icns_path = os.path.join(folder, base + ".icns")
    with open(icns_path, "wb") as fh:
        fh.write(b"icns" + struct.pack(">I", len(body) + 8) + body)
    out["icns"] = icns_path
    return out


def icon_folders() -> List[str]:
    """Where to look for ready made icons before drawing them again."""
    folders = []
    if FROZEN:
        base = getattr(sys, "_MEIPASS", "")
        if base:
            folders.append(os.path.join(base, "scratchpy_assets"))
    folders.append(ASSET_DIR)
    return folders


def ensure_icons() -> Dict[str, str]:
    """Find the icon files, drawing them the first time if need be."""
    for folder in icon_folders():
        png64 = os.path.join(folder, "scratchpy_icon_64.png")
        ico = os.path.join(folder, "scratchpy_icon.ico")
        if os.path.exists(png64) and os.path.exists(ico):
            return {"png64": png64, "ico": ico,
                    "png": os.path.join(folder, "scratchpy_icon.png"),
                    "icns": os.path.join(folder, "scratchpy_icon.icns")}
    return write_icon_files(ASSET_DIR)


def apply_window_icon(root: tk.Tk):
    """Give the window and the task bar the ScratchPy logo."""
    try:
        paths = ensure_icons()
    except Exception:
        return
    try:
        photo = tk.PhotoImage(file=paths["png64"])
        root.iconphoto(True, photo)
        root._scratchpy_icon = photo          # keep a reference alive
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            root.iconbitmap(default=paths["ico"])
        except Exception:
            pass


# =========================================================================== #
#  SECTION 16 - start up
# =========================================================================== #

def enable_dpi_awareness():
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def selftest() -> int:
    """Build everything head-less and compile every block. Used by --selftest."""
    failures: List[str] = []
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    root.update()

    project = app.project

    # 0. every block definition must be well formed
    for problem in SPEC_PROBLEMS:
        failures.append("block definition: " + problem)

    # 1. the demo project must compile
    src = generate_file(project, project.files[0])
    err = check_syntax(src)
    if err:
        failures.append("demo project: %s" % err)

    # 2. every single block must produce compiling Python
    probe = SpyFile("probe")
    project.files.append(probe)
    skip = ("missing::",)
    tested = 0
    for bid, spec in sorted(SPECS.items()):
        if bid.startswith(skip):
            continue
        probe.scripts = []
        hat = Block(SPECS["event_start"])
        hat.x = hat.y = 20.0
        loop = Block(SPECS["control_repeat"])
        hat.attach_next(loop)
        try:
            block = Block(spec)
        except Exception as exc:
            failures.append("%s: could not create (%s)" % (bid, exc))
            continue
        if spec.shape in ("hat", "define"):
            probe.scripts.append(block)
            block.x = block.y = 20.0
        else:
            if spec.is_value:
                holder = Block(SPECS["text_print"])
                holder.attach_slot("msg", block)
                loop.attach_branch(0, holder)
            else:
                loop.attach_branch(0, block)
            probe.scripts.append(hat)
        try:
            out = generate_file(project, probe)
            err = check_syntax(out)
        except Exception:
            err = "crashed: " + traceback.format_exc(limit=2)
        if err:
            failures.append("%s -> %s" % (bid, err))
        tested += 1
    project.files.remove(probe)

    # 3. the renderer must survive every block
    gallery = SpyFile("gallery")
    project.files.append(gallery)
    y = 20.0
    for bid, spec in sorted(SPECS.items()):
        if bid.startswith(skip):
            continue
        b = Block(spec)
        b.x, b.y = 20.0, y
        y += 90.0
        gallery.scripts.append(b)
    app.file_index = len(project.files) - 1
    try:
        app.workspace.set_file(gallery)
        root.update()
    except Exception:
        failures.append("renderer: " + traceback.format_exc(limit=3))
    app.file_index = 0
    project.files.remove(gallery)

    # 4. save / load round trip
    try:
        data = json.loads(project.snapshot())
        clone = Project.from_json(data)
        err = check_syntax(generate_file(clone, clone.files[0]))
        if err:
            failures.append("round trip: %s" % err)
    except Exception:
        failures.append("round trip crashed: " + traceback.format_exc(limit=3))

    # 5. package blocks built from a fake module description
    try:
        fake = {"module": "demomod", "version": "1.0", "items": [
            {"name": "shout", "kind": "func", "doc": "",
             "params": [{"name": "text", "required": True, "default": None},
                        {"name": "times", "required": False, "default": "3"}]},
            {"name": "Widget", "kind": "class", "doc": "", "params": []},
            {"name": "VERSION", "kind": "const", "doc": "", "params": []},
        ]}
        pack = app.packages.build_pack("demomod", fake, "demomod")
        register_pack(pack)
        probe2 = SpyFile("packprobe")
        project.files.append(probe2)
        hat = Block(SPECS["event_start"])
        hat.x = hat.y = 10.0
        holder = Block(SPECS["text_print"])
        holder.attach_slot("msg", Block(SPECS["pkg::demomod::shout"]))
        hat.attach_next(holder)
        probe2.scripts.append(hat)
        err = check_syntax(generate_file(project, probe2))
        if err:
            failures.append("package blocks: %s" % err)
        project.files.remove(probe2)
        unregister_pack(pack)
    except Exception:
        failures.append("package blocks crashed: " + traceback.format_exc(limit=3))

    # 5b. any Python file can be turned into blocks again
    sample = (
        "import math\n"
        "total = 0\n"
        "words = []\n\n\n"
        "def area(r):\n"
        "    return math.pi * r ** 2\n\n\n"
        "class Thing:\n"
        "    pass\n\n\n"
        "for i in range(3):\n"
        "    total = total + i\n"
        "    words.append(f'row {i}')\n"
        "    if total > 1 and i != 0:\n"
        "        print('big', round(area(i), 2))\n"
        "    else:\n"
        "        print('small')\n"
        "try:\n"
        "    value = int('x')\n"
        "except ValueError as problem:\n"
        "    print('nope', problem)\n"
        "while total > 0:\n"
        "    total -= 1\n"
        "print(len(words), words[0])\n")
    try:
        fresh = Project()
        imported, importer = import_python_source(fresh, sample, "imported")
        fresh.files = [imported]
        err = check_syntax(generate_file(fresh, imported))
        if err:
            failures.append("python importer: %s" % err)
        made = sum(1 for s in imported.scripts for _ in s.descendants())
        if made < 15:
            failures.append("python importer only made %d blocks" % made)
        if not any("Thing" in chunk for chunk in imported.header_code):
            failures.append("python importer lost the class definition")
    except Exception:
        failures.append("python importer crashed: " + traceback.format_exc(limit=3))

    # 5c. the MCP server answers the calls an assistant would make
    try:
        folder = os.path.join(APP_DIR, "scratchpy_selftest")
        os.makedirs(folder, exist_ok=True)
        server = MCPServer(os.path.join(folder, "mcp.spy"))
        hello = server.handle({"jsonrpc": "2.0", "id": 1,
                               "method": "initialize", "params": {}})
        if not hello or "result" not in hello:
            failures.append("mcp: initialize gave nothing back")
        tools = server.handle({"jsonrpc": "2.0", "id": 2,
                               "method": "tools/list"})
        if len(tools["result"]["tools"]) < 12:
            failures.append("mcp: not all the tools are listed")
        built = server.call("write_python",
                            {"source": "print('hi from mcp')\n",
                             "file": "mcpdemo"})
        if "blocks" not in built:
            failures.append("mcp: write_python said %r" % built)
        code = server.call("read_code", {"file": "mcpdemo"})
        if "hi from mcp" not in code:
            failures.append("mcp: read_code lost the program")
        outline = server.call("read_blocks", {"file": "mcpdemo"})
        if "when green flag clicked" not in outline:
            failures.append("mcp: read_blocks gave %r" % outline[:120])
        bad = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "nope", "arguments": {}}})
        if not bad["result"].get("isError"):
            failures.append("mcp: unknown tools should report an error")
    except Exception:
        failures.append("mcp server crashed: " + traceback.format_exc(limit=3))

    # 6. the generated demo really runs
    try:
        folder = os.path.join(APP_DIR, "scratchpy_selftest")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "demo.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src.replace('return input(str(prompt) + " ")',
                                 'return "Tester"'))
        kw = dict(capture_output=True, text=True, timeout=60, cwd=folder)
        if IS_WINDOWS:
            kw["creationflags"] = NO_WINDOW
        res = subprocess.run([PYTHON_EXE, path], **kw)
        if res.returncode != 0:
            failures.append("running the demo failed:\n" + (res.stderr or "")[:600])
        else:
            print("--- demo output ---")
            print(res.stdout.strip())
            print("-------------------")
    except Exception:
        failures.append("running the demo crashed: " + traceback.format_exc(limit=3))
    finally:
        try:
            import shutil
            shutil.rmtree(os.path.join(APP_DIR, "scratchpy_selftest"),
                          ignore_errors=True)
        except Exception:
            pass

    root.destroy()

    print("blocks defined : %d" % len(SPECS))
    print("blocks tested  : %d" % tested)
    if failures:
        print("FAILURES (%d):" % len(failures))
        for f in failures:
            print("  * " + f)
        return 1
    print("SELFTEST PASSED")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--exec" in args:
        # The bundled app acting as a plain Python interpreter, so programs
        # still run on computers that have no Python of their own.
        import runpy
        index = args.index("--exec")
        target = args[index + 1] if index + 1 < len(args) else ""
        sys.argv = [target] + args[index + 2:]
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass
        try:
            runpy.run_path(target, run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0
    if FROZEN:
        # A windowed bundle has no console, so keep a log next to the app.
        try:
            log = open(os.path.join(APP_DIR, "scratchpy_log.txt"), "a",
                       encoding="utf-8", buffering=1)
            if sys.stdout is None or not sys.stdout.writable():
                sys.stdout = log
            if sys.stderr is None or not sys.stderr.writable():
                sys.stderr = log
            if "--selftest" in args:
                sys.stdout = sys.stderr = log
        except Exception:
            pass
    if "--mcp" in args:
        index = args.index("--mcp")
        target = args[index + 1] if index + 1 < len(args) else None
        if target and target.startswith("-"):
            target = None
        return MCPServer(target).serve()
    if "--selftest" in args:
        return selftest()
    if "--make-icons" in args:
        where = ASSET_DIR
        for i, a in enumerate(args):
            if a == "--make-icons" and i + 1 < len(args):
                where = args[i + 1]
        for kind, path in sorted(write_icon_files(where).items()):
            print("%-6s %s" % (kind, path))
        return 0
    enable_dpi_awareness()
    root = tk.Tk()
    apply_window_icon(root)
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
