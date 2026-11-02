# espspapy

Unofficial asynchronous Python library, compatible with the **Intex PureSpa**
Wi-Fi models released before 2024: it enrolls their ESP module on the Wi-Fi
network (ESP-Touch v1) and drives it on the local network.

> [!NOTE]
> This project is not affiliated with or endorsed by Intex. Intex and PureSpa
> are trademarks of their owner, used only to indicate compatibility. Newer
> models use another protocol and are not supported.

> [!WARNING]
> Use at your own risk: the library drives a heater and pumps. It comes without
> any warranty (see the license); keep an eye on the device when automating it.

## Installation

```bash
pip install espspapy
```

Requires Python 3.14+.

## Usage

```python
import asyncio

from espspapy import Device, Spa, async_discover, async_pair


async def main() -> None:
    # Enroll a module in pairing mode on the Wi-Fi (ESP-Touch)
    result = await async_pair(
        ssid="MyWifi",  # 2.4 GHz network
        password="secret",
        bssid="00:00:1A:2B:3C:4D",  # 2.4 GHz access point to join
        # local_ip="192.168.1.10",  # optional: this host, detected by default
    )
    print(result.mac, result.ip)

    # Find the modules of the LAN
    for info in await async_discover():
        print(info.uid, info.ip)

    # Drive a module (persistent connection + heartbeat)
    async with Spa("192.168.1.10") as spa:
        print(await spa.async_identify())
        status = await spa.async_get_status()
        print(status.current_temperature, status.target_temperature)
        await spa.async_set_device(Device.BUBBLES, True)
        await spa.async_set_temperature(38)


asyncio.run(main())
```

### Pairing

1. Hold the C/F Wi-Fi button of the control panel for about 5 seconds, until
   the Wi-Fi light blinks quickly: the module stays in pairing mode for about
   one minute.
2. Run `async_pair()` (or `espspapy pair`) from a host on the 2.4 GHz network.
   The credentials are sent with ESP-Touch v1 in broadcast mode (multicast is
   not used).
3. Open a TCP connection to the module right after the pairing (`Spa(ip)`):
   without a client on port 8990, the module may drop off the network.

The TCP server accepts a single client: close the original mobile client
before driving the module, and the other way round.

### API

| Call                                                    | Description                                                               |
| ------------------------------------------------------- | ------------------------------------------------------------------------- |
| `async_pair(ssid, password, local_ip=None, *, bssid)`   | Wi-Fi pairing, returns `PairingResult(mac, ip)`                           |
| `get_local_ip()`                                        | IP address of this host on the LAN                                        |
| `get_current_bssid(interface=None)`                     | BSSID of the 2.4 GHz access point this host is on (Linux), else `None`    |
| `async_discover(timeout)`                               | LAN discovery, returns `list[SpaInfo]`                                    |
| `Spa(host).async_identify()`                            | `SpaInfo(uid, ip, dtype)`                                                 |
| `Spa(host).async_get_status()`                          | `SpaStatus`                                                               |
| `Spa(host).async_set_device(device, on)`                | Switch `Device.POWER`, `FILTER`, `HEATER`, `JETS`, `BUBBLES`, `SANITIZER` |
| `Spa(host).async_toggle(device)`                        | Raw toggle                                                                |
| `Spa(host).async_set_temperature(value)`                | Target temperature, in the device unit (10-40 °C or 50-104 °F)            |
| `Spa(host).async_send_frame(frame)`                     | Raw hex frame (checksum added)                                            |
| `Spa(host).async_heartbeat()`                           | Keep-alive, when `heartbeat_interval=None`                                |
| `Spa(host).async_set_timers(device, slots)`             | Replace the timers of `FILTER`, `HEATER` or `SANITIZER` (untested)        |
| `build_timer_frame(device, slots)`                      | Timer frame without checksum, `[]` clears the timers                      |

Exceptions derive from `SpaException`: `SpaConnectionError`,
`TimeoutExceededError`, `SpaNotRespondingError` (the Wi-Fi module answered but
not the controller board), `InvalidFrameError`, `CommandError`, `PairingError`.

Timer slots are one-shot, relative to the time of sending:

```python
from datetime import datetime, timedelta

from espspapy import Device, TimerSlot

start = datetime.now() + timedelta(hours=2)
await spa.async_set_timers(Device.HEATER, [TimerSlot(start, 3, temperature=38)])
await spa.async_set_timers(Device.HEATER, [])  # clear
```

### Command line

```bash
espspapy discover
espspapy pair --ssid MyWifi  # BSSID detected if this host is on 2.4 GHz Wi-Fi
espspapy pair --ssid MyWifi --bssid 00:00:1A:2B:3C:4D
espspapy status 192.168.1.10
espspapy monitor 192.168.1.10 --interval 10
espspapy bubbles 192.168.1.10 on
espspapy temp 192.168.1.10 38
espspapy --json status 192.168.1.10
```

## Protocol

| Port              | Protocol      | Use                                                        |
| ----------------- | ------------- | ---------------------------------------------------------- |
| 7001 →            | UDP broadcast | ESP-Touch v1 pairing (lengths of packets)                  |
| 18266 ←           | UDP           | Pairing answer: marker, MAC, IP                            |
| 10549 → / 10500 ← | UDP broadcast | Discovery (`spa_request`)                                  |
| 8990              | TCP           | JSON messages `{"data", "sid", "type"}`                    |
| 23                | TCP           | Debug shell of the firmware (e.g. `task-stats`), see below |

Message types: `0` heartbeat, `1` command, `2` status report, `3` identification,
`7` firmware version (answer in `data`, not used by this library).
Frames are hex strings followed by the checksum `255 - sum(bytes) % 255`.
The status request is `8888060FEE0F01DA`; the answer is `FFFF110F01…`:

| Byte  | Content                                                                                                  |
| ----- | -------------------------------------------------------------------------------------------------------- |
| 5     | Devices: bit 0 power, 1 filter, 2 heater, 3 jets, 4 bubbles, 5 sanitizer, 6 heater (temperature reached) |
| 6     | Flashing: bit 0 error code                                                                               |
| 7     | Water temperature, or error code + 100 (E81…E99, 200 = END)                                              |
| 12-14 | Filter, sanitizer, heater timers (bit 7 active, half hours)                                              |
| 15    | Target temperature (10-40 °C or 50-104 °F)                                                               |

The toggle and temperature frames belong to command set 1/3; other models may
use other frames. Switching the heater on also starts the filter (bits 1 and 2
set together), and switching it off stops both.

### Device behaviour

From the user manual of a 2024 model with the same control panel; older models
are expected to behave the same way, which is not confirmed.

| Code | Meaning                                                                    |
| ---- | -------------------------------------------------------------------------- |
| E81  | Transmission failure                                                       |
| E90  | No water flow                                                              |
| E91  | Low salt level                                                             |
| E92  | High salt level                                                            |
| E94  | Water temperature too low                                                  |
| E95  | Water temperature too high                                                 |
| E96  | System error                                                               |
| E97  | Dry-run protection                                                         |
| E99  | Broken water temperature sensor                                            |
| END  | After 72 hours of continuous heating, the pump goes to an extended standby |

- Timers set from the control panel: filter 2, 4 or 6 hours (the cycle restarts
  every day at the same time), sanitizer 3, 5 or 8 hours, heater start delay 0
  to 120 hours and heater duration 0 to 48 hours. The controller only counts
  down: it has no clock.
- Because of the power limit, only these combinations can run together: filter,
  heater and bubbles; filter, heater and jets; filter, bubbles, sanitizer and
  jets (without the heater).
- Jets and bubbles stop by themselves after 30 minutes.
- Frost protection: below 4 °C, the filter and the heater start until the water
  reaches 10 °C.
- The default target temperature is 20 °C.

### Timers

The module has no clock: the decompiled mobile client sends each timer list as
delays from the time of sending, and resends it after every change.
`Spa.async_set_timers()` sends one-shot slots of the filter, heater or
sanitizer; the layout below comes from the decompiled code, only the combined
frame was seen in a capture, and **these frames were not tested on a device
yet** (see `debug.py`).

| Frame        | Content                                                                  |
| ------------ | ------------------------------------------------------------------------ |
| `8888280F09` | Filter timers                                                            |
| `8888280F0A` | Heater timers                                                            |
| `8888280F0B` | Sanitizer timers                                                         |
| `8888710F11` | The three lists (filter, heater, sanitizer), then the target temperature |

Each list is `55` (or `00` to clear it) followed by up to 7 slots of 5 bytes,
padded with zeros to 36 bytes; the frame ends with the checksum. A one-shot slot
is:

| Byte | Content                                                                   |
| ---- | ------------------------------------------------------------------------- |
| 1    | Hours before the start, modulo 24 (modulo 168 for a long slot)            |
| 2    | Minutes before the start, bit 7 set for a long slot (24 hours or more)    |
| 3    | Duration in hours                                                         |
| 4    | Day bit: `1 << (hours before the start + duration) // 24` (`// 168` long) |
| 5    | Target temperature in °C (heater only, `00` otherwise)                    |

The library accepts slots that end within 7 days and last 1 to 48 hours. Weekly
slots set bit 7 of the day byte and use a mask of the selected days; their
exact layout is not confirmed, so they are not supported. The combined frame is
not sent either.

### Network and security

- The debug shell on TCP port 23 is open without authentication: keep the
  module on a trusted network.
- The module also connects to a cloud service
  (`intexapphub.azure-devices.net`, Azure IoT Hub). Local control does not need
  it, so it can be blocked with a DNS rewrite.

## Sources

- ESP-Touch v1: [EspressifApp/EsptouchForAndroid](https://github.com/EspressifApp/EsptouchForAndroid),
  [KurdyMalloy/EsptouchPython](https://github.com/KurdyMalloy/EsptouchPython),
  [AtharvSavarkar/ESPTouch-Python](https://github.com/AtharvSavarkar/ESPTouch-Python)
- Pairing procedure: [Home Assistant integration, issue #51](https://github.com/mathieu-mp/homeassistant-intex-spa/issues/51),
  [manufacturer FAQ](https://www.intexdevelopment.com/support/faqlist?catkey=306&lst_lang=2)
- TCP protocol, message type 7, debug shell, cloud service and captured
  schedule frame: [djo1338/intex-mqtt-bridge, PROTOCOL.md](https://github.com/djo1338/intex-mqtt-bridge/blob/main/docs/PROTOCOL.md)
  and [REVERSE-ENGINEERING.md](https://github.com/djo1338/intex-mqtt-bridge/blob/main/docs/REVERSE-ENGINEERING.md)
- Commands and status frame: [mathieu-mp/aio-intex-spa](https://github.com/mathieu-mp/aio-intex-spa),
  [Home Assistant community thread](https://community.home-assistant.io/t/intex-pure-spa-wifi-control/323591)
- Error codes, timer ranges, power limit and automatic stops:
  [user manual of a 2024 model (351IO), in French](https://www.irripiscine.fr/media/akeneo_connector/asset_files/N/o/Notice_258b.pdf)
- Off-state temperature value and heater/filter dependency:
  [adrianjagielak/homebridge-intex-plus](https://github.com/adrianjagielak/homebridge-intex-plus/blob/latest/src/spaAccessory.ts)

## Interoperability

The vendor no longer provides a client for these modules. The protocol is
documented here for interoperability only, so that owners keep the use of their
hardware (article L122-6-1 of the French intellectual property code, article 6
of Directive 2009/24/EC).

- The library is an independent implementation: it contains no code from the
  original client, only the information needed to talk to the module (ports,
  frames, encodings).
- Neither the original client nor its decompiled code is distributed with this
  project, and none should be contributed.
- New features should preferably be validated with network captures of the
  original client (observation of its behaviour) rather than with decompiled
  code.

## Development

```bash
uv sync --extra dev
uv run prek run --all-files
uv run mypy espspapy tests
uv run pytest
```

`debug.py` prints the timer frames of a few examples; with `--host` and
`--send`, it sends them and reads the status before and after each one:

```bash
uv run python debug.py
uv run python debug.py --host 192.168.1.10 --send filter clear-filter
```
