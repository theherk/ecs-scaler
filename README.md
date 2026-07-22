# ecs-scaler

Scale ECS services by environment, with include/exclude filtering.

Available from [GHCR](https://github.com/theherk/ecs-scaler/pkgs/container/ecs-scaler). Multi-arch images are published for `linux/amd64` and `linux/arm64`.

## How it works

Matches all ECS clusters with `-ENV` or `ENV-` in the name, retrieves all services in those clusters, then scales them to the given min/max. Filter results with `-i` (include) or `-e` (exclude).

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

### Docker

```
docker run --rm \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN \
  -e AWS_DEFAULT_REGION=eu-north-1 \
  ghcr.io/theherk/ecs-scaler:1.2.0 \
  scale dev -e reverse-proxy --min 2 --max 4
```

Or mount credentials:

```
docker run --rm -v ~/.aws:/root/.aws:ro ghcr.io/theherk/ecs-scaler:1.2.0 scale dev -l
```

## Development

Requires [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/).

```
mise run build      # Build multi-arch images locally
mise run publish    # Build and push to Docker Hub
mise run run -- dev -l  # Run locally via uv
```

## Publishing

Push a git tag to trigger the GitHub Actions workflow, which builds and publishes multi-arch images to GHCR:

```
git tag 1.2.0
git push origin 1.2.0
```
