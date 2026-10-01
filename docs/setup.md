# Setup

## Prerequisites

- Linux (the process lock uses `fcntl.flock`, and skills run in their own process groups).
- Python **3.10 or 3.11** (`requires-python = ">=3.10,<3.12"`; OD-3). `uv` can install it for you.
- `git`.
- An Anthropic API key, for `run`, `batch` and `go2-bot`. You do not need one for `catalog`, `state`, `--reset-stub` or the test suite.
- A Telegram bot token, for `go2-bot` only.
- For the real robot (lab machine only): a Unitree Go2 EDU on a wired network interface, CycloneDDS 0.10.2, and the YOLO weights file.

## Install `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or see <https://docs.astral.sh/uv/getting-started/installation/>. Check with `uv --version`.

## Development / stub machine

```bash
git clone <repo-url> go2-dispatcher
cd go2-dispatcher
uv sync                    # core + dev dependencies; enough for the stub and the tests
```

This creates `.venv/` from `uv.lock`. Stub mode and the default tests need no SDK, no robot and no network.

## Lab machine (real robot)

### 1. CycloneDDS

`cyclonedds==0.10.2` (Python) builds against a CycloneDDS C install, which it finds through `CYCLONEDDS_HOME`. Build the C library at the matching release:

```bash
sudo apt install -y cmake build-essential
git clone https://github.com/eclipse-cyclonedds/cyclonedds -b releases/0.10.x
cd cyclonedds && mkdir build install && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=../install
cmake --build . --target install
export CYCLONEDDS_HOME="$(cd ../install && pwd)"
```

Add the `export CYCLONEDDS_HOME=...` line to your shell profile, so later `uv sync` runs can find it.

### 2. Robot and vision extras

```bash
cd go2-dispatcher
uv sync --extra robot --extra vision
```

- `robot`: `unitree_sdk2py` (from the `unitree_sdk2_python` git repository) and `cyclonedds==0.10.2`.
- `vision`: `ultralytics`, `opencv-python`, `numpy`.

**Fallback (OD-14).** If the `robot` extra cannot be resolved or built on a machine, install the two packages by hand into the project environment, then sync without removing them:

```bash
uv pip install cyclonedds==0.10.2
git clone https://github.com/unitreerobotics/unitree_sdk2_python
uv pip install -e ./unitree_sdk2_python
uv sync --inexact --extra vision
```

`docs/decisions.md` (T1) records that `uv lock` with the `robot` extra worked on the development machine, so the fallback was not needed there.

### 3. Network interface

Connect the robot by Ethernet and find the interface name:

```bash
ip a
```

Look for the wired interface on the robot's subnet (usually `192.168.123.x`; the robot is `192.168.123.161`). Its name is something like `enp0s31f6` or `eth0`. Put it in the config:

```toml
[robot]
backend = "real"
network_interface = "enp0s31f6"
```

The interface is never hardcoded. With `backend = "real"`, a blank `network_interface` is a config error.

### 4. YOLO weights

`detect_object` never downloads weights. Place the file at the configured path (default `models/yolov8n.pt`, relative to the base dir; `models/` is gitignored):

```bash
mkdir -p models
uv run python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"   # downloads to the cwd
mv yolov8n.pt models/
```

If the file is missing, `detect_object` returns the error `weights_missing`.

## Configuration and secrets

```bash
cp config.example.toml config.toml
cp .env.example .env
```

Edit `.env` (in the same folder as `config.toml`):

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456789:AA...
```

`config.toml` and `.env` are gitignored. Every key is described in `docs/configuration.md`. If you change `llm.model`, read Model compatibility there first.

## Verify

```bash
uv run go2-dispatch catalog          # prints system text, catalog, tool schema, registry hash; no key needed
uv run go2-dispatch state            # stub: prints a JSON state with "backend": "stub"
uv run go2-dispatch run "turn left 90 degrees, then tell me if you see a chair"
uv run pytest                        # all default tests pass with no key, robot or network
```

The `run` command needs `ANTHROPIC_API_KEY`. Its run log is written to `runs/` (see `docs/run-log.md`).

On the lab machine, with the robot on and standing, check the real backend:

```bash
uv run go2-dispatch --backend real state     # must return within about 1 s
```

(or set `backend = "real"` in `config.toml`). Then work through the supervised robot checklist in `docs/testing.md` before running real tasks. Read `docs/safety.md` first.
