# DarktableMCP

<p align="center">
  <img src="icon.png" alt="DarktableMCP Logo" width="160"/>
</p>

> A fork of [Yadullah Abidi (YaddyVirus)](https://github.com/YaddyVirus/darktable-mcp),
> maintained by [Yuri Schmaltz (yuri-schmaltz)](https://github.com/yuri-schmaltz).

A Model Context Protocol (MCP) server that lets you edit photos using Claude as your AI photo editor. Works with **Claude Desktop** and **Claude Code**.

Tell Claude what you want — *"make this warmer and more dramatic"*, *"crop to 16:9"*, *"recover the blown highlights"* — and it applies the edits, shows you a preview, and exports the final image. Supports RAW files (CR2, NEF, ARW, etc.) and JPEGs.

## What's different in this fork

- **Real darktable rendering.** When `darktable-cli` is installed (and the
  official darktable is recommended for accurate RAW demosaicing), this
  fork delegates the actual pixel pipeline to darktable's modules via
  the `.xmp` sidecar instead of re-implementing them in Pillow.  The
  Pillow path is now strictly a fallback for raster images.
- **Strict parameter validation.** Every numeric input is checked
  against documented ranges before touching the sidecar.
- **Cross-platform preview.** Works on Windows, macOS and Linux.
- **Batch operations.** `apply_settings_to_folder` copies the same edit
  recipe to every image in a directory.
- **Linear RAW histogram.** `get_raw_histogram` reads the sensor data
  before the tone curve so you can see true highlight clipping.
- **CI.** Tests run on Linux / macOS / Windows × Python 3.10–3.12, plus
  `pyright` lint.  See `.github/workflows/test.yml`.
- **Tests.** 48 unit tests covering state persistence, XMP emission,
  processor behaviour, batch tools, and darktable-cli control flow
  (mocked subprocess).

## Features

- 🖼️ **RAW file support** — CR2, NEF, ARW, RAF, DNG, ORF, RW2, and more
- 🎨 **Full editing toolkit** — exposure, white balance, contrast, highlights/shadows, saturation, vibrance, clarity, sharpness, noise reduction, vignette
- ✂️ **Crop & rotate** — free crop, aspect ratio crop (16:9, 4:3, 1:1…), straighten
- 📝 **Rename outputs** — give your exports meaningful names
- 📊 **Histogram analysis** — Claude checks clipping and tonal distribution before suggesting edits
- 💾 **Non-destructive** — all edits stored in a sidecar JSON; originals never touched
- 🔄 **Darktable XMP export** — edits written as `.xmp` sidecars darktable can read
- 📋 **Batch copy settings** — apply one image's edits to many others
- 🧪 **Tested** — 48 unit tests in `tests/`, plus `pyright` lint in CI

---

## Requirements

- Python 3.10 or newer
- [Claude Desktop](https://claude.ai/download) (free or Pro)
- **Recommended:** [darktable](https://www.darktable.org/install/) (the
  `darktable-cli` binary enables proper RAW rendering)
- **Optional:** `rawpy` (only needed as a fallback if you want to render
  RAW files without installing darktable)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yuri-schmaltz/mcp-darktable.git
cd mcp-darktable
```

### 2. Create a virtual environment (recommended)

On modern Python installations `pip install` system-wide is blocked by
PEP 668.  Use a venv:

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
```

### 3. Install the package

```bash
pip install -e ".[dev]"   # includes pytest + the optional rawpy fallback
# or, for a minimal install:
pip install -e .
```

This installs all runtime dependencies automatically:
- `mcp` — Model Context Protocol SDK
- `Pillow` — image processing
- `numpy` — array operations
- `rawpy` *(optional, in `[raw]` extra)* — fallback RAW decoder

### 4. Verify it works

```bash
python -m darktable_mcp
```

You should see it start (it will wait for MCP input — press Ctrl+C to exit).
If it starts without errors, you're good.

### 5. (Recommended) Install darktable

Download from <https://www.darktable.org/install/> and make sure
`darktable-cli` is on your `PATH`:

```bash
darktable-cli --version
```

If `darktable-cli` is not on the PATH, the server still works but RAW
files will raise a clear error pointing to the install page.

### 6. Run the tests

```bash
pytest
```

---

## Connecting to Claude Desktop

### 1. Find your Claude Desktop config file

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |

### 2. Add the server

Open the config file and merge the `mcpServers` block from
[`claude_config.json`](claude_config.json) in this repo.  Don't replace
the whole file if it already has content.

**Windows:**
```json
{
  "mcpServers": {
    "darktable": {
      "command": "python",
      "args": ["-m", "darktable_mcp"]
    }
  }
}
```

**macOS / Linux:**
```json
{
  "mcpServers": {
    "darktable": {
      "command": "python3",
      "args": ["-m", "darktable_mcp"]
    }
  }
}
```

> **Tip:** If `python` isn't on your PATH, use the full path to the
> Python executable from inside the virtual environment you created in
> step 2.  For example
> `"C:\\Users\\YourName\\...\\mcp-darktable\\.venv\\Scripts\\python.exe"`.

### 3. Restart Claude Desktop

Fully quit (don't just close the window) and reopen.  The **darktable**
server should appear in the Connectors list.

---

## Usage

Once connected, just talk to Claude naturally:

```
My photos are in /Users/me/Pictures/Trip

Make IMG_3500 warmer and more punchy — it looks flat and cold.

Crop it to 16:9 and export as JPEG at maximum quality, name it "jodhpur_desert".
```

Claude will:
1. Call `list_images` to see your folder
2. Call `get_image_preview` to analyse the photo
3. Call `apply_adjustments` with specific values (out-of-range values
   are rejected with a clear error before any sidecar is touched)
4. Show you a preview (saved as `filename__preview.jpg` next to the
   source, auto-opened in the system image viewer on Windows / macOS / Linux)
5. Iterate based on your feedback
6. Export the final image when you're happy

### Available tools

| Tool | What it does |
|------|-------------|
| `list_images` | List all images in a directory |
| `get_image_info` | EXIF metadata + current edit state + `darktable_cli` availability |
| `get_image_preview` | Render and preview with edits applied |
| `apply_adjustments` | Exposure, WB, tone, colour, detail, effects (validated) |
| `crop_image` | Crop by coordinates or aspect ratio |
| `rotate_image` | Rotate / straighten |
| `reset_crop` | Remove crop, restore full frame |
| `rename_output` | Set the export filename |
| `export_image` | Export to JPEG / PNG / TIFF (via darktable-cli when available) |
| `reset_edits` | Undo everything, back to original (also cleans up `.xmp`) |
| `get_histogram` | Tonal/clipping analysis on the rendered preview |
| `get_raw_histogram` | Tonal analysis on linear sensor data (RAW only) |
| `copy_settings` | Copy edits from one image to another |
| `apply_settings_to_folder` | Batch-copy edits to every image in a folder |

### Example prompts

- *"What would you suggest to improve this shot?"*
- *"Make it look like a moody film photo"*
- *"Recover the blown sky and open up the shadows"*
- *"Straighten the horizon by about 1.5 degrees"*
- *"Export all photos in this folder with the same settings"*
- *"Reset everything and start fresh"*

---

## How edits are stored

Each image gets a companion `.mcp.json` sidecar file (e.g.
`IMG_3500.mcp.json`) that stores all adjustments non-destructively.  Your
original RAW files are never modified.

On export, a Darktable-compatible `.xmp` sidecar is also written, so you
can open the RAW in Darktable and see the edits there too.  When
`darktable-cli` is on the PATH, the same XMP is fed back into darktable
for the final render — giving you the full quality of darktable's
modules instead of a Pillow approximation.

`reset_edits` removes both the `.mcp.json` and any `.xmp` sidecar that
was created, so the directory is left clean.

---

## Troubleshooting

**Server doesn't appear in Claude Desktop**
Make sure you fully quit and restarted Claude Desktop after editing the
config.  Check that the JSON is valid (no trailing commas, matching
braces).

**`python` command not found**
Use the full path to the Python executable from your virtual environment
in the config (see tip above).

**`darktable-cli` not found**
The server works without it for JPEG/PNG/TIFF inputs, but RAW inputs
require either `darktable-cli` (preferred) or `rawpy` (install via
`pip install -e ".[raw]"`).

**RAW file fails to open**
Some camera models need an updated version of `rawpy`.  Run
`pip install --upgrade rawpy`.

**Preview file not opening**
The preview is always saved as `originalname__preview.jpg` next to your
source file.  The auto-open uses `os.startfile` on Windows, `open` on
macOS, and `xdg-open` on Linux — make sure the corresponding tool is
installed.  If auto-open fails, the tool's response includes a clear
status string so you can open the file manually.

**`Invalid parameter: 'exposure_ev'=99.0 is out of range [-5.0, 5.0]`**
You passed a value outside the documented range.  See
`src/darktable_mcp/edits.py` (`RANGES`) for the full list.

---

## Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=darktable_mcp

# Type check
pyright src/ tests/

# Regenerate logos (cross-platform)
python generate_logos.py ./logos
```

The fork is on the `feature/v0.3-batch-and-async` branch.  All work
should go through a worktree — see `AGENTS.md` for conventions.  To
sync with the upstream repo see `docs/UPSTREAM_SYNC.md` and the
`scripts/sync-upstream.sh` helper.

---

## Authors

- Original: **Yadullah Abidi** — [@YaddyVirus](https://github.com/YaddyVirus)
- Fork maintainer: **Yuri Schmaltz** — [@yuri-schmaltz](https://github.com/yuri-schmaltz)

## License

MIT — see [LICENSE](LICENSE).
