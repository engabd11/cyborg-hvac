# Adding a brand

Cyborg HVAC keeps everything brand-specific in one folder. Entities, the coordinator and the zone logic only see the generic model in `core/model.py`.

## Steps

1. Create `custom_components/cyborg_hvac/backends/<brand>/`.
2. Write a backend that subclasses `core.backend.HvacBackend`:
   - `async_discover()` returns a `UnitInfo` per unit, with `Capabilities` (modes, fan speeds, setpoint range and step, features, zone slots and opening step).
   - `async_fetch()` returns a `UnitState` per unit.
   - `async_send(unit_id, command)` applies a `Command`. One command can change power, mode, setpoint, fan, features and several zones together, so send it as one request where the protocol allows.
   - Optional: `async_start_push(callback)` for push updates, `validate()` for brand rules, `async_close()`.
   Keep this code free of Home Assistant imports, so it can move into a PyPI library later.
3. Write `adapter.py` with `async_create_backend(hass, entry)` and `async_create_flow_backend(hass, data)`. This is the only file that may import Home Assistant.
4. Register the brand in `brands.py` and add its sign-in or connection steps to `config_flow.py`.
5. Add a fixture under `tests/fixtures/` and tests for status parsing and command translation.

`backends/demo/` is the smallest complete example.

## Mapping guidelines

- Map modes to `HvacMode`, fan speeds to the shared keys in `core/model.py` (`auto`, `low`, `medium`, `high` and so on) and features to `Feature`.
- Mark features as `verified=False` until confirmed on real hardware; their entities start disabled.
- Declare mode rules in `FeatureSpec.valid_modes` so Home Assistant explains a refused change before it reaches the device.
- Zones are numbered from 1. Report `ZoneState.temperature` when the brand has a sensor per zone.
