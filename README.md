# ecs-scaler

Scale ECS services by environment, with include/exclude filtering.

Available from [GHCR](https://github.com/theherk/ecs-scaler/pkgs/container/ecs-scaler). Multi-arch images are published for `linux/amd64` and `linux/arm64`.

## How it works

Matches all ECS clusters with `-ENV` or `ENV-` in the name, retrieves all services in those clusters, then scales them to the given min/max. Filter results with `-i` (include) or `-e` (exclude). Filters are exact service names (no globs or substrings); an unknown name is an error.

`--min` and `--max` must be non-negative with `--min` not exceeding `--max`; otherwise the command exits 2 with a usage error.

If scaling one service fails, the rest are still attempted and the command exits 1 at the end. A fatal AWS error (e.g. no region or credentials configured, or AccessDenied on `ecs:ListClusters` or `ecs:ListServices`) stops the run and exits 1 with a one-line `error:` message on stderr.

## JSON output

`--output json` writes [JSON Lines](https://jsonlines.org/) to stdout for machine consumption; all logs and progress go to stderr. The default, `--output text`, prints human-readable progress to stdout.

One `service` object is emitted per service in scope, followed by exactly one `result` object as the last line. Services outside an include list are not emitted.

```json
{"type": "service", "cluster": "app-uat", "service": "prometheus-svc", "previous_desired": 1, "new_desired": 0, "running": 0, "status": "scaled", "reason": null}
{"type": "result", "env": "uat", "region": "eu-north-1", "min": 0, "max": 0, "dry_run": false, "filters": ["-e reverse-proxy"], "ok": true, "error": null}
```

`service` fields:

| Field              | Type           | Description                                                                                       |
| ------------------ | -------------- | ------------------------------------------------------------------------------------------------- |
| `cluster`          | string         | ECS cluster name                                                                                  |
| `service`          | string         | ECS service name                                                                                  |
| `previous_desired` | int or null    | Desired count before scaling                                                                      |
| `new_desired`      | int or null    | Previous desired clamped into `[min, max]`; unchanged previous desired for `failed` and `skipped` |
| `running`          | int or null    | Running count (after scaling, or before for dry run, skipped, and failed services)                |
| `status`           | string         | `scaled`, `converging`, `dry-run`, `skipped`, or `failed`                                         |
| `reason`           | string or null | Detail for `converging` (current desired), `skipped` (`excluded (-e)`), `failed` (AWS error)      |

`result` fields:

| Field     | Type           | Description                                                                  |
| --------- | -------------- | ---------------------------------------------------------------------------- |
| `env`     | string         | Environment argument                                                         |
| `region`  | string or null | Region resolved by the boto3 client; `null` if client creation failed        |
| `min`     | int            | Minimum capacity                                                             |
| `max`     | int            | Maximum capacity                                                             |
| `dry_run` | bool           | `true` with `-l`                                                             |
| `filters` | string[]       | Filters as given, e.g. `"-i prometheus-svc"`, `"-e reverse-proxy"`           |
| `ok`      | bool           | `false` if any service failed or a fatal error occurred; exit code is then 1 |
| `error`   | string or null | Fatal error that stopped the run before scaling; `null` otherwise            |

A fatal `error` is an unknown filter name or an AWS error during client setup or discovery (no region, no credentials, AccessDenied on `ecs:ListClusters` or `ecs:ListServices`). Per-service scaling failures do not set `error`; they are reported in that service's `reason`.

Counts come from `ecs:DescribeServices`, which is called only with `--output json`; text mode needs no extra permission. Calls are batched per cluster; if one fails (e.g. the permission is missing), a warning is logged, that batch's counts are `null`, other batches keep their counts, and scaling proceeds. `converging` means autoscaling has not yet moved desired to `new_desired`.

## Usage

```
scale [env] [options]
```

### Examples

Scale all services in dev to min 2 / max 4, excluding reverse-proxy:

```
scale dev -e reverse-proxy --min 2 --max 4
```

List matched services without scaling:

```
scale dev -e reverse-proxy -l
```

Emit JSON Lines for scripting:

```
scale dev -e reverse-proxy --min 2 --max 4 --output json > results.jsonl
```

### Docker

```
docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN \
  -e AWS_DEFAULT_REGION=eu-north-1 \
  ghcr.io/theherk/ecs-scaler:1.3.0 \
  scale dev -e reverse-proxy --min 2 --max 4
```

Or mount credentials:

```
docker run --rm -v ~/.aws:/root/.aws:ro ghcr.io/theherk/ecs-scaler:1.3.0 scale dev -l
```

## Development

Requires [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/).

```
mise run build      # Build multi-arch images locally
mise run publish    # Build and push to Docker Hub
mise run run -- dev -l  # Run locally via uv
uv run pytest       # Run tests
uvx ruff check .    # Lint
```

## Publishing

Push a git tag to trigger the GitHub Actions workflow, which builds and publishes multi-arch images to GHCR:

```
git tag 1.3.0
git push origin 1.3.0
```
