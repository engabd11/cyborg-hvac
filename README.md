# Cyborg HVAC

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-green.svg)](https://github.com/engabd11/cyborg-hvac/blob/main/LICENSE)
[![Home Assistant 2025.7+](https://img.shields.io/badge/Home%20Assistant-2025.7%2B-blue.svg)](https://www.home-assistant.io)

Control your ducted air conditioner and every zone from Home Assistant: switch zones on and off, set how much air each zone gets, and use Powerful, Quiet, Sleep and Energy Saving just like the vendor app.

Cyborg HVAC starts with Hisense ducted systems on the ConnectLife app and is built so other brands can plug in.

## What you get

- **The unit as a thermostat**: power, mode (cool, heat, dry, fan, auto), setpoint in half degrees, fan speed and presets.
- **Every zone as its own device**: a damper you can open, close or set from 0 to 100%, plus a power switch. Zones keep their opening when switched off, as on the wall controller.
- **The app's features as switches**: Powerful, Quiet, Sleep, Health, Energy Saving and 8 °C heat, each checked against the current mode so Home Assistant explains why a change is refused.
- **Fast, light cloud use**: one request polls every unit, push updates arrive within seconds, and changes made together (a slider across five zones) go out as one command.
- **Smart comfort**: good-practice advice using your room, presence, outdoor, forecast and solar sensors, guest mode, and opt-in autopilot that zones by presence, balances every room, pre-conditions ahead of the weather and uses spare solar. See [smart comfort](https://github.com/engabd11/cyborg-hvac/blob/main/docs/smart-comfort.md).
- **A demo unit** that simulates rooms warming and cooling, for building dashboards before you connect anything.

## Works with

| Brand | App | Tested hardware |
|---|---|---|
| Hisense ducted | ConnectLife | HACZ01 Wi-Fi zone controller with HAAZ01 zone board |

Other Hisense air conditioners on ConnectLife (device type 009) load with their unit controls. See [supported devices](https://github.com/engabd11/cyborg-hvac/blob/main/docs/supported-devices.md).

## Requirements

- Home Assistant 2025.7 or newer.
- A ConnectLife account with your air conditioner added in the app. Google, Apple and email sign-in all work.

## Install

**HACS (custom repository)**

1. In HACS, open the menu, choose **Custom repositories**, add `https://github.com/engabd11/cyborg-hvac` as an **Integration**.
2. Install **Cyborg HVAC** and restart Home Assistant.

**Manual**

Copy `custom_components/cyborg_hvac` into your Home Assistant `config/custom_components` folder and restart.

## Set up

1. Go to **Settings → Devices & services → Add integration** and pick **Cyborg HVAC**.
2. Sign in to ConnectLife. **Sign in with your browser** returns to Home Assistant on its own when it runs at `homeassistant.local:8123`. Otherwise choose **Sign in, then paste the address** and paste the address your browser lands on.
3. Enter how many zones you use and name them as in the app.

Add, rename or remove zones later from the integration page.

## Documentation

- [Smart comfort](https://github.com/engabd11/cyborg-hvac/blob/main/docs/smart-comfort.md)
- [Entities](https://github.com/engabd11/cyborg-hvac/blob/main/docs/entities.md)
- [Card contract](https://github.com/engabd11/cyborg-hvac/blob/main/docs/card-contract.md)
- [Supported devices](https://github.com/engabd11/cyborg-hvac/blob/main/docs/supported-devices.md)
- [Adding a brand](https://github.com/engabd11/cyborg-hvac/blob/main/docs/adding-a-brand.md)
- [Development](https://github.com/engabd11/cyborg-hvac/blob/main/docs/development.md)

## Licence and credits

MIT. Parts of the ConnectLife client are adapted from Hisense's own MIT-licensed Home Assistant plugin, and the HACZ01 zone properties were first documented by the community `connectlife` project. See [NOTICE](https://github.com/engabd11/cyborg-hvac/blob/main/NOTICE).

Cyborg HVAC is an independent project, not affiliated with Hisense or ConnectLife.
