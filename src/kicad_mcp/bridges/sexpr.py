"""KiCad S-expression parser, AST manipulation helpers, and formatter.

Handles KiCad 10 `.kicad_sch`, `.kicad_pcb`, `.kicad_sym`, `.kicad_mod`,
`sym-lib-table`, and `fp-lib-table` files cleanly without external dependencies.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass(frozen=True)
class Symbol:
    """An unquoted KiCad S-expression atom (keyword, identifier, or number)."""

    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class QuotedString:
    """A double-quoted KiCad S-expression string literal."""

    value: str

    def __str__(self) -> str:
        return self.value


SExprItem = Union["SExpr", Symbol, QuotedString]


def _escape_string(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _needs_quoting(text: str) -> bool:
    if text == "":
        return True
    for ch in text:
        if ch.isspace() or ch in '()"':
            return True
    return False


def atom_str(item: SExprItem) -> str:
    """Return the string content of an atom (Symbol or QuotedString)."""
    if isinstance(item, (Symbol, QuotedString)):
        return item.value
    raise TypeError(f"Expected atom, got SExpr({item.tag!r})")


def atom_float(item: SExprItem) -> float:
    return float(atom_str(item))


def atom_int(item: SExprItem) -> int:
    return int(atom_str(item))


class SExpr:
    """A parenthesized S-expression list node `(tag child1 child2 ...)`."""

    __slots__ = ("items",)

    def __init__(self, items: list[SExprItem] | None = None):
        self.items: list[SExprItem] = list(items) if items is not None else []

    @classmethod
    def node(cls, tag: str, *children: SExprItem | str | float) -> SExpr:
        """Construct a node cleanly from a tag and mixed Python / SExprItem args."""
        items: list[SExprItem] = [Symbol(tag)]
        for ch in children:
            if isinstance(ch, (SExpr, Symbol, QuotedString)):
                items.append(ch)
            elif isinstance(ch, bool):
                items.append(Symbol("yes" if ch else "no"))
            elif isinstance(ch, int):
                items.append(Symbol(str(ch)))
            elif isinstance(ch, float):
                # Trim trailing zeros cleanly like KiCad does
                formatted = f"{ch:.6f}".rstrip("0").rstrip(".")
                if formatted == "-0":
                    formatted = "0"
                items.append(Symbol(formatted))
            elif isinstance(ch, str):
                items.append(QuotedString(ch))
            else:
                raise TypeError(f"Unsupported SExpr child type: {type(ch)!r}")
        return cls(items)

    @property
    def tag(self) -> str:
        if not self.items:
            return ""
        first = self.items[0]
        if isinstance(first, (Symbol, QuotedString)):
            return first.value
        return ""

    @property
    def args(self) -> list[SExprItem]:
        return self.items[1:]

    def children(self, tag: str | None = None) -> list[SExpr]:
        """Return direct child SExpr nodes, optionally filtered by tag."""
        result: list[SExpr] = []
        for item in self.items[1:]:
            if isinstance(item, SExpr) and (tag is None or item.tag == tag):
                result.append(item)
        return result

    def first(self, tag: str) -> SExpr | None:
        """Return the first direct child SExpr with the given tag, or None."""
        for item in self.items[1:]:
            if isinstance(item, SExpr) and item.tag == tag:
                return item
        return None

    def first_atom(self, index: int = 1, default: str | None = None) -> str | None:
        """Return the string value of `self.items[index]` if it is an atom."""
        if index < len(self.items) and isinstance(self.items[index], (Symbol, QuotedString)):
            return self.items[index].value
        return default

    def child_atom(self, tag: str, index: int = 1, default: str | None = None) -> str | None:
        """Return atom at `index` from the first child matching `tag`."""
        child = self.first(tag)
        if child is None:
            return default
        return child.first_atom(index, default)

    def remove_children(self, tag: str) -> int:
        """Remove all direct child nodes with `tag`. Returns count removed."""
        before = len(self.items)
        self.items = [
            item for idx, item in enumerate(self.items)
            if idx == 0 or not (isinstance(item, SExpr) and item.tag == tag)
        ]
        return before - len(self.items)

    def append(self, item: SExprItem) -> None:
        self.items.append(item)

    def insert_before_end(self, item: SExprItem, before_tags: set[str] | None = None) -> None:
        """Insert a child before the first occurrence of any tag in `before_tags`, or append."""
        if before_tags:
            for idx in range(1, len(self.items)):
                cur = self.items[idx]
                if isinstance(cur, SExpr) and cur.tag in before_tags:
                    self.items.insert(idx, item)
                    return
        self.items.append(item)

    def to_string(self, indent: int = 0) -> str:
        """Serialize to canonical KiCad-style indented S-expression text."""
        return _format_node(self, indent)

    def __repr__(self) -> str:
        return f"SExpr(tag={self.tag!r}, len={len(self.items)})"


# Tags that KiCad normally keeps on a single compact line when they have no complex sub-lists
_INLINE_TAGS = frozenset({
    "xy",
    "at",
    "size",
    "offset",
    "start",
    "mid",
    "end",
    "center",
    "width",
    "type",
    "color",
    "fill",
    "stroke",
    "font",
    "justify",
    "uuid",
    "version",
    "generator",
    "generator_version",
    "paper",
    "lib_id",
    "unit",
    "in_bom",
    "on_board",
    "dnp",
    "exclude_from_sim",
    "fields_autoplaced",
    "pin_names",
    "pin_numbers",
    "hide",
    "layer",
    "layers",
    "net",
    "drill",
    "roundrect_rratio",
    "path",
    "reference",
    "value",
    "page",
    "name",
    "descr",
    "uri",
    "options",
    "length",
    "number",
    "diameter",
    "shape",
    "fields",
})


def _format_atom(item: Symbol | QuotedString) -> str:
    if isinstance(item, QuotedString):
        return f'"{_escape_string(item.value)}"'
    if _needs_quoting(item.value):
        return f'"{_escape_string(item.value)}"'
    return item.value


def _is_compact(node: SExpr) -> bool:
    """Decide whether an SExpr node should be rendered on a single line."""
    has_sublist = any(isinstance(ch, SExpr) for ch in node.items[1:])
    if not has_sublist:
        return True
    return (
        node.tag in _INLINE_TAGS
        and len(node.items) <= 6
        and all(
            not isinstance(ch, SExpr) or not any(isinstance(g, SExpr) for g in ch.items[1:])
            for ch in node.items[1:]
        )
    )


def _format_inline(node: SExpr) -> str:
    parts: list[str] = []
    for item in node.items:
        if isinstance(item, SExpr):
            parts.append(_format_inline(item))
        else:
            parts.append(_format_atom(item))
    return "(" + " ".join(parts) + ")"


def _format_node(node: SExpr, indent: int = 0) -> str:
    if _is_compact(node):
        return _format_inline(node)

    tab = "\t" * indent
    child_tab = "\t" * (indent + 1)

    # Emit header atoms on the opening line until the first SExpr child
    header_parts: list[str] = []
    idx = 0
    while idx < len(node.items) and not isinstance(node.items[idx], SExpr):
        item = node.items[idx]
        assert isinstance(item, (Symbol, QuotedString))
        header_parts.append(_format_atom(item))
        idx += 1

    lines = ["(" + " ".join(header_parts)]
    while idx < len(node.items):
        item = node.items[idx]
        if isinstance(item, SExpr):
            lines.append(child_tab + _format_node(item, indent + 1))
        else:
            lines.append(child_tab + _format_atom(item))
        idx += 1

    return "\n".join(lines) + f"\n{tab})"


def _tokenize(text: str) -> Iterator[tuple[str, str]]:
    """Yield `(kind, value)` tokens where kind is '(', ')', 'STR', or 'SYM'."""
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            yield ("(", "(")
            i += 1
            continue
        if ch == ")":
            yield (")", ")")
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 1
                    if i >= n:
                        raise ValueError("Unterminated escape sequence in quoted string")
                    esc = text[i]
                    if esc == "n":
                        buf.append("\n")
                    elif esc == "r":
                        buf.append("\r")
                    elif esc == "t":
                        buf.append("\t")
                    elif esc == '"':
                        buf.append('"')
                    elif esc == "\\":
                        buf.append("\\")
                    else:
                        buf.append(esc)
                    i += 1
                elif c == '"':
                    i += 1
                    break
                else:
                    buf.append(c)
                    i += 1
            else:
                raise ValueError("Unterminated quoted string in S-expression")
            yield ("STR", "".join(buf))
            continue
        if ch == "#":
            # Skip line comments if present (e.g., legacy or user comments)
            while i < n and text[i] != "\n":
                i += 1
            continue
        start = i
        while i < n and not text[i].isspace() and text[i] not in '()"':
            i += 1
        yield ("SYM", text[start:i])


def parse_sexpr(text: str) -> SExpr:
    """Parse a KiCad S-expression document string into a root SExpr node."""
    stack: list[list[SExprItem]] = []
    root: SExpr | None = None

    for kind, value in _tokenize(text):
        if kind == "(":
            stack.append([])
        elif kind == ")":
            if not stack:
                raise ValueError("Unexpected ')' in S-expression")
            completed = SExpr(stack.pop())
            if stack:
                stack[-1].append(completed)
            else:
                if root is not None:
                    raise ValueError("Multiple root S-expressions found")
                root = completed
        elif kind == "STR":
            if not stack:
                raise ValueError(f"Unexpected top-level string: {value!r}")
            stack[-1].append(QuotedString(value))
        else:
            if not stack:
                raise ValueError(f"Unexpected top-level atom: {value!r}")
            stack[-1].append(Symbol(value))

    if stack:
        raise ValueError("Unclosed '(' in S-expression")
    if root is None:
        raise ValueError("Empty S-expression input")
    return root


def load_sexpr_file(path: str | Path) -> SExpr:
    """Read and parse a KiCad S-expression file from disk."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_sexpr(text)


def save_sexpr_file(path: str | Path, node: SExpr, *, create_backup: bool = False) -> None:
    """Atomically write an SExpr tree to disk (optionally saving a .bak copy first)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if create_backup and p.exists():
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_bytes(p.read_bytes())

    rendered = node.to_string(0) + "\n"
    fd, tmp_path = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        os.replace(tmp_path, p)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
