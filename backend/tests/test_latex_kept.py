"""A model's LaTeX in a JSON reply keeps its backslashes: "\textbf" is not a tab and "extbf"."""

import json

from mavo.crew_workers import latex_kept, parse_checkpoint

B = "\\"


def test_single_backslash_latex_survives_json():
    raw = json.dumps({"x": 0}).replace(
        '"x": 0',
        f'"summary": "{B}textbf{{Hi}} {B}begin{{itemize}} {B}item a '
        f'{B}end{{itemize}} {B}frac{{1}}{{2}}"',
    )
    assert json.loads(latex_kept(raw))["summary"] == (
        f"{B}textbf{{Hi}} {B}begin{{itemize}} {B}item a {B}end{{itemize}} {B}frac{{1}}{{2}}"
    )


def test_properly_escaped_json_and_ordinary_escapes_are_untouched():
    proper = json.dumps({"summary": f"{B}textbf{{Hi}}\nline two\tcol"})
    assert latex_kept(proper) == proper
    assert json.loads(latex_kept(proper))["summary"] == f"{B}textbf{{Hi}}\nline two\tcol"


def test_a_reply_with_latex_parses_into_a_clean_summary():
    raw = '{"state": "wait_for_next_review", "summary": "' + B + 'textbf{Note:} all good"}'
    assert parse_checkpoint(raw).summary.startswith(B + "textbf{Note:}")
