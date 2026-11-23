# Configuration

TUPA has no runtime dependencies beyond the Python standard library. Settings
come from three layers, in decreasing priority:

1. command-line arguments;
2. `TUPA_*` environment variables;
3. a JSON configuration file.

**Most users should not need to change any configuration.** The defaults are
intended to work out of the box. In deployments the usual settings are the
listening address/port and, occasionally, the timezone (which defaults to
Argentina Time). Timeout, TTL, severity thresholds and
member/source enabling are administrator-level controls:
changing them without a specific reason can produce misleading status or make
the service harder to operate.

When running with Docker, environment variables are lifted from a `.env` file in
the project directory. Copy the [`stack.env`](stack.env) template to `.env` and
edit it; every supported variable is listed there with its default commented
out. Compose interpolates `TZ` from the shell or `.env` for `TUPA_TZ=system`; it
does not discover the host timezone. Output defaults to Argentina Time.

Values not supplied anywhere use the defaults below. Configuration is optional.
The JSON file defaults to `config.json` in the working directory and can be
selected with `--config PATH` or `TUPA_CONFIG`.

If an explicitly selected config file is missing, TUPA logs an info message and
uses defaults. If a config file exists but cannot be read/parsed, or contains an
invalid top-level shape or unknown top-level keys, TUPA logs a warning, ignores the invalid part
and uses defaults for it. `--once` forces one-shot JSON output regardless of
`serve` settings. Invalid values exit with status 2.

## Quick start

```json
{
  "selected_members": ["edenor", "edesur"],
  "serve": false,
  "host": "127.0.0.1",
  "port": 8000,
  "timeout": 20,
  "ttl": 60,
  "indent": 2,
  "tz": "system",
  "customers": {"edenor": 3400000, "edesur": 2700000, "edelap": 400000},
  "levels": [0.0001, 0.005, 0.05],
  "demand_levels": [0.15, 0.30, 0.50]
}
```

Run commands from `src/`; relative paths are resolved from there. For the JSON
above saved as `tupa.json` in the repository root:

```sh
cd src
python3 -m tupa --config ../tupa.json
python3 -m tupa --config ../tupa.json --serve
```

The same settings can be supplied without a file:

```sh
export TUPA_MEMBERS=edenor,edesur
export TUPA_TTL=30
export TUPA_CUSTOMERS=edelap=500000
export TUPA_LEVELS=0.0002,0.002,0.02
export TUPA_DEMAND_LEVELS=0.05,0.15,0.30
python3 -m tupa --serve
```

## Members and sources

A **member** is a built-in system whose status appears in the output. A
**source** is one built-in datasource that contributes evidence about a member.
Configuration may only enable or disable built-ins; custom members, custom
sources, source kinds, endpoints, `empresa` values and regions are not
configurable.

```json
{
  "selected_members": ["edenor"],
  "members": ["edenor", "edesur"],
  "sources": [
    "enre_edenor",
    "enre_edesur",
    "cammesa_edenor",
    "cammesa_edesur"
  ]
}
```

- `members` enables built-in members; `sources` enables built-in sources.
- `selected_members` chooses which enabled members to query (CLI `MEMBER...`,
  `-m`/`--member` and `TUPA_MEMBERS` do the same).
- Empty lists mean "all built-ins". Disabling a source removes it from its
  member; a member with no enabled sources reports `unknown`.
- Selecting a member that is not enabled is a configuration error.
- Custom definitions or URL overrides are rejected. Built-in endpoints are
  fixed by TUPA.

Built-in members and sources:

| Member | Sources |
| --- | --- |
| Edenor | ENRE outages, CAMMESA demand (region 1077) |
| Edesur | ENRE outages, CAMMESA demand (region 1078) |

Source kinds:

- `enre_outages`: classifies by affected customers from ENRE's generated
  `data_EDN.js` / `data_EDS.js`. Severity thresholds come from `customers` and
  `levels` for the built-in member; every built-in ENRE member has a default
  baseline.
- `cammesa_demand`: classifies by demand deficit against the lower of yesterday
  and the same weekday last week, using fixed regional CAMMESA endpoints.

A member's status is the **worst known** status of its sources, ignoring
`unknown` unless every source is unknown. Each output comment is prefixed with
the source name.

## Options

| Meaning | CLI | Environment | JSON key | Default |
| --- | --- | --- | --- | --- |
| Members to query | `MEMBER...`, `-m`, `--member` | `TUPA_MEMBERS` | `selected_members` | all enabled members |
| Enabled built-in members | — | — | `members` | all built-ins |
| Enabled built-in sources | — | — | `sources` | all built-ins |
| Run the HTTP daemon | `--serve` | `TUPA_SERVE` | `serve` | `false` |
| Force one-shot mode | `--once` | — | — | off |
| Listen address | `--host ADDR` | `TUPA_HOST` | `host` | `127.0.0.1` |
| Listen port | `--port PORT` | `TUPA_PORT` | `port` | `8000` |
| Upstream timeout (seconds) | `--timeout SECONDS` | `TUPA_TIMEOUT` | `timeout` | `20` |
| Daemon cache TTL (seconds) | `--ttl SECONDS` | `TUPA_TTL` | `ttl` | `60` |
| JSON indentation (0-8 spaces) | `--indent SPACES` | `TUPA_INDENT` | `indent` | `2` |
| JSON file path | `--config PATH` | `TUPA_CONFIG` | — | `./config.json` |
| Output timezone | `--tz NAME` | `TUPA_TZ` | `tz` | Argentina Time (ART) |
| Customer baselines | `--customers NAME=COUNT,...` | `TUPA_CUSTOMERS` | `customers` | see below |
| Severity fractions | `--levels WARN,HIGH,DISASTER` | `TUPA_LEVELS` | `levels` | `0.0001,0.005,0.05` |
| Demand-deficit fractions | `--demand-levels WARN,HIGH,DISASTER` | `TUPA_DEMAND_LEVELS` | `demand_levels` | `0.15,0.30,0.50` |

Notes:

- Names are case-insensitive, normalized to lowercase, and may contain letters,
  digits, underscores and hyphens. Repeated names are de-duplicated.
- Values are trimmed. Empty selections fall back to all available entries:
  `selected_members: []`, empty or whitespace-only `TUPA_MEMBERS`, an empty CLI
  selection, `members: []` and `sources: []`.
- Unknown or disabled members, and unknown built-in source names, are rejected.
- `--once` wins over `--serve`, `TUPA_SERVE` and `"serve": true`.
- Booleans accept `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`
  (case-insensitive).
- `port` is 1-65535, `timeout` is at least 0.1, `ttl` is at least 0 (zero
  disables caching), and `indent` is 0-8.
- `timeout` is the overall deadline for a collection and the socket timeout
  budget for each upstream fetch. Fetches run in parallel under that one
  deadline, so the default 20 seconds bounds the whole collection, not each
  source. The socket budget is recomputed from the absolute deadline after
  connection setup and checked while reading the body, so a slow upstream
  cannot extend the collection indefinitely. Lower the timeout for tight
  health checks, raise it for slow upstreams.
- DNS lookups are made by the operating system resolver (`getaddrinfo`), which
  Python cannot interrupt: a slow lookup can consume the budget and mark a
  source unavailable even if the name would eventually resolve, and a stalled
  resolver keeps a worker busy until the OS gives up. The default leaves room
  for ordinary resolver delays; with a slow resolver or many nameservers,
  raise `TUPA_TIMEOUT` or point the host at a faster resolver.
- In daemon mode a cache miss refreshes synchronously, so the first request
  after the TTL expires can take up to the timeout to respond. If collection
  fails, the request returns an error instead of stale data.
- Redirects follow urllib defaults: at most 10 distinct URLs and 4 repeats of
  the same URL. Redirects to loopback, private, link-local or reserved addresses
  are rejected unless the original URL itself used such an address.
- Invalid values exit with status 2 and an explanatory message.

## Timezone

All timestamps in the output are normalized to one timezone. By default TUPA
uses Argentina Time (ART, `America/Argentina/Buenos_Aires`). TUPA does not probe
`/etc/localtime`: set `tz` / `TUPA_TZ` / `--tz` to an IANA name such as
`America/Argentina/Buenos_Aires` or `UTC` to override it. The values `system`
and `local` explicitly select the host `TZ` instead, falling back to ART when
`TZ` is unset or unresolvable; an invalid explicit TUPA setting is a
configuration error. The container image includes `tzdata`.

ENRE reports only a local time of day in ART; TUPA combines it with the file's HTTP
`Last-Modified` date and converts it to the configured output timezone. Without
that header, `updated` records when TUPA observed the data instead of assuming
a date.

## HTTP endpoints

In `--serve` mode, `/` renders a standalone HTML dashboard with a strict
Content-Security-Policy and no external scripts, and `/api` returns the same
status as JSON. `HEAD` is supported for both. The former `/status` and
`/status.json` paths are not served.

## ENRE severity thresholds

Severity for ENRE outages is based on the number of customers reported without
service, using rough published customer/household counts as baselines rather
than population figures. Two settings control it:

- `customers`: `name=count` pairs. Supplied pairs are **merged** with the
  defaults, so you can override a single built-in member.
- `levels`: exactly three increasing fractions greater than 0 and at most 1,
  corresponding to `warn`, `high` and `disaster`.

For each member the thresholds are computed at startup as
`ceil(baseline * fraction)`. A count below `warn` is `operational`. All reported
outages use these thresholds, including planned outages.

Default baselines and resulting thresholds:

| Member | Customers | Warn ≥ | High ≥ | Disaster ≥ |
| --- | ---: | ---: | ---: | ---: |
| Edenor | 3,400,000 | 340 | 17,000 | 170,000 |
| Edesur | 2,700,000 | 270 | 13,500 | 135,000 |
| Edelap | 400,000 | 40 | 2,000 | 20,000 |

Edelap is kept as a built-in baseline for compatibility even though it has no
implemented source.

## CAMMESA severity thresholds

CAMMESA's regional endpoint returns 5-minute demand in MW for today, yesterday
and the same weekday last week. TUPA uses the lower of the two reference series
to reduce weather-related false positives. Rows are ordered by reported time; a
zero demand reading is valid (a total collapse is `disaster`). TUPA picks the
sample closest to the median deficit of the latest three comparable samples,
and classifies that sample's deficit with `demand_levels`. The comment reports
the same displayed values used for the classification.

The default fractions are 15% / 30% / 50%:

| Deficit | Status |
| --- | --- |
| below 15% | `operational` |
| 15% to <30% | `warn` |
| 30% to <50% | `high` |
| at least 50% | `disaster` |

For example, `TUPA_DEMAND_LEVELS=0.05,0.15,0.30` makes the demand source more
sensitive while leaving customer thresholds unchanged. Sources without
comparable demand data return `unknown`.
