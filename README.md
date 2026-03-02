# desktop-hud

Configurable desktop overlay (HUD) for Wayland desktops. Renders images, videos (with alpha-channel support), and live graphs on a transparent GTK4 layer-shell surface.

## Features

- **Image elements** — display PNG, SVG, or any GdkPixbuf-supported format
- **Video elements** — looping video with explicit backend selection (`auto`, `simple`, `alpha-pipeline`)
- **Graph elements** — live-updating gauges and line charts via graph-lib
- **Click-through** — overlay does not intercept mouse input
- **Interactive edit mode** — outline elements, drag to move, corner-drag to resize
- **Smart snapping** — snap to screen edges/corners and sibling element edges
- **Layout profiles** — switch/save named layouts and autosave `last-used` recovery
- **HTTP API** — add, remove, and update elements at runtime
- **Config hot-reload** — send SIGHUP to apply config changes without restart

## Requirements

- GTK4 + PyGObject
- gtk4-layer-shell (with GObject Introspection typelib)
- GStreamer (used for all video playback) plus codec plugins:
  - `gstreamer1.0-libav` (common H.264/AAC decoding)
  - `gstreamer1.0-plugins-bad` (extra codecs, including many ProRes setups)
  - `gstreamer1.0-plugins-ugly`
  - GTK4 sink plugin providing `gtk4paintablesink` for alpha video
- PyYAML
- graph-lib (for graph elements)

## Configuration

Copy `config.example.yaml` to `config.local.yaml` and customize:

```yaml
overlay:
  layer: overlay
  namespace: desktop-hud
  click_through: true
  edit_mode: false
  edit_hotkey: Ctrl+Alt+M
  show_borders_in_edit_mode: true
  snap_threshold_px: 12
  min_size: { width: 32, height: 32 }
  interaction:
    debug_logging: false
    force_full_redraw_on_move: true
    disable_snap_modifier: Ctrl
    snap_hysteresis_px: 4

layouts:
  directory: layouts
  default_profile: default
  last_used_profile: last-used
  autosave_last_used: true
elements:
  - id: cpu-gauge
    type: graph
    renderer: gauge
    renderer_config:
      min_value: 0
      max_value: 100
      label: "CPU"
    provider:
      type: command
      command: "grep 'cpu ' /proc/stat | awk '{u=$2+$4; t=$2+$4+$5} END {printf \"%.0f\", u*100/t}'"
      interval_ms: 2000
    position: { x: 50, y: 50 }
    size: { width: 150, height: 150 }
    opacity: 0.8

api:
  enabled: true
  port: 7820
  main_thread_timeout_seconds: 5.0
```

Element source path behavior:
- `source` paths can be absolute, `~/...`, or relative to the package root (for example `assets/showcase/botanical-illustration.svg`).
- Image/video elements support `on_missing_source`:
  - `error` (default): fail element creation.
  - `skip`: skip the element without crashing the app.
  - `placeholder`: render a styled placeholder tile using `placeholder_label`.
- Video elements support `backend`:
  - `auto` (default): `alpha-pipeline` when `alpha: true`, else `simple`
  - `simple`: force `Gtk.Video`/`Gtk.MediaFile`
  - `alpha-pipeline`: force explicit GStreamer RGBA pipeline

Graph element behavior:
- `clear_before_draw` is optional and defaults to `false` to avoid transparent flash artifacts between updates.

## Usage

```bash
# Run directly
PYTHONPATH=src python3 -m desktop_hud

# As a systemd service
systemctl --user start desktop-hud

# Reload config
systemctl --user reload desktop-hud
# or: kill -HUP $(pidof -x desktop_hud)
```

## Troubleshooting Video Placeholders

If videos appear as black boxes with a `-` icon, the media backend could not decode the file.

```bash
# Check service logs for explicit playback/probe errors
journalctl --user -u desktop-hud.service -n 120 --no-pager

# Probe a file with GStreamer directly
gst-launch-1.0 -q filesrc location=/path/to/video.mp4 ! decodebin ! fakesink
```

Common causes:
- Missing codec plugins (install the packages listed above)
- Invalid source path
- File is not actually media (for example, an HTML download saved as `.mp4`)

For `alpha: true` videos, also verify:

```bash
gst-inspect-1.0 gtk4paintablesink
```

## API

The HTTP API runs on `http://127.0.0.1:7820` by default:

```bash
# Health check
curl http://localhost:7820/health

# List elements
curl http://localhost:7820/elements

# Add an element
curl -X POST http://localhost:7820/elements \
  -H 'Content-Type: application/json' \
  -d '{"id":"test","type":"image","source":"assets/showcase/corner-sigil.svg","on_missing_source":"placeholder","placeholder_label":"Test Image","position":{"x":100,"y":100},"size":{"width":80,"height":80}}'

# Update element
curl -X PATCH http://localhost:7820/elements/test \
  -H 'Content-Type: application/json' \
  -d '{"opacity":0.5,"position":{"x":200,"y":200},"size":{"width":120,"height":120}}'

# Remove element
curl -X DELETE http://localhost:7820/elements/test

# Reload config
curl -X POST http://localhost:7820/reload

# Toggle edit mode
curl -X POST http://localhost:7820/mode \
  -H 'Content-Type: application/json' \
  -d '{"edit_mode": true}'

# Inspect live interaction diagnostics
curl http://localhost:7820/mode/diagnostics

# Get current layout snapshot
curl http://localhost:7820/profiles/current

# Additively load a profile
curl -X POST http://localhost:7820/profiles/add \
  -H 'Content-Type: application/json' \
  -d '{"name":"extra-gauges"}'

# API dispatch timeout (main thread busy) returns HTTP 504 with JSON.
# Timeout is configurable with api.main_thread_timeout_seconds.

# Save current state to last-used profile
curl -X POST http://localhost:7820/profiles/save-last-used

# List/switch/save profiles
curl http://localhost:7820/profiles
curl -X POST http://localhost:7820/profiles/switch \
  -H 'Content-Type: application/json' \
  -d '{"name":"default"}'
curl -X POST http://localhost:7820/profiles/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"workspace-a"}'
```

## License

MIT
