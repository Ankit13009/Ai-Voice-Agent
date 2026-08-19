"""seed.py must stay a mechanism, not a data file.

It once carried an invented clinic and salon with fabricated patients, phone
numbers and call transcripts, in a public repository. That is gone. These tests
exist so it does not come back by accident: no credentials, and no phone numbers
that could belong to a real person.
"""

import pathlib

SEED = pathlib.Path(__file__).resolve().parents[1] / "seed.py"


def test_no_phone_numbers_at_all():
    """A realistic number in a public repository belongs to somebody."""
    import re

    numbers = re.findall(r"\+\d{10,15}", SEED.read_text())
    assert not numbers, f"seed.py should hold no phone numbers, found: {numbers}"


def test_credentials_are_required_arguments_with_no_defaults():
    """Nothing here may produce a predictable account."""
    source = SEED.read_text()
    assert 'parser.add_argument("--email", required=True)' in source
    assert 'parser.add_argument("--password", required=True)' in source
    assert "password=" not in source.replace("--password", "")


def test_it_creates_no_businesses():
    """Businesses come from the onboarding form, where a human names them.

    Checks the code rather than the prose: the module docstring mentions the
    removed demo data on purpose, so that a reader knows why it is not here.
    """
    import ast

    tree = ast.parse(SEED.read_text())
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "create_business" not in functions
    assert "add_activity" not in functions
    assert "create_demo_data" not in functions

    # And no Business rows constructed anywhere in it.
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "Business" not in constructed, "seed.py must not create businesses"
