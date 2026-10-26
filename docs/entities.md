# Entities

Each unit is a device, and each zone is its own device linked to the unit, so zones can sit in their own areas.

## Unit

| Entity | What it does |
|---|---|
| Climate | Power, mode, setpoint, fan speed and presets (Powerful, Energy saving, Sleep). The current temperature is the unit's own reading, which on ducted units is the return air. Silent appears as a fan speed on units with Quiet; choosing it turns Quiet on. |
| Powerful | Runs the compressor at full speed to reach the setpoint quickly. Works while heating or cooling. |
| Quiet | Lowers fan noise. Works in cool, heat and fan modes. |
| Energy saving | Limits output to save power. |
| Sleep | Eases the setpoint overnight and turns the fan down. A switch on the HACZ01; a list on units with family variants. |
| Health | Air purifying (HI-NANO, UVC or plasma, depending on the model). |
| 8 °C heat | Holds the home at 8 °C while away. Works in heat mode. |
| Temperature offset | Corrects the temperature AI mode works from, on units with AI mode (not the HACZ01). Starts disabled. |
| Return air temperature | The unit's temperature reading as a sensor, for graphs and automations. |
| Voltage | Supply voltage, where reported. |
| Connected | Whether the unit is reachable through the cloud. |
| Fault | On when the unit reports a fault; the codes are listed as attributes. |
| Filter | Filter cleaning reminder. Starts disabled until its meaning is confirmed on ducted units. |
| Advice | How many smart comfort recommendations are open; the top one and the full list are attributes. |
| Guest mode | Puts guest zones first. |
| Climate Butler | Hands the unit to the Butler: mode, setpoint, fan and every damper. Off until you turn it on. Attributes show manual holds and the last change with its reason. (Autopilot: room balancing before 0.7.) |
| Bedtime | Holds the sleep zones within half a degree of the sleep temperature. |
| Climate Butler: presence zoning, Climate Butler: weather pre-conditioning | What the Butler takes into account, each off until you turn it on. |
| Energy | Auto, Eco, Mid or Max: how much comfort is worth right now. Attributes say which mode is in use and why. |
| Climate Butler activity | What the Butler is doing in one word (heating, cooling, drying, ventilating, idling, watching, resting, away, holding, paused, off), with the headline, reason and watched rooms as attributes. |
| Sleep temperature | The temperature bedtime holds sleep zones at. |

Switching on a feature the unit would refuse in its current mode is stopped with a clear message instead of failing silently in the cloud.

## Zone

| Entity | What it does |
|---|---|
| Damper (cover) | Open, close, or set the opening from 0 to 100%. Closing switches the zone off and keeps its opening, so opening it again restores the same airflow. Setting 0% switches the zone off. Openings round to the controller's step (5% on the HACZ01). |
| Power (switch) | Switches the zone on or off without changing its opening. |
| Temperature | The zone's own temperature, for brands that report one per zone (the demo unit does). |
| Occupied | Whether anyone is in the room, from its presence sensors with the presence hold applied. Only on zones with presence sensors. |

See the [README](https://github.com/engabd11/cyborg-hvac#readme) for every option and behaviour.
