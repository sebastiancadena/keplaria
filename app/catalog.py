"""The fleet catalog: versioned authority for routing and department scope.

Follows the app.risk.load_policy conventions exactly: pydantic models, ALL
structural validation at load time (never at decision time), a
process-lifetime cache with a test-only reset hook, and lazy loading —
never at import, because a malformed artifact failing at import presents on
the serving platform as a log-less "failed to start and cannot serve
traffic", the most expensive failure mode this project has hit.

Authority is per-deployment, not live: the artifact ships in the container
and is cached for the process lifetime, so a scope change takes effect at
the next deploy. Departments are a policy-and-audit boundary, not a
security boundary — the department on an event is asserted by its
producer, and what this module's consumers guarantee is that an
out-of-scope proposal is refused and durably recorded.

CLOCK_EVENTS lives here (re-exported by app.policy for its existing
importers) so the loader can cross-check clock event types against
event_routes without a circular import.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.lifecycle import (
    APPLY_HOLD,
    ATTACH_EVIDENCE,
    CLEAR_HOLD,
    CREATE_SUPPLIER,
    REQUEST_RENEWAL,
)

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "catalog" / "fleet.v1.json"
)

# Events driven by the clock rather than by a document or an entity change.
CLOCK_EVENTS = frozenset({"renewal_due", "evidence_overdue"})

KNOWN_COMMANDS = frozenset({
    CREATE_SUPPLIER, ATTACH_EVIDENCE, REQUEST_RENEWAL, APPLY_HOLD, CLEAR_HOLD,
})

DEPLOYMENT_STATES = ("deployed", "retired", "proposed")


class CatalogLoadError(ValueError):
    """The catalog artifact is missing, malformed, or self-inconsistent."""


class EvalPointer(BaseModel):
    """A citation to the suite that grades an agent — a pointer, never a
    number, because a copied score goes stale silently."""

    model_config = ConfigDict(extra="forbid")
    suite: str
    metric: str
    evidence: str


class AgentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    routable: bool
    version: int = Field(ge=1)
    owner: str
    purpose: str
    input_schema: str
    output_schema: str
    approved_tools: list[str]
    data_classes: list[str]
    deployment: str
    evals: EvalPointer


class DepartmentScope(BaseModel):
    """One authoritative list per concern: what routing authorizes IS what
    the fleet view renders. extra='forbid' means a typo'd scope key is a
    load error, never a silent grant or denial."""

    model_config = ConfigDict(extra="forbid")
    description: str
    permitted_agents: list[str]
    permitted_commands: list[str]


class LegacyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v1_department: str | None


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_id: str
    catalog_version: int = Field(ge=1)
    agents: list[AgentManifest]
    event_routes: dict[str, list[str]]
    departments: dict[str, DepartmentScope]
    legacy: LegacyPolicy

    def routable_ids(self) -> tuple[str, ...]:
        """Routable agent ids in canonical (declaration) order."""
        return tuple(agent.id for agent in self.agents if agent.routable)


def load_catalog(path: Path | None = None) -> Catalog:
    """Parse and validate the artifact. Raises CatalogLoadError on ANY problem.

    Validation lives here, not in the consumers, so a structurally invalid
    catalog is impossible at decision time — same argument as
    app.risk.load_policy.
    """
    target = Path(path) if path else DEFAULT_CATALOG_PATH
    try:
        catalog = Catalog.model_validate_json(target.read_text())
    except (OSError, ValidationError, ValueError) as exc:
        raise CatalogLoadError(f"cannot load catalog from {target}: {exc}") from exc

    ids = [agent.id for agent in catalog.agents]
    if len(ids) != len(set(ids)):
        raise CatalogLoadError("duplicate agent ids")

    routable = catalog.routable_ids()
    if "evidence" in routable and "compliance" in routable:
        if routable.index("evidence") > routable.index("compliance"):
            raise CatalogLoadError(
                "canonical order: evidence must precede compliance "
                "(screening consumes grounded fields the evidence step produces)"
            )

    for agent in catalog.agents:
        if agent.deployment not in DEPLOYMENT_STATES:
            raise CatalogLoadError(
                f"agent {agent.id}: unknown deployment state {agent.deployment!r}"
            )

    for event_type, route in catalog.event_routes.items():
        for name in route:
            if name not in routable:
                raise CatalogLoadError(
                    f"event type {event_type!r} routes to {name!r}, which is "
                    "not a declared routable agent"
                )
        if event_type in CLOCK_EVENTS and route:
            raise CatalogLoadError(
                f"clock event type {event_type!r} must map to an empty route"
            )
    for clock_event in CLOCK_EVENTS:
        if clock_event not in catalog.event_routes:
            raise CatalogLoadError(
                f"clock event type {clock_event!r} is missing from event_routes"
            )

    for name, scope in catalog.departments.items():
        for agent_id in scope.permitted_agents:
            if agent_id not in routable:
                raise CatalogLoadError(
                    f"department {name!r} permits unknown agent {agent_id!r}"
                )
        for command in scope.permitted_commands:
            if command not in KNOWN_COMMANDS:
                raise CatalogLoadError(
                    f"department {name!r} permits unknown command {command!r}"
                )

    if catalog.legacy.v1_department is not None:
        if catalog.legacy.v1_department not in catalog.departments:
            raise CatalogLoadError(
                f"legacy v1_department {catalog.legacy.v1_department!r} is "
                "not a declared department"
            )

    return catalog


_CACHE: dict[str, Catalog] = {}


def reset_catalog_cache() -> None:
    """Test hook. Production never calls this."""
    _CACHE.clear()


def get_catalog(path: Path | None = None) -> Catalog:
    """The cached catalog. Raises CatalogLoadError — callers fail closed."""
    key = str(Path(path) if path else DEFAULT_CATALOG_PATH)
    if key not in _CACHE:
        _CACHE[key] = load_catalog(path)
    return _CACHE[key]
