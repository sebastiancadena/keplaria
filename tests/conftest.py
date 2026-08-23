"""Shared test fixtures.

Firestore transaction semantics are the thing under test, so these tests need
a real Firestore API on the other end: either the local emulator (fast and
hermetic) or the `keplaria-test` database (slow, shared, and permanent).

**The emulator is used automatically when it is running.** It used to be used
only when FIRESTORE_EMULATOR_HOST was exported, and a shell without that
variable fell through to the real database in complete silence. That is not a
theoretical footgun: on 2026-08-23 it made two `test_sweep` tests hang for ten
minutes each and `test_console_store` fail, and the failure had been carried
for two sessions as a disclosed product defect. It was neither -- it was
10,330 accumulated case documents crowding a fixture row out of a limited
query. The same tests pass in 0.72s against the emulator.

So the fallback is now loud, and preference is expressed by behaviour rather
than by a variable someone has to remember:

    gcloud beta emulators firestore start --host-port=localhost:8451

Override either way: set FIRESTORE_EMULATOR_HOST to point somewhere else, or
KEPLARIA_TEST_USE_REAL_FIRESTORE=1 to insist on `keplaria-test`.
"""

import os
import socket
import sys
import uuid
import warnings

import pytest

from app.state.firestore import get_client

TEST_DATABASE = os.environ.get("KEPLARIA_TEST_DATABASE", "keplaria-test")
DEFAULT_EMULATOR = os.environ.get("KEPLARIA_EMULATOR_HOSTPORT", "localhost:8451")


def _listening(hostport: str) -> bool:
    """Whether something accepts a connection there right now.

    A cheap connect rather than a Firestore call: the emulator dies silently
    and stays dead, so "is the port open" is the question that distinguishes
    a hermetic run from a slow one, and it has to be answered before any
    client is built.
    """
    host, _, port = hostport.rpartition(":")
    try:
        with socket.create_connection((host or "localhost", int(port)), timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


def _is_live_run() -> bool:
    """Whether this invocation selected the live-marked tests.

    A live run talks to the real deployed system, and its Firestore should be
    the real `keplaria-test` database rather than the emulator: the emulator
    does not enforce collection-group indexes, so a query that 400s against
    real Firestore passes against it. Auto-selecting the emulator is the right
    default for the fast suite and the wrong one here.

    Read off argv rather than a pytest hook because the choice has to be made
    before any client is built, and `get_client` is cached from its first call.
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        expression = ""
        if arg == "-m" and i + 1 < len(argv):
            expression = argv[i + 1]
        elif arg.startswith("-m") and len(arg) > 2:
            expression = arg[2:]
        if expression and "live" in expression and "not live" not in expression:
            return True
    return False


if os.environ.get("KEPLARIA_TEST_USE_REAL_FIRESTORE") == "1" or _is_live_run():
    os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
elif not os.environ.get("FIRESTORE_EMULATOR_HOST") and _listening(DEFAULT_EMULATOR):
    os.environ["FIRESTORE_EMULATOR_HOST"] = DEFAULT_EMULATOR

# App code (graph nodes, executor) resolves its own client from
# FIRESTORE_DATABASE at call time, and `--env-file .env` hands it the live
# "(default)" value. Under pytest that must always be the test database; the
# emulator case keeps "(default)" because the emulator holds no real data.
if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
    os.environ["FIRESTORE_DATABASE"] = TEST_DATABASE
    warnings.warn(
        f"No Firestore emulator on {DEFAULT_EMULATOR}: running against the REAL "
        f"{TEST_DATABASE} database. Expect whole-collection queries to be slow "
        f"and order-dependent against its accumulated rows. Start one with: "
        f"gcloud beta emulators firestore start --host-port={DEFAULT_EMULATOR}",
        stacklevel=1,
    )


@pytest.fixture(scope="session")
def db():
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "keplaria")
        return get_client(database="(default)")
    return get_client(database=TEST_DATABASE)


@pytest.fixture
def case_id():
    """A unique case ID per test, so parallel or repeated runs never collide."""
    return f"TEST-{uuid.uuid4().hex[:12]}"
