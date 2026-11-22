"""Tests for the KiCad S-expression parser, AST helpers, formatter, and atomic file writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_mcp.bridges.sexpr import (
    QuotedString,
    SExpr,
    Symbol,
    atom_float,
    atom_int,
    atom_str,
    load_sexpr_file,
    parse_sexpr,
    save_sexpr_file,
)


def test_parse_and_roundtrip_escaping() -> None:
    src = r'''(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(property "Note" "Line1\nLine2\t\"quoted\" \\slash")
	(at 10.5 -20.25 90)
)'''
    tree = parse_sexpr(src)
    assert tree.tag == "kicad_sch"
    assert tree.child_atom("version") == "20250114"
    assert tree.child_atom("generator") == "eeschema"
    prop = tree.first("property")
    assert prop is not None
    assert prop.first_atom(1) == "Note"
    assert prop.first_atom(2) == 'Line1\nLine2\t"quoted" \\slash'

    rendered = tree.to_string()
    reparsed = parse_sexpr(rendered)
    assert reparsed.first("property") is not None
    assert reparsed.first("property").first_atom(2) == 'Line1\nLine2\t"quoted" \\slash'


def test_node_builder_and_ast_helpers() -> None:
    node = SExpr.node(
        "symbol",
        SExpr.node("lib_id", "Device:R"),
        SExpr.node("at", 10.0, -0.0, 90),
        SExpr.node("in_bom", True),
        SExpr.node("dnp", False),
    )
    assert node.child_atom("lib_id") == "Device:R"
    assert node.child_atom("in_bom") == "yes"
    assert node.child_atom("dnp") == "no"
    at = node.first("at")
    assert at is not None
    assert atom_float(at.items[1]) == 10.0
    assert atom_str(at.items[2]) == "0"
    assert atom_int(at.items[3]) == 90

    node.insert_before_end(SExpr.node("unit", 1), before_tags={"in_bom"})
    tags = [c.tag for c in node.children()]
    assert tags == ["lib_id", "at", "unit", "in_bom", "dnp"]

    removed = node.remove_children("dnp")
    assert removed == 1
    assert node.first("dnp") is None


def test_parse_errors() -> None:
    with pytest.raises(ValueError, match="Empty"):
        parse_sexpr("   ")
    with pytest.raises(ValueError, match="Unclosed"):
        parse_sexpr("(a (b 1)")
    with pytest.raises(ValueError, match="Unexpected '\\)'"):
        parse_sexpr("(a 1))")
    with pytest.raises(ValueError, match="Unterminated quoted string"):
        parse_sexpr('(a "unterminated)')
    with pytest.raises(ValueError, match="Multiple root"):
        parse_sexpr("(a 1) (b 2)")


def test_comments_and_quoted_hash() -> None:
    src = """# leading comment
(symbol
    (property "Reference" "#PWR01")
)"""
    tree = parse_sexpr(src)
    prop = tree.first("property")
    assert prop is not None
    assert prop.first_atom(2) == "#PWR01"


def test_atomic_save_and_backup(tmp_path: Path) -> None:
    target = tmp_path / "demo.kicad_sch"
    tree1 = SExpr.node("kicad_sch", SExpr.node("version", 20250114))
    save_sexpr_file(target, tree1, create_backup=True)
    assert target.is_file()
    assert not target.with_suffix(".kicad_sch.bak").exists()

    tree2 = SExpr.node(
        "kicad_sch",
        SExpr.node("version", 20250114),
        SExpr.node("paper", QuotedString("A4")),
        SExpr.node("flag", Symbol("yes")),
    )
    save_sexpr_file(target, tree2, create_backup=True)
    bak = target.with_suffix(".kicad_sch.bak")
    assert bak.is_file()

    loaded_bak = load_sexpr_file(bak)
    assert loaded_bak.first("paper") is None
    loaded_cur = load_sexpr_file(target)
    assert loaded_cur.child_atom("paper") == "A4"
    assert loaded_cur.child_atom("flag") == "yes"
