"""Example: enroll a spa on the Wi-Fi, then query it.

Put the spa in pairing mode (Wi-Fi icon blinking) and run::

    python example.py --ssid MyWifi

The Wi-Fi password is prompted. A spa already on the network is found by
discovery when ``--ssid`` is omitted, or reached directly with ``--host``.
"""

import argparse
import asyncio
import getpass

from espspapy import Spa, SpaException, async_discover, async_pair


async def find_host(args: argparse.Namespace) -> str | None:
    """Return the IP address of the spa: enrolled, given or discovered."""
    if args.ssid:
        # Step 1 (first use only): enroll the spa on the Wi-Fi with ESP-Touch.
        # The spa is not on the network yet, so the credentials are broadcast
        # on the LAN and the spa Wi-Fi module sniffs them from the air. This
        # host must therefore be on the same network, ideally the same 2.4 GHz
        # access point (the spa module does not support 5 GHz).
        password = getpass.getpass(f"Wi-Fi password for {args.ssid}: ")
        print(f"Enrolling the spa on {args.ssid}, this can take a minute…")
        # Our local IP is sent to the spa so that it knows where to answer:
        # it is detected automatically (see get_local_ip()) unless given as
        # third argument. The BSSID (access point MAC) is optional too.
        result = await async_pair(args.ssid, password)
        # Once connected, the spa answers with its MAC and its new IP address.
        print(f"Spa module {result.mac} joined with IP {result.ip}")
        return result.ip

    if args.host:
        # The spa is already enrolled and its IP address is known.
        return str(args.host)

    # The spa is already enrolled but its IP address is unknown: ask every spa
    # of the LAN to introduce itself (UDP broadcast).
    print("Looking for spas on the network…")
    spas = await async_discover()
    for info in spas:
        print(f"Found spa {info.uid} at {info.ip}")
    # This example simply uses the first spa found.
    return spas[0].ip if spas else None


async def main(args: argparse.Namespace) -> None:
    """Run the example."""
    # Every espspapy error derives from SpaException (pairing failure, spa
    # unreachable, unexpected answer…), so a single except is enough.
    try:
        if (host := await find_host(args)) is None:
            print("No spa found")
            return

        # Step 2: query the spa. "async with" opens a persistent TCP connection
        # and keeps it alive with heartbeats; it is closed on exit.
        async with Spa(host) as spa:
            # Identity of the spa: unique id, IP address and device type.
            info = await spa.async_identify()
            print(f"Connected to spa {info.uid} ({info.ip})")

            # Current state of the spa, decoded into a SpaStatus.
            status = await spa.async_get_status()
            # Temperatures are expressed in the unit configured on the spa.
            unit = "°F" if status.fahrenheit else "°C"
            print(f"Power       : {'on' if status.power else 'off'}")
            print(f"Heater      : {'on' if status.heater else 'off'}")
            print(f"Filter      : {'on' if status.filter else 'off'}")
            print(f"Bubbles     : {'on' if status.bubbles else 'off'}")
            print(f"Temperature : {status.current_temperature} {unit}")
            print(f"Target      : {status.target_temperature} {unit}")

            # To control the spa, see Spa.async_set_device() and
            # Spa.async_set_temperature(), e.g.:
            #     await spa.async_set_device(Device.BUBBLES, True)
            #     await spa.async_set_temperature(38)
    except SpaException as err:
        print(f"Error: {err}")


parser = argparse.ArgumentParser(description="Enroll a spa, then query it.")
parser.add_argument("--ssid", help="2.4 GHz Wi-Fi network to enroll the spa on")
parser.add_argument("--host", help="IP address of a spa already enrolled")
asyncio.run(main(parser.parse_args()))
