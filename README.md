# DarktableMCP

<p align="center">
  <img src="icon.png" alt="DarktableMCP Logo" width="160"/>
</p>

> Developed by [Yadullah Abidi (YaddyVirus)](https://github.com/YaddyVirus)

A Model Context Protocol (MCP) server that lets you edit photos using Claude as your AI photo editor. Works with **Claude Desktop** and **Claude Code**.

Tell Claude what you want — *"make this warmer and more dramatic"*, *"crop to 16:9"*, *"recover the blown highlights"* — and it applies the edits, shows you a preview, and exports the final image. Supports RAW files (CR2, NEF, ARW, etc.) and JPEGs.

## Features

- 🖼️ **RAW file support** — CR2, NEF, ARW, RAF, DNG, ORF, RW2, and more
- 🎨 **Full editing toolkit** — exposure, white balance, contrast, highlights/shadows, saturation, vibrance, clarity, sharpness, noise reduction, vignette
- ✂️ **Crop & rotate** — free crop, aspect ratio crop (16:9, 4:3, 1:1…), straighten
- 📝 **Rename outputs** — give your exports meaningful names
- 📊 **Histogram analysis** — Claude checks clipping and tonal distribution before suggesting edits
- 💾 **Non-destructive** — all edits stored in a sidecar JSON; originals never touched
- 🔄 **Darktable XMP export** — edits written as `.xmp` sidecars Darktable can read
- 📋 **Batch copy settings** — apply one image's edits to many others

---

## Requirements

- Python 3.10 or newer
- [Claude Desktop](https://claude.ai/download) (free or Pro)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yadullahabidi/darktable-mcp.git
cd darktable-mcp
```

### 2. Install the package

```bash
pip install -e .
```

This installs all dependencies automatically:
- `mcp` — Model Context Protocol SDK
- `rawpy` — RAW file decoding
- `Pillow` — image processing
- `numpy` — array operations
- `piexif` — EXIF metadata
- `exifread` — reading camera EXIF from RAW files

### 3. Verify it works

```bash
python -m darktable_mcp
```

You should see it start (it will wait for MCP input — press Ctrl+C to exit). If it starts without errors, you're good.

---

## Connecting to Claude Desktop

### 1. Find your Claude Desktop config file

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |

### 2. Add the server

Open the config file and add the `mcpServers` block. If the file already has content, merge carefully — don't replace the whole file.

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

> **Tip:** If `python` isn't on your PATH, use the full path to the Python executable.  
> Windows example: `"C:\\Users\\YourName\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"`  
> Find it by running `where python` (Windows) or `which python3` (macOS/Linux) in a terminal.

### 3. Restart Claude Desktop

Fully quit (don't just close the window) and reopen. The **darktable** server should appear when you select Connectors in Claude Chat or Code.

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
3. Call `apply_adjustments` with specific values
4. Show you a preview (saved as `filename__preview.jpg` in your temp folder, under `darktable-mcp-previews`)
5. Iterate based on your feedback
6. Export the final image when you're happy

### Available tools

| Tool | What it does |
|------|-------------|
| `list_images` | List all images in a directory |
| `get_image_info` | EXIF metadata + current edit state |
| `get_image_preview` | Render and preview with edits applied |
| `apply_adjustments` | Exposure, WB, tone, colour, detail, effects |
| `crop_image` | Crop by coordinates or aspect ratio |
| `rotate_image` | Rotate / straighten |
| `reset_crop` | Remove crop, restore full frame |
| `rename_output` | Set the export filename |
| `export_image` | Export to JPEG / PNG / TIFF |
| `reset_edits` | Undo everything, back to original |
| `get_histogram` | Tonal/clipping analysis |
| `copy_settings` | Copy edits from one image to another |

### Example prompts

- *"What would you suggest to improve this shot?"*
- *"Make it look like a moody film photo"*
- *"Recover the blown sky and open up the shadows"*
- *"Straighten the horizon by about 1.5 degrees"*
- *"Export all photos in this folder with the same settings"*
- *"Reset everything and start fresh"*

---

## How edits are stored

Each image gets a companion `.mcp.json` sidecar file (e.g. `IMG_3500.mcp.json`) that stores all adjustments non-destructively. Your original RAW files are never modified.

On export, a Darktable-compatible `.xmp` sidecar is also written, so you can open the RAW in Darktable and see the edits there too.

---

## Troubleshooting

**Server doesn't appear in Claude Desktop**  
Make sure you fully quit and restarted Claude Desktop after editing the config. Check that the JSON is valid (no trailing commas, matching braces).

**`python` command not found**  
Use the full path to your Python executable in the config. Run `where python` (Windows) or `which python3` (macOS) to find it.

**RAW file fails to open**  
Some camera models need an updated version of `rawpy`. Run `pip install --upgrade rawpy`.

**Preview file not opening**  
The preview is always saved as `originalname__preview.jpg` in `%TEMP%\darktable-mcp-previews` (never next to your source file). Open it manually if your system doesn't pop it up automatically.

---

## Author

**Yadullah Abidi** — [@YaddyVirus](https://github.com/YaddyVirus)

## License

MIT
