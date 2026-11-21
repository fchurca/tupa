# TUPA: Unified Argentine Power Telemetry

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
[example.json](example.json).

Configuration: [config.md](config.md).

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

### Topology

- [System topology and upstream dependencies](topology.md)
- [diagram](diagram.pu)

### Status

A `member` carries a `status` (`operational`, `warn`, `high`,
`disaster`, or `unknown`), a `comment`, and an `updated` timestamp. Colors
(green, yellow, orange, red, grey) are display hints only. A `system` has its
own `updated` timestamp and a `members` map keyed by member name. Timestamps
are ISO 8601 / RFC 3339 strings. See the [CDDL schema](status.cddl).

Planned outages are outages: they use the same severity thresholds as any other
outage.

## Todo

- [X] Spanish documentation
- [ ] POC
  - stdlib Python 3, callable once for cron or as a daemon for HTTP
  - Docker with python:3.12-slim
  - Implement JSON output
  - Implement Edenor and Edesur sources
- [ ] MVP
  - Add basic HTML output
  - Implement outgoing webhooks or triggers
- [ ] Pilot
  - Add classful SVG output
  - Add sources mapped in the topology: Transener, SADI, CAMMESA, SACME
  - Add Edelap source
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

