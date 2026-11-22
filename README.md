# TUPA⚡: Telemetría Unificada de Potencia Argentina

TUPA is a project aimed at aggregating and displaying the status of the
Argentine electrical grid, intended for feeding messaging bots and other
systems.

It is named after Tupã, the Guaraní spirit of lightning, thunder, and storms. ⚡

> **Work in progress:** This project is in an exploratory stage.

## Philosophy

In priority order:

1. Security
2. Correctness
3. Limit necessary scope
4. Few or no dependencies

> TUPA is a broad aggregator, not an authoritative source. In mission-critical
> systems it should be treated as just another alert source, not a health
> indicator.

## Design

TUPA runs standalone or from cron, writing JSON to stdout. Members are
selectable via arguments, environment, or JSON configuration and default to all.
Each member combines several datasources and reports the worst known status.
The output can be used directly or piped to a file and served; individual
consumers are expected to know to select the members relevant to them. See
[example.json](example.json). In daemon mode, TUPA serves an HTML dashboard at
`/` and JSON at `/api`. The dashboard sources modal shows only sources active in
the running instance.

Configuration: [config.md](config.md).

### Run with Docker (recommended)

```sh
docker compose up --build --detach
```

`stack.env` is a template of all supported settings with their defaults commented
out. Optionally copy it to `.env`, then uncomment and change any values you need
before starting the container.

### Run from source

From `src/`:

```sh
cd src
python3 -m tupa
python3 -m unittest discover -s tests
```

From the repository root, `python3 -m pip install .` also installs a `tupa`
command.

### Releases

The version is declared once in `pyproject.toml`. After bumping it, create a Git
tag with the same `vX.Y.Z` value.

### Initial sources

Work begins with the following sources:

- Edenor
- Edesur
- Transener
- SADI
- CAMMESA
- SACME
- Edelap
- CortesDeLuz.com.ar (CDL, crosschecking)

> **Relationship to CDL:** CDL is useful but can be unavailable, its code is
> closed, and it can't be run independently. TUPA is its automatable complement,
> not a replacement: it adds scriptable status output and uses CDL for
> cross-checking when other sources are unavailable. CDL's own data feeds are
> shown in the [diagram](diagram.pu).
>
> In short: we have CDL at home.

### Topology

- [System topology and upstream dependencies](topology.md)
- [diagram](diagram.pu)

### Status

A `member` carries a `status` (`operational`, `warn`, `high`,
`disaster`, or `unknown`), a `comment`, and an `updated` timestamp. Colors
(green, yellow, orange, red, grey) are display hints only. A `system` has its
own `updated` timestamp and a `members` map keyed by member name. Timestamps
are ISO 8601 / RFC 3339 strings. See the [CDDL schema](status.cddl).

ENRE only publishes a time of day: TUPA pairs it with the file's HTTP
`Last-Modified` date. Without that header, `updated` records when TUPA observed
the data instead of assuming a date.

Planned outages are outages: they use the same severity thresholds as any other
outage.

## Todo

- [X] Spanish documentation
- [X] POC
  - stdlib Python 3, callable once for cron or as a daemon for HTTP
  - Docker with python:3.12-slim
  - Implement JSON output
  - Implement Edenor and Edesur sources
- [ ] Output
  - [X] HTML
  - [ ] SVG
  - [ ] Webhooks
- [ ] Sources and members
  - [ ] Transener
  - [ ] SADI
  - [ ] SACME
  - [ ] Edelap
  - [ ] Other (TBD)
- [ ] Other Argentine jurisdictions

Initial sources are planned in this order; CDL is validation-only and has no
implementation task.

## Contributing

- Send a properly reviewed pull request, or at least a well-thought-out issue.
- Write me a message on Nostr: [fred@hodl.ar](https://njump.me/fred@hodl.ar)
- Send me a coffee through the Lightning Network at `fred@lawallet.io`.

## License

Copyright (c) 2026 LU1AJR. Distributed under the
[BSD 2-Clause License](LICENSE).

The license covers TUPA's own code and documentation; data obtained from
upstream sources such as CAMMESA, ENRE, and OCEBA remains subject to its
respective terms.

## UGLY

* **ENRE TLS:** only legacy static-RSA TLS 1.2; TUPA uses `@SECLEVEL=1` for ENRE only, with certificate checks still on.
* **ENRE API(n't):** no API — data lives in generated `data_EDN.js` / `data_EDS.js`.

