"""Shared test fixtures.

Firestore transaction semantics are the thing under test, so these tests run
against a real Firestore database (`keplaria-test`) by default. If the local
emulator is installed, exporting FIRESTORE_EMULATOR_HOST makes them hermetic and
much faster:

    gcloud beta emulators firestore start --host-port=localhost:8451
    export FIRESTORE_EMULATOR_HOST=localhost:8451
"""

import os
import uuid

import pytest

from app.state.firestore import get_client

TEST_DATABASE = os.environ.get("KEPLARIA_TEST_DATABASE", "keplaria-test")


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
