<p align="center">
  <img src=".github/banner.svg" width="100%" alt="Testbeds · 6G AI Traffic Characterization Testbed: Network Emulator Dashboard">
</p>

<p align="center">
  A web dashboard for applying, creating and scheduling network impairment profiles (delay,
  jitter, loss, bandwidth caps) with the 6G-Testbed's netemu.
</p>

<p align="center">
  <img alt="Status: under development"
    src="https://img.shields.io/badge/Status-Under%20Development-f39c12">
  <a href="https://github.com/5G-MAG/netemu-dashboard/releases"><img alt="Version"
    src="https://img.shields.io/github/v/release/5G-MAG/netemu-dashboard?label=Version&sort=semver"></a>
  <a href="LICENSE"><img alt="License: 5G-MAG Public License v1.0"
    src="https://img.shields.io/badge/License-5G--MAG%20PL%20v1.0-blue"></a>
</p>

<p align="center">
  <a href="https://www.5g-mag.com/testbeds/6g-testbed">Project page</a> &nbsp;&middot;&nbsp;
  <a href="https://github.com/5G-MAG/netemu-dashboard/issues">Issues</a> &nbsp;&middot;&nbsp;
  <a href="https://www.5g-mag.com/contributing">Contributing</a>
</p>

---

## At a glance

|  |  |
|---|---|
| **Part of** | [6G AI Traffic Characterization Testbed](https://www.5g-mag.com/testbeds/6g-testbed), alongside [6G-Testbed](https://github.com/5G-MAG/6G-Testbed) |
| **Depends on** | `netemu` from [6G-Testbed](https://github.com/5G-MAG/6G-Testbed/tree/main/netemu), tracked on `main` |

## Introduction

The Network Emulator Dashboard is a web UI and REST API on top of
[netemu](https://github.com/5G-MAG/6G-Testbed/tree/main/netemu), the 6G-Testbed's Linux
`tc`/`netem` wrapper. It applies a named impairment profile to a network interface with one
click, lets you define your own profiles, and cycles through a sequence of profiles on a timer.

Use it to put a device under test behind controlled, repeatable network conditions, for
example to watch a streaming client's adaptation and CMCD reports react to congestion. The
traffic shaping itself is netemu's; this repository is the control surface around it.

## Install dependencies

```bash
sudo apt install python3 python3-venv git iproute2
```

The kernel needs the `sch_netem`, `sch_htb` and `ifb` modules (stock on Ubuntu and Debian).

The dashboard runs as your normal user and only calls `sudo` for `tc`, `ip` and `modprobe`.
Allow those three without a password:

```bash
sudo visudo -f /etc/sudoers.d/netemu
```

```
<your-username> ALL=(ALL) NOPASSWD: /usr/sbin/tc, /usr/sbin/ip, /usr/sbin/modprobe
```

Check with `sudo -n tc -V`, which must not prompt. If it isn't set up, `run.sh` prints this
line with your username and this machine's paths.

## Downloading

```bash
cd ~
git clone https://github.com/5G-MAG/netemu-dashboard.git
```

## Running

```bash
cd netemu-dashboard
./run.sh
```

The first run creates `.venv` and installs the Python dependencies, including netemu from
6G-Testbed `main`. Then open `http://<host>:8090/`.

- **Interface & status**: the shaped interface, whether egress/ingress shaping is active, and
  whether `sudo` works.
- **Impairment profiles**: click a card to apply it (marked "✓ Active"). **Clear impairments**
  returns the interface to normal. **+ New profile** creates your own; custom profiles have
  edit (✎) and delete (×) buttons, built-ins don't.
- **Automated pattern**: add profiles as steps, set the interval, then **Start pattern**. With
  **Loop** it repeats; without, it clears after one pass. Applying a card or clearing also
  stops a running pattern.
- **Raw status**: the live `tc qdisc show` output for both directions.

The UI is a client of a REST API you can script against:

| Method | Path | Body | Effect |
|---|---|---|---|
| GET | `/api/profiles` | | All profiles, each with `is_custom` |
| GET | `/api/status` | | Interface, active rules, sudo check, pattern state |
| POST | `/api/apply` | `{"profile": "name"}` | Apply a profile |
| POST | `/api/clear` | | Remove all impairments |
| POST | `/api/interface` | `{"interface": "eno1"}` | Change the shaped interface |
| POST | `/api/pattern` | `{"sequence": ["a","b"], "interval_s": 30, "loop": true}` | Start cycling |
| POST | `/api/pattern/stop` | | Stop cycling and clear impairments |
| POST | `/api/profiles` | `{"name": "...", "delay_ms": 0, "loss_pct": 0, "rate_mbit": null, ...}` | Create or update a custom profile |
| DELETE | `/api/profiles/{name}` | | Delete a custom profile |

## Configuration

Copy [`config.example.yaml`](config.example.yaml) to `config.yaml` (gitignored). Every key is
optional:

| Key | Default | Meaning |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `8090` | Where the UI and API listen |
| `interface` | `auto` | Interface to shape |
| `bidirectional` | `true` | Shape ingress too, via an IFB device |
| `ifb_device` | `ifb0` | IFB device used for ingress |
| `profiles_path` | `profiles.yaml` | Built-in, read-only profiles |
| `custom_profiles_path` | `custom_profiles.yaml` | Where profiles created in the UI are saved |

Override any key for one run with `NETEMU_CONTROL_<KEY>`, e.g.
`NETEMU_CONTROL_INTERFACE=eno1 ./run.sh`. `NETEMU_CONTROL_CONFIG` points at another config file.

**Set `interface` explicitly on a host with more than one network interface.** `auto` picks the
interface holding the default route, which is usually not the one facing the device under
test. Find the right one with `ip route get <device-ip>`. When `interface` stays `auto`, the
last interface chosen in the UI is remembered in `.interface_state`, so a restart can't switch
interfaces and leave an impairment running on the old one.

Profiles created in the UI apply immediately. Edits to `profiles.yaml` need a restart.

## Development

The dependency on 6G-Testbed tracks its `main` branch: on every start, `run.sh` compares the
installed netemu with `main`'s head and reinstalls if it has moved. Offline, it keeps the
installed version and says so.

To work on netemu and the dashboard together, point `run.sh` at a local 6G-Testbed checkout:

```bash
NETEMU_SOURCE=/path/to/6G-Testbed ./run.sh
```

That installs the checkout's `netemu/` in editable mode. Run `./run.sh` without it to return to
`main`.

## Contributing

Contributions are welcome. How to raise an issue, fork the repository and open a pull request, and
the Contributor License Agreement required before code can be merged, are described at
<https://www.5g-mag.com/contributing>.

## License

Distributed under the 5G-MAG Public License v1.0. See [LICENSE](LICENSE).
