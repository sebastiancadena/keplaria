"""Which Firestore the suite talks to, and why getting it wrong is silent.

Until 2026-08-23 the emulator was used only when FIRESTORE_EMULATOR_HOST was
exported, and a shell without it fell through to the real `keplaria-test`
database without saying so. The cost was not theoretical: two `test_sweep`
tests hung for ten minutes each and a `test_console_store` assertion failed,
and that failure was carried for two sessions as a disclosed product defect.
It was 10,330 accumulated case documents crowding a fixture row out of a
limited query. The same tests pass in well under a second on the emulator.

The selection is therefore load-bearing in both directions, and neither
direction announces itself when it is wrong: the emulator does not enforce
collection-group indexes, so a live run silently pointed at it would pass
queries that 400 against real Firestore.
"""

from __future__ import annotations

import sys

import tests.conftest as conftest


def _argv(*args: str) -> list[str]:
    return ["pytest", *args]


def test_the_default_suite_is_not_a_live_run(monkeypatch):
    """`-m 'not live'` is what pyproject passes on every ordinary run."""
    monkeypatch.setattr(sys, "argv", _argv("-m", "not live"))
    assert conftest._is_live_run() is False


def test_selecting_live_tests_is_a_live_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv("-m", "live"))
    assert conftest._is_live_run() is True


def test_the_attached_form_of_the_flag_is_read_too(monkeypatch):
    """`-mlive` is the same request as `-m live` and must not read as neither."""
    monkeypatch.setattr(sys, "argv", _argv("-mlive"))
    assert conftest._is_live_run() is True


def test_a_run_with_no_marker_expression_is_not_live(monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv("tests/unit"))
    assert conftest._is_live_run() is False


def test_a_path_containing_the_word_live_does_not_make_a_run_live(monkeypatch):
    """Only a marker EXPRESSION counts, not any argument that spells the word."""
    monkeypatch.setattr(sys, "argv", _argv("tests/unit/test_delivery.py"))
    assert conftest._is_live_run() is False


def test_a_closed_port_is_not_mistaken_for_a_running_emulator():
    """The emulator dies silently; the check must answer no, not hang."""
    assert conftest._listening("localhost:9") is False


def test_a_malformed_hostport_is_answered_rather_than_raised():
    """A typo in KEPLARIA_EMULATOR_HOSTPORT must degrade to the real database."""
    assert conftest._listening("not-a-port") is False
