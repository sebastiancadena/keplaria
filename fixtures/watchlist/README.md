# Synthetic screening watchlist fixture

`entities.ftm.json` is a **fully synthetic**, rights-cleared watchlist in
[FollowTheMoney](https://followthemoney.tech/) entity-graph JSON-lines format.
It is indexed by the self-hosted `yente` screening service on the private
`keplaria-yente` VM.

## Provenance

| Aspect | Statement |
|---|---|
| Origin | Authored for this project. No third-party dataset was copied, derived from, or reformatted. |
| Real-world subjects | **None.** Every company, person, organization, and vessel is invented. |
| Personal-like fields | Names, birth dates, nationalities, positions, registration numbers, and IMO numbers are fictional. Each record carries a `notes` value saying so. |
| Licence | Same licence as this repository. |
| OpenSanctions data | **Not used.** See below. |

## Why this fixture and not OpenSanctions data

yente's shipped manifests pull the OpenSanctions catalog — `civic.yml` from
`data.opensanctions.org`, `commercial.yml` from `delivery.opensanctions.com`
behind an `OPENSANCTIONS_DELIVERY_TOKEN`.

Data rights are **not** the reason this fixture exists. OpenSanctions confirmed
in writing that bulk-data use by this entry is covered by the data's CC BY-NC
4.0 terms. The fixture is indexed because a synthetic watchlist is small,
reproducible, and safe to publish, and because it can be shaped to exercise the
screening path deterministically — a real dataset can do none of those things.

This deployment therefore runs on this fixture alone:

- `infra/yente/manifest.yml` declares `catalogs: []` — no remote catalog.
- `YENTE_AUTO_REINDEX=false` — no scheduled fetch from any OpenSanctions host.

While that is true, do not describe the deployment as screening against the
live OpenSanctions dataset, and do not make source-count claims. The accurate
claim is: *a self-hosted yente instance screening against a synthetic watchlist
fixture.* The claim depends on what is indexed, not on what is licensed.

If the real collection is ever indexed, two obligations attach: the data files
and any export derived from them stay out of this repository, and the README
carries the attribution OpenSanctions specifies.

## Contents

16 entities, deliberately shaped to exercise the screening path:

- 8 `Company`, 6 `Person`, 1 `Organization`, 1 `Vessel`.
- Topics span `sanction`, `sanction.linked`, `role.pep`, `debarment`,
  `export.control`, `crime.fin`, and `poi`.
- Two **near-name decoy pairs** — `syn-co-001`/`syn-co-008` and
  `syn-pe-001`/`syn-pe-006` — so fuzzy scoring and false-positive handling have
  something to resolve rather than always returning a clean single hit.
- Countries skew to Latin America to match the Andina Foods supplier scenario.

## Changing the fixture

After editing, bump `version` in `infra/yente/manifest.yml` to a higher
monotonic number and re-run the reindex; yente only re-imports on a version
increase.
