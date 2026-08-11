"""The migrations must produce exactly the schema the models describe.

Every other test builds its database with `Base.metadata.create_all`, which is
fast and correct for testing behaviour but never runs a single migration. That
gap has cost us twice: a table (`waitlist_entries`) that existed only in the
models and was never created in production, and boolean defaults written as
SQLite's `1`/`0`, which Postgres rejects outright.

Both failures shared a shape. They passed locally, passed CI, and surfaced only
against the production database. So these tests do the one thing the rest of the
suite deliberately skips: run the real migration chain and compare the result to
the models.
"""

import pathlib
import tempfile

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.db.models import Base

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _migrated_engine(tmp: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> sa.Engine:
    """Build a database by running the migration chain, not create_all.

    alembic/env.py deliberately overrides `sqlalchemy.url` with the app's own
    settings, so passing a URL into Config here would be silently ignored and
    the migrations would run against the developer's real database. The only
    way in is the environment variable the settings object reads.
    """
    from app.config import get_settings

    path = tmp / "migrated.db"
    # env.py runs migrations through an async engine, so it needs the aiosqlite
    # driver; the inspection below is synchronous and needs the plain one.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    get_settings.cache_clear()

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    return sa.create_engine(f"sqlite:///{path}")


def test_migrations_match_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """`alembic upgrade head` and the models must agree.

    A non-empty diff means someone changed models.py without a migration, so
    production would be missing whatever they added.
    """
    from app.config import get_settings

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = _migrated_engine(pathlib.Path(tmpdir), monkeypatch)
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "target_metadata": Base.metadata}
            )
            diff = compare_metadata(context, Base.metadata)
        engine.dispose()

    # Other tests build their own database; leaving a stale cached settings
    # object pointed at a deleted temp file would break whatever runs next.
    get_settings.cache_clear()

    assert not diff, (
        "Models and migrations have diverged. Run:\n"
        "  alembic revision --autogenerate -m '<what changed>'\n\n"
        f"Missing operations: {diff}"
    )


@pytest.mark.parametrize(
    "column",
    [c for t in Base.metadata.tables.values() for c in t.columns if c.server_default is not None],
    ids=lambda c: f"{c.table.name}.{c.name}",
)
def test_server_defaults_are_dialect_portable(column: sa.Column) -> None:
    """A server default must be valid on Postgres, not just on SQLite.

    SQLite accepts `DEFAULT 1` for a BOOLEAN column; Postgres raises
    DatatypeMismatchError and the whole migration aborts. Since we develop on
    SQLite and deploy on Postgres, the literal has to be checked directly.
    """
    literal = str(getattr(column.server_default, "arg", "")).strip().lower()
    if not literal or literal == "''":
        return

    if isinstance(column.type, sa.Boolean):
        assert literal in ("true", "false"), (
            f"{column.table.name}.{column.name} is BOOLEAN but defaults to {literal!r}. "
            "Postgres needs true/false; use sa.true()/sa.false()."
        )
    elif isinstance(column.type, (sa.Integer, sa.Numeric)):
        assert literal not in ("true", "false"), (
            f"{column.table.name}.{column.name} is numeric but defaults to {literal!r}."
        )


def test_added_columns_are_safe_against_a_table_with_rows() -> None:
    """A NOT NULL column added without a server default cannot be applied.

    Postgres has to put something in that column for every existing row, and
    with no default there is nothing to put, so the migration aborts. It applies
    perfectly well to an empty database, which is exactly what the other test
    above uses, so this failure reaches production untouched by the suite: it
    already did once, taking down a deploy while every test passed.

    Static check over the migration files rather than a runtime one, because
    reproducing it needs a database that already holds data at the right point
    in the history.
    """
    import re

    offenders: list[str] = []
    pattern = re.compile(r"op\.add_column\(\s*(['\"])(?P<table>[^'\"]+)\1\s*,\s*(?P<col>sa\.Column\(.*)")

    for revision in sorted((BACKEND_ROOT / "alembic" / "versions").glob("*.py")):
        source = revision.read_text()
        for match in pattern.finditer(source):
            # The column definition runs to the end of the add_column call; the
            # regex stops at the line end, which is how alembic autogenerates it.
            column = match.group("col")
            if "nullable=False" not in column:
                continue
            if "server_default" in column:
                continue
            name = re.search(r"['\"]([^'\"]+)['\"]", column)
            offenders.append(
                f"{revision.name}: {match.group('table')}."
                f"{name.group(1) if name else '?'}"
            )

    assert not offenders, (
        "These columns are NOT NULL with no server_default, so adding them to a "
        "table that already has rows will fail:\n  " + "\n  ".join(offenders)
    )
