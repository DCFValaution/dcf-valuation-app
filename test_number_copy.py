"""Numbers in prose must not contradict the words around them.

Fixed-point formatting keeps a sign that rounding has already made
meaningless, and the sentences these numbers sit in were written assuming
it would not. Two things went out to users: "revenue has fallen - -0.5%",
where a dash meant as punctuation collided with a minus sign, and "-0.0%"
for figures that are not negative.
"""

import pytest

from plain_language import fixed, pct, signed_pct


@pytest.mark.parametrize("value", [-0.0, -0.000001, -0.00004, -0.0004])
def test_a_figure_that_rounds_to_zero_keeps_no_minus(value):
    assert pct(value) == "0.0%"
    assert signed_pct(value) == "0.0%"


def test_a_figure_that_does_not_round_away_keeps_its_sign():
    assert pct(-0.052) == "-5.2%"
    assert signed_pct(-0.052) == "-5.2%"
    assert signed_pct(0.153) == "+15.3%"


def test_zero_claims_no_direction():
    """"+0.0%" asserts a rise the figure does not support."""
    assert signed_pct(0.0) == "0.0%"


def test_places_are_honoured():
    assert pct(0.05117, places=2) == "5.12%"
    assert fixed(-0.004, 2) == "0.00"
    assert fixed(-1234.5, 2) == "-1234.50"


def test_no_prose_puts_a_dash_in_front_of_a_formatted_number():
    """The collision that produced "revenue has fallen - -0.5%".

    Checked against the source, because the sentence is composed deep inside
    a suitability assessment and the shape is what matters: a dash used as
    punctuation, directly before a number that may carry its own sign.

    The dash must follow a word to count. "(1 - {tax_rate:.2%})" is
    arithmetic, not punctuation, and its dash follows a digit.
    """
    import pathlib
    import re

    prose_dash = re.compile(r"[a-zA-Z] - \{[a-z_][^}]*[%f]\}")
    for name in ("analysis.py", "plain_language.py", "relative.py",
                 "speculative.py", "roic.py", "ddm.py", "dcf.py"):
        source = pathlib.Path(name).read_text(encoding="utf-8")
        found = prose_dash.findall(source)
        assert not found, (
            f"{name} puts a dash directly after a word and before a "
            f"formatted number {found}; if that number is negative it "
            "renders as '- -0.5%'")
