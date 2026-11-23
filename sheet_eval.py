"""A very small spreadsheet evaluator, for tests.

WHY THIS EXISTS
---------------
The workbook is the app's answer in a form the reader can argue with, so
the two have to agree. Nothing was checking that they did, and they had
quietly stopped: the engine began charging for terminal growth - the
perpetuity is built from NOPAT x (1 - g / ROIC) rather than from the final
year's free cash flow - while the spreadsheet kept the old form. "Return on
capital, long run" sat there as an input cell that nothing read.

Every test that could have caught it instead checked the shape of a
formula: that the Gordon denominator was there, that the terminal value
hung off the final column. Both stayed true across the drift. The only
check that could not drift is to work the sheet out and compare the number
at the bottom with the one the engine printed.

WHAT IT HANDLES
---------------
Exactly what the export writes and no more: + - * / ^, parentheses,
unary minus, A1 and $A$1 references, SUM over a rectangular range, and
IF(cond, then, else) with = < > comparisons. Anything else raises, which
is the point - a formula this cannot read is a formula no test is checking,
and silence would be worse than the error.

It is not a spreadsheet. It does not do iteration, names, text, or dates.
"""
from __future__ import annotations

import re
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

_CELL = re.compile(r"\$?([A-Z]{1,3})\$?([0-9]{1,7})")
_RANGE = re.compile(r"\$?[A-Z]{1,3}\$?[0-9]{1,7}:\$?[A-Z]{1,3}\$?[0-9]{1,7}")


class SheetEvalError(Exception):
    """A formula this evaluator will not guess at."""


def _split_args(text: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return out


class Sheet:
    """Evaluates a worksheet's formulas, with a cache and cycle detection."""

    def __init__(self, ws):
        self.ws = ws
        self._cache: dict[tuple[int, int], Any] = {}
        self._active: set[tuple[int, int]] = set()

    # -- public ------------------------------------------------------------

    def value(self, ref: str) -> Any:
        """The value of a cell, by its A1 reference."""
        match = _CELL.fullmatch(ref.replace("$", ""))
        if match is None:
            raise SheetEvalError(f"not a cell reference: {ref!r}")
        return self._at(int(match.group(2)),
                        column_index_from_string(match.group(1)))

    def at(self, row: int, col: int = 2) -> Any:
        return self._at(row, col)

    # -- the work ----------------------------------------------------------

    def _at(self, row: int, col: int) -> Any:
        key = (row, col)
        if key in self._cache:
            return self._cache[key]
        if key in self._active:
            raise SheetEvalError(
                f"circular reference at {get_column_letter(col)}{row}")

        raw = self.ws.cell(row=row, column=col).value
        if isinstance(raw, str) and raw.startswith("="):
            self._active.add(key)
            try:
                result = self._eval(raw[1:])
            finally:
                self._active.discard(key)
        elif isinstance(raw, (int, float)):
            result = float(raw)
        else:
            # Excel reads a blank cell as zero. This does not, because a
            # formula in this workbook that reaches a blank cell is a
            # mistake in the export, and answering zero would hide it
            # behind a plausible number. Nothing here reads one: the only
            # blank inputs are the optional margin target and its year,
            # which no formula references unless they are set. Same for
            # text, which has no arithmetic meaning at all.
            raise SheetEvalError(
                f"{get_column_letter(col)}{row} is not a number: {raw!r}")

        self._cache[key] = result
        return result

    def _eval(self, expr: str) -> float:
        expr = self._expand_functions(expr)
        expr = _RANGE.sub(self._refuse_bare_range, expr)
        expr = _CELL.sub(self._substitute, expr)
        expr = expr.replace("^", "**")
        if re.search(r"[A-Za-z_]", expr):
            raise SheetEvalError(f"unevaluated name in: {expr!r}")
        try:
            return float(eval(expr, {"__builtins__": {}}, {}))
        except SheetEvalError:
            raise
        except Exception as exc:  # pragma: no cover - a bad formula
            raise SheetEvalError(f"{expr!r}: {exc}") from exc

    def _expand_functions(self, expr: str) -> str:
        """Replace SUM(...) and IF(...) with their values, innermost first."""
        while True:
            match = re.search(r"\b(SUM|IF)\(", expr)
            if match is None:
                return expr
            start = match.end() - 1
            depth, i = 0, start
            while i < len(expr):
                if expr[i] == "(":
                    depth += 1
                elif expr[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            else:
                raise SheetEvalError(f"unclosed {match.group(1)} in {expr!r}")
            inner = expr[start + 1:i]
            if match.group(1) == "SUM":
                value = self._sum(inner)
            else:
                value = self._if(inner)
            expr = expr[:match.start()] + repr(value) + expr[i + 1:]

    def _sum(self, inner: str) -> float:
        inner = inner.strip()
        if not _RANGE.fullmatch(inner):
            raise SheetEvalError(f"SUM takes one range here, got {inner!r}")
        first, last = inner.split(":")
        a = _CELL.fullmatch(first.replace("$", ""))
        b = _CELL.fullmatch(last.replace("$", ""))
        c1, r1 = column_index_from_string(a.group(1)), int(a.group(2))
        c2, r2 = column_index_from_string(b.group(1)), int(b.group(2))
        total = 0.0
        for row in range(min(r1, r2), max(r1, r2) + 1):
            for col in range(min(c1, c2), max(c1, c2) + 1):
                total += self._at(row, col)
        return total

    def _if(self, inner: str) -> float:
        parts = _split_args(inner)
        if len(parts) != 3:
            raise SheetEvalError(f"IF takes three arguments, got {len(parts)}")
        condition, then, otherwise = parts
        # Excel's single "=" is a comparison here; "<>" is "not equal".
        condition = condition.replace("<>", "!=")
        condition = re.sub(r"(?<![<>!=])=(?!=)", "==", condition)
        return self._eval(then) if self._eval(condition) else \
            self._eval(otherwise)

    def _substitute(self, match: re.Match) -> str:
        return repr(self._at(int(match.group(2)),
                             column_index_from_string(match.group(1))))

    @staticmethod
    def _refuse_bare_range(match: re.Match) -> str:
        raise SheetEvalError(
            f"a range outside SUM: {match.group(0)!r}")
