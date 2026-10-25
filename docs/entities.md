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

Switching on a feature the unit would refuse in its current mode is stopped with a clear message instead of failing silently in the cloud.

## Zone

| Entity | What it does |
|---|---|
| Damper (cover) | Open, close, or set the opening from 0 to 100%. Closing switches the zone off and keeps its opening, so opening it again restores the same airflow. Setting 0% switches the zone off. Openings round to the controller's step (5% on the HACZ01). |
| Power (switch) | Switches the zone on or off without changing its opening. |
| Temperature | The zone's own temperature, for brands that report one per zone (the demo unit does). |
