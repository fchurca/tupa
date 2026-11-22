# Configuration

TUPA has no runtime dependencies beyond the Python standard library. Settings
come from three layers, in decreasing priority:

1. command-line arguments;
2. `TUPA_*` environment variables;
3. a JSON configuration file.

When running with Docker, environment variables are lifted from a `.env` file in
the project directory. Copy the [`stack.env`](stack.env) template to `.env` and
edit it; every supported variable is listed there with its default commented out.
Compose also forwards `TZ` and sets `RES_OPTIONS=timeout:1 attempts:1` to shorten
any resolver work done outside TUPA's bounded DNS child.

Values not supplied anywhere use the defaults below. Defaults are intended to be
sane and sufficient for normal use; configuration is optional. The JSON file is
selected with `--config PATH` or `TUPA_CONFIG`.

If no config file is found, TUPA logs an info message and uses defaults. If a
config file exists but cannot be read/parsed, or contains an invalid top-level
shape or unknown top-level keys, TUPA logs a warning and uses defaults while
ignoring the invalid part. With `--strict` / `TUPA_STRICT=true` / `"strict":
true`, missing config files still log info and use defaults, but existing
unloadable/invalid config files log an error and cause startup failure.

## Quick start

```json
{
  "selected_members": ["edenor", "edesur"],
  "serve": false,
  "host": "127.0.0.1",
  "port": 8000,
  "timeout": 10,
  "ttl": 60,
  "format": "json",
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

A **member** is a system whose status appears in the output. A **source** is one
datasource that contributes evidence about a member. JSON defines them
separately:

```json
{
  "selected_members": ["edenor"],
  "members": {
    "edenor": ["enre_edenor", "cammesa_edenor"]
  },
  "sources": {
    "enre_edenor": {
      "kind": "enre_outages",
      "member": "edenor",
      "url": "https://www.enre.gov.ar/paginacorte/js/data_EDN.js",
      "empresa": "EDENOR"
    },
    "cammesa_edenor": {
      "kind": "cammesa_demand",
      "member": "edenor",
      "region": 1077
    }
  }
}
```

Built-in members and sources are always available; JSON entries override or
extend them by name. A member's status is the **worst known** status of its
sources, ignoring `unknown` unless every source is unknown. Each output comment
is prefixed with the source name.

Default members and sources:

| Member | Sources |
| --- | --- |
| Edenor | ENRE outages, CAMMESA demand (region 1077) |
| Edesur | ENRE outages, CAMMESA demand (region 1078) |

Source kinds:

- `enre_outages`: requires `url` and `empresa`; classifies by affected customers.
  Every member using this kind needs a matching entry in `customers` (defaults
  cover `edenor`, `edesur` and `edelap`); startup fails with an explanatory
  message otherwise.
- `cammesa_demand`: requires `region`; classifies by demand deficit against the
  lower of yesterday and the same weekday last week. `url` may override the
  CAMMESA endpoint.

## Options

| Meaning | CLI | Environment | JSON key | Default |
| --- | --- | --- | --- | --- |
| Members to query | `MEMBER...`, `-m`, `--member`, `--source` | `TUPA_MEMBERS`, `TUPA_SOURCES` | `selected_members` | all members |
| Run the HTTP daemon | `--serve` | `TUPA_SERVE` | `serve` | `false` |
| Listen address | `--host ADDR` | `TUPA_HOST` | `host` | `127.0.0.1` |
| Listen port | `--port PORT` | `TUPA_PORT` | `port` | `8000` |
| Upstream timeout (seconds) | `--timeout SECONDS` | `TUPA_TIMEOUT` | `timeout` | `10` |
| Daemon cache TTL (seconds) | `--ttl SECONDS` | `TUPA_TTL` | `ttl` | `60` |
| Output format | `--format json` | `TUPA_FORMAT` | `format` | `json` |
| JSON indentation (0-8 spaces) | `--indent SPACES` | `TUPA_INDENT` | `indent` | `2` |
| JSON file path | `--config PATH` | `TUPA_CONFIG` | — | none |
| Strict config validation | `--strict` | `TUPA_STRICT` | `strict` | `false` |
| Output timezone | `--tz NAME` | `TUPA_TZ` | `tz` | system timezone |
| Customer baselines | `--customers NAME=COUNT,...` | `TUPA_CUSTOMERS` | `customers` | see below |
| Severity fractions | `--levels WARN,HIGH,DISASTER` | `TUPA_LEVELS` | `levels` | `0.0001,0.005,0.05` |
| Demand-deficit fractions | `--demand-levels WARN,HIGH,DISASTER` | `TUPA_DEMAND_LEVELS` | `demand_levels` | `0.15,0.30,0.50` |

Notes:

- Member and source names are case-insensitive and normalized to lowercase;
  they may contain letters, digits, underscores and hyphens.
- Repeated names are de-duplicated. Unknown members, unknown sources, and
  sources assigned to the wrong member are rejected.
- An empty selection means "unspecified" and falls back to all available
  entries: `selected_members: []`, empty `TUPA_MEMBERS`/`TUPA_SOURCES`, an empty
  CLI selection, a member with an empty source list, and `sources: {}` all
  select all members, all sources of that member, and all default sources
  respectively. Empty values are skipped in precedence order, so an empty
  `TUPA_MEMBERS` does not mask a non-empty `TUPA_SOURCES`.
- `TUPA_MEMBERS` takes precedence over the legacy alias `TUPA_SOURCES`; both
  override `selected_members` from JSON.
- Source URLs must be absolute `http://` or `https://` URLs.
- Booleans accept `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`
  (case-insensitive).
- `port` is 1-65535, `timeout` is at least 0.1, `ttl` is at least 0 (zero
  disables caching), and `indent` is 0-8. `format` only supports `json`.
- `timeout` is the overall deadline for a collection and the initial socket
  timeout for each upstream fetch. When the deadline expires, TUPA closes the
  active sockets of unfinished fetches, marks them `unknown` with a Spanish
  comment like
  `cammesa_edenor no respondió a tiempo después de 10 segundos`, and returns.
  Fetch workers are daemons, so they cannot keep the CLI process alive. DNS
  lookups run in short-lived child processes and are bounded by the remaining
  collection deadline; numeric hosts bypass the child. Redirects follow urllib
  defaults: at most 10 distinct URLs and 4 repeats of the same URL.
- Invalid values exit with status 2 and an explanatory message.

## Timezone

All timestamps in the output are normalized to one timezone. By default TUPA
uses the system timezone (`TZ` or `/etc/localtime`, falling back to the local
offset). Set `tz` / `TUPA_TZ` / `--tz` to override it with an IANA name such as
`America/Argentina/Buenos_Aires`, a fixed offset such as `-03:00`, or `UTC`.
`system` and `local` explicitly select the system default. The container image
includes `tzdata`, and Compose forwards the host `TZ` value (default `UTC`).

## HTTP endpoints

In `--serve` mode, `/` renders a standalone HTML dashboard and `/api` returns
the same status as JSON. The former `/status` and `/status.json` paths are not
served.

## ENRE severity thresholds

Severity for ENRE outages is based on the number of customers reported without
service, using rough published customer/household counts as baselines rather
than population figures. Two settings control it:

- `customers`: `name=count` pairs. Supplied pairs are **merged** with the
  defaults, so you can override a single member.
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
implemented source yet.

## CAMMESA severity thresholds

CAMMESA's regional endpoint returns 5-minute demand in MW for today, yesterday
and the same weekday last week. TUPA uses the lower of the two reference series
to reduce weather-related false positives, picks the sample closest to the
median deficit of the latest three comparable samples, and classifies that
sample's deficit with `demand_levels`. The comment reports the same displayed
values used for the classification.

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
