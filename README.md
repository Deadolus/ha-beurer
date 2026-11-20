# ha-beurer
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/deadolus/ha-beurer)
![Hassfest](https://github.com/deadolus/ha-beurer/actions/workflows/hassfest.yaml/badge.svg)
![HACS](https://github.com/deadolus/ha-beurer/actions/workflows/hacs.yml/badge.svg)

Home Assistant integration for BLE based Beurer TL100/wellbeing/Daylight therapy lamp

Supports controlling BLE based lights controllable through the Beurer LightUp app.

## Installation

Requires **Home Assistant 2026.11 or newer**. Enable Home Assistant's [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/) and use a supported local adapter or an ESPHome Bluetooth proxy with active connections enabled. Advertisement-only proxies cannot control this lamp.

Note: Restart is always required after installation.

### [HACS](https://hacs.xyz/) (recommended)
Installation can be done through [HACS custom repository](https://hacs.xyz/docs/faq/custom_repositories).

### Manual installation
You can manually clone this repository inside `config/custom_components/beurer`.

For  example, from Terminal plugin:
```
cd /config/custom_components
git clone https://github.com/deadolus/ha-beurer beurer
```

## Setup
After installation and a restart, open **Settings → Devices & services**. Nearby TL100 lamps appear as discovered integrations. Confirm the discovered lamp, or choose **Add integration → Beurer** to select a lamp or enter its Bluetooth MAC address manually.

Setup connects and reads both daylight and mood-light status; it does not blink the lamp or change its settings. Keep the lamp powered and within Bluetooth range. Close the Beurer LightUp app if it is holding the lamp's connection.

The setup needs to be repeated for each light.

Existing Beurer entries and light entity IDs are retained when upgrading; do not delete and re-add them. Restart Home Assistant after replacing the integration files.

## Features
1. Discovery through Home Assistant's shared Bluetooth stack, including connectable proxies
2. On/off, RGB color, daylight brightness, and mood-light brightness
3. Multiple light support
4. Light modes (Rainbow, Pulse, Forest, ..) as found in the app

### Daylight and mood-light controls

The light supports `rgb` and `white` color modes. A brightness-only action dims the current mode. Setting `rgb_color` or an effect selects mood light; setting `white` selects daylight. Switching modes disables the other channel. Turning the light off disables both channels.

```yaml
# Daylight at brightness 200/255
action: light.turn_on
target:
  entity_id: light.beurer
data:
  white: 200
```

```yaml
# Red mood light at brightness 128/255
action: light.turn_on
target:
  entity_id: light.beurer
data:
  rgb_color: [255, 0, 0]
  brightness: 128
```

## Known issues
1. An unreachable lamp causes setup to retry automatically. After setup, connection loss marks the light unavailable, and a status refresh attempts reconnection every 30 seconds. Check Bluetooth range, proxy connection slots, and competing phone connections if it stays unavailable.
2. The rightmost LED, which signals Bluetooth connection, on the lamp will always be on.
   This is because HomeAssistant will always be connected to the lamp, use black duct tape if you want the lamp to be completely dark.

## Not supported
Timers for automatic turn off of the lamp are currently not supported. 
Use HomeAssistant automations if you need this functionality.

## Debugging
Add the following to `configuration.yaml` to show debugging logs. Include debug logs and your Home Assistant version when filing an issue.

See the [logger integration docs](https://www.home-assistant.io/integrations/logger/) for more information about logging.

```yml
logger:
  default: warn
  logs:
    custom_components.beurer: debug
```

## Development verification

Use Python 3.14 and an isolated environment:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests
```

Regression tests use real Home Assistant 2026.11.2 setup, entity, service, and flow APIs with simulated TL100 GATT traffic. They cover color/white control, reconnects, incomplete or malformed notifications, setup validation, and connection failures. They do not verify physical lamp behavior or render the frontend.


## Credits
This integration will is a fork of [sysofwan ha-triones integration](https://github.com/sysofwan/ha-triones), whose framework I used for this Beurer integration. 

Additional help by: 
- [@pyromaniac2k](https://github.com/pyromaniac2k)
- [@ALandOfDodd](https://github.com/LandOfDodd)
