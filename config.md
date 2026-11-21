# Configuration

TUPA has no runtime dependencies beyond the Python standard library. Every
setting can be supplied in three ways, in decreasing priority:

1. command-line arguments;
2. `TUPA_*` environment variables;
3. the `[tupa]` section of an INI file.

Values not supplied anywhere use the defaults below. The INI file itself is
selected with `--config PATH` or `TUPA_CONFIG`; a missing file is an error.
Interpolation is disabled, so `%` characters in values are literal.

## Quick start

```ini
[tupa]
sources = edenor, edesur
serve = no
host = 127.0.0.1
port = 8000
timeout = 10
ttl = 60
format = json
indent = 2
customers = edenor=3400000, edesur=2700000, edelap=400000
levels = 0.0001, 0.005, 0.05
```

```sh
python3 -m tupa --config tupa.ini
python3 -m tupa --config tupa.ini --serve
```

The same settings can come from the environment:

```sh
export TUPA_SOURCES=edenor,edesur
export TUPA_TTL=30
export TUPA_CUSTOMERS=edelap=500000
export TUPA_LEVELS=0.0002,0.002,0.02
python3 -m tupa --serve
```

## Options

| Meaning | CLI | Environment | INI key | Default |
| --- | --- | --- | --- | --- |
| Sources to query | `SOURCE...`, `--source SOURCE` | `TUPA_SOURCES` | `sources` | all available |
| Run the HTTP daemon | `--serve` | `TUPA_SERVE` | `serve` | `false` |
| Listen address | `--host ADDR` | `TUPA_HOST` | `host` | `127.0.0.1` |
| Listen port | `--port PORT` | `TUPA_PORT` | `port` | `8000` |
| Upstream timeout (seconds) | `--timeout SECONDS` | `TUPA_TIMEOUT` | `timeout` | `10` |
| Daemon cache TTL (seconds) | `--ttl SECONDS` | `TUPA_TTL` | `ttl` | `60` |
| Output format | `--format json` | `TUPA_FORMAT` | `format` | `json` |
| JSON indentation (0-8 spaces) | `--indent SPACES` | `TUPA_INDENT` | `indent` | `2` |
| INI file path | `--config PATH` | `TUPA_CONFIG` | — | none |
| Customer baselines | `--customers NAME=COUNT,...` | `TUPA_CUSTOMERS` | `customers` | see below |
| Severity fractions | `--levels WARN,HIGH,DISASTER` | `TUPA_LEVELS` | `levels` | `0.0001,0.005,0.05` |

Notes:

- `sources` accepts commas or semicolons; positional names and repeated
  `--source` flags are merged and de-duplicated. Unknown names are rejected.
- Booleans accept `1`, `true`, `yes`, `on` and `0`, `false`, `no`, `off`
  (case-insensitive).
- `port` is 1-65535, `timeout` is at least 0.1, `ttl` is at least 0 (zero
  disables caching), and `indent` is 0-8. `format` only supports `json`.
- Invalid values exit with status 2 and an explanatory message.

## Severity thresholds

Severity is based on the number of customers reported without service, using
rough published customer/household counts as baselines rather than population
figures. Two settings control it:

- `customers`: `name=count` pairs. Supplied pairs are **merged** with the
  defaults, so you can override a single source. Names are case-insensitive and
  normalized to lowercase; they may contain letters, digits or underscores, and
  counts are positive integers. `edelap` is included
  even though that source is not implemented yet.
- `levels`: exactly three increasing fractions greater than 0 and at most 1,
  corresponding to `warn`, `high` and `disaster`.

For each source the thresholds are computed at startup as
`ceil(baseline * fraction)`. A count below `warn` is `operational`; planned
outages can still downgrade the final status by one level.

Default baselines and resulting thresholds:

| Source | Customers | Warn ≥ | High ≥ | Disaster ≥ |
| --- | ---: | ---: | ---: | ---: |
| Edenor | 3,400,000 | 340 | 17,000 | 170,000 |
| Edesur | 2,700,000 | 270 | 13,500 | 135,000 |
| Edelap | 400,000 | 40 | 2,000 | 20,000 |

The default fractions are 0.01% / 0.5% / 5%. For example,
`TUPA_CUSTOMERS=edelap=500000` leaves Edenor and Edesur untouched and moves
Edelap to 50 / 2,500 / 25,000. `TUPA_LEVELS=0.0002,0.002,0.02` halves every
threshold while keeping the baselines.

Sources that do not report an affected-customer count return `unknown` and are
not classified by these thresholds.
