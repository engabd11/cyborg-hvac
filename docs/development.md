# Development

## Layout

```
custom_components/cyborg_hvac/
  core/        brand-agnostic model, backend interface, errors (no Home Assistant imports)
  backends/    one folder per brand; only adapter.py may import Home Assistant
  *.py         Home Assistant layer: config flow, coordinator, entity platforms
tests/         pure tests (no Home Assistant needed)
tests_ha/      Home Assistant harness tests (Linux only)
```

The coordinator merges writes made within 0.3 seconds into one backend command, shows the result straight away, and keeps showing commanded values until the cloud reports them (or 20 seconds pass).

## Environment

Home Assistant 2026.10 needs Python 3.14. With [uv](https://docs.astral.sh/uv/):

```
uv venv --python 3.14 .venv
uv pip install --python .venv pytest-homeassistant-custom-component ruff
```

## Checks

```
ruff check . && ruff format --check .
pytest tests -p no:homeassistant
```

The harness tests need Linux. On Windows or macOS, use Docker:

```
docker build -t cyborg-hvac-test -f dev/Dockerfile.test dev
docker run --rm -v "$PWD:/repo" cyborg-hvac-test
```

## Running Home Assistant locally

```
docker run -d --name cyborg-ha -p 8123:8123 \
  -v "$PWD/dev/config:/config" \
  -v "$PWD/custom_components:/config/custom_components" \
  ghcr.io/home-assistant/home-assistant:stable
```

Pick **Demo** when adding the integration to try it without hardware. For ConnectLife, use **Sign in, then paste the address**, because the sign-in redirect points at `homeassistant.local:8123` rather than your local container.
