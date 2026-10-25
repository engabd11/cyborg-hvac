# Smart comfort

Cyborg HVAC can run your ducted system the way a careful owner would: the right mode for the weather, air only where people are, every room held in its comfort range, and work moved to when it is cheapest. It always explains itself first, and only acts on its own where you switch that on.

## Set it up

1. **House sensors and weather** (integration page, **Configure**): an outdoor temperature sensor, a weather forecast (the BOM integration works well), the people who live there, and a grid power sensor so spare solar can be spotted.
2. **Comfort targets** (same place): heating and cooling targets for day, night and away, the highest comfortable humidity, and when night starts and ends. Below the heating target a room is too cold; above the cooling target it is too warm; in between is comfortable.
3. **Each zone** (the zone's settings): its room temperature and humidity sensors, presence or motion sensors, window or door sensors, a comfort offset (for example a bedroom 1 degree cooler), its room type, whether it is a guest zone, opening limits, and whether it may be opened to keep airflow up (a spill zone).

Anything you leave out simply switches off the advice that needs it.

## Advice

The **Advice** sensor and the HVAC card list what would help right now, most urgent first. Examples:

- Heating while rooms are already too warm, or cooling while they are cold.
- Rooms comfortable and outside mild: turn off and let the house coast.
- Outside cooler than inside on a summer evening: open windows instead.
- Rooms uneven but fine on average: run the fan to mix them.
- Humid rooms: dry mode.
- A heating setpoint of 26 or more, or cooling of 21 or less: each degree costs about 10% more and does not heat or cool faster.
- Too few zones open, an empty room left open, a window open while its zone runs, or someone in a closed zone.
- Everyone out: set back for a short trip, turn off for a long one.
- A hot day or a cold night in the forecast: pre-cool or pre-heat while it is cheaper.
- Spare solar: bank some comfort now.

Apply a recommendation from the card or with `cyborg_hvac.apply_recommendation`; dismiss it for a while with `cyborg_hvac.dismiss_recommendation`.

## Guest mode

Mark the living and dining zones as guest zones. With **Guest mode** on, they get full comfort and priority, and the other zones only need to stay tolerable, so the unit works for the rooms your guests are in.

## Autopilot

Four switches, each off until you turn it on:

| Behaviour | What it does |
|---|---|
| Presence zoning | Opens rooms people are in, trims or closes rooms empty for 30 minutes, sets back or turns off while everyone is out, and puts things back when someone returns. |
| Room balancing | Shares air by need so every room holds its range: the room furthest from comfort gets full air, others less. It also moves the unit's setpoint, because the unit only measures return air, which reads differently from the rooms. |
| Weather pre-conditioning | Uses the hourly forecast and what it has learned about your rooms to start early, so rooms are comfortable when night or morning begins, or before a heat spike. |
| Solar shifting | While you export spare solar, cools or warms up to a degree further within comfort, and puts things back when the surplus ends. |

Autopilot always stays inside guard rails: it never closes the last zones, keeps enough airflow for the fan, waits at least 8 minutes between turning the compressor on and off, spaces setpoint changes, and limits how often it writes to the cloud.

**It respects people.** If someone changes a zone or the unit in Home Assistant, the ConnectLife app or on the wall controller, autopilot leaves that alone for 2 hours. The card marks held zones with a hand. **Resume** (or `cyborg_hvac.resume_autopilot`) ends holds early; **Pause 2 h** (or `cyborg_hvac.pause_autopilot`) holds everything.

## Learning

Cyborg HVAC learns how fast each room loses heat to outside and how quickly the unit warms or cools it. It starts from typical values and becomes confident after a few hours of each, which makes pre-conditioning start at the right time. What it learns survives restarts.
