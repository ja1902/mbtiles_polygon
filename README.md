# Shaped MBTiles Generator for QGIS

Generate MBTiles from a polygon you draw on the map. Only creates tiles that actually intersect your shape. Uses clipping approach for efficient rendering.

## Features

- Draw any polygon shape, generate tiles for just that area
- **Pause/Resume drawing** - pause to pan/zoom, then resume where you left off
- **Undo last point** - Delete/Backspace key, Shift+Right-click, or middle-click
- **ESC to exit** - Press ESC to cancel and exit drawing mode
- **Pre-filters tiles before rendering** - only intersecting tiles are created
- **Uses clipping** - to render only pixels inside the polygon (more efficient)
- **Configurable metatile size** - prevents labels from being cut off at edges
- **Standard QGIS options** - DPI, antialiasing, tile format (PNG/JPG), background color
- **JPEG quality control** (1-100%, default 75)
- **Non-blocking generation** - progress dialog with time estimates, UI stays responsive
- **Pre-flight estimates** - see tile count and time estimate before starting
- **Memory safety** - metatile size capped to prevent crashes
- **Keyboard shortcuts** for quick access
- **Batch database commits** for better performance

You can find more info on how it works in the shaped_mbtiles.md file.

## Installation

### Option 1: Install as Plugin

1. **Find your QGIS plugins folder:**
   - Windows: `C:\Users\<YourUsername>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

   If you can't find it, open QGIS, go to **Plugins** -> **Python Console** and run:
   ```python
   from qgis.utils import plugin_dirs
   print(plugin_dirs[0])
   ```

2. **Create a new folder** called `shaped_mbtiles` inside the plugins folder.

3. **Download the following 4 files from this repository and place them into the shaped_mbtiles folder that you just created:**
   - `__init__.py`
   - metadata.txt
   - shaped_mbtiles.py
   - shaped_mbtiles_plugin.py 

3. **Enable the plugin**: Restart QGIS, then go to **Plugins** -> **Manage and Install Plugins** → **Installed** tab. Find "Shaped MBTiles Generator" and check the box to enable it.


### Option 2: Direct Script Loading (No Plugin Setup)

If you don't want to set up a plugin, just load the script directly:

1. Open QGIS
2. Go to **Plugins** → **Python Console**
3. Show editor
4. Paste the contents of the shaped_mbtiles_direct.py and click run

A toolbar button will appear. Click it to start drawing polygons.

## Usage

1. Load your map layers in QGIS
2. Click the toolbar button **"Draw Shaped MBTiles"** (or press **Ctrl+Shift+M**)
3. **Draw your polygon:**
   - **Left-click** to add polygon vertices
   - **Delete/Backspace** or **Shift+Right-click** or **Middle-click** to undo the last point
   - **Right-click** to finish and open export dialog
   - **ESC** to cancel and exit drawing mode
4. **Pause if needed:**
   - Click **"Pause Drawing"** button (or press **Ctrl+Shift+P**) to pause
   - Pan/zoom the map as needed
   - Click **"Resume Drawing"** (same button) to continue
5. **Configure export:**
   - Set zoom levels (min/max) - tile count estimate updates automatically
   - Set DPI (48-384, default: 96)
   - Optionally select a background color
   - Enable/disable antialiasing
   - Choose tile format (PNG or JPG)
   - Adjust JPEG quality if using JPG (1-100%, default: 75)
   - Set metatile size (1-16, default: 4) - memory warning shown if too high
   - Select output file location
6. Click OK to start generation
   - Progress dialog shows current tile and time remaining
   - Click Cancel to stop generation
   - UI remains responsive during generation

## Tips

- Layers should be in Web Mercator (EPSG:3857) for best results
- Start with a small zoom range to test (e.g., 10-12)
- Use **PNG** for transparent backgrounds, **JPG** for smaller file sizes
- Use **Pause Drawing** (Ctrl+Shift+P) if you need to navigate the map while drawing complex polygons
- Press **ESC** to quickly exit drawing mode
- **Delete/Backspace** keys are the easiest way to undo the last point
- Lower JPEG quality (30-50) for smaller files, higher (75-95) for better quality
- Higher DPI (e.g., 192) creates sharper tiles but increases file size
- Metatile size of 4 is good for preventing label clipping; increase if labels still get cut off
- Disable antialiasing for faster generation if visual quality isn't critical
- Check the **tile count estimate** before starting - large numbers mean long wait times

## Troubleshooting

**Plugin doesn't show up:** Check files are in the right folder, restart QGIS, check Python console for errors.

**Blank tiles:** Make sure layers are visible and styled, check polygon is in the right place.

**Slow generation:** Normal for large zoom ranges. Generate in smaller chunks if needed.


## Requirements

- QGIS 3.0+
- Python 3.x (included with QGIS)
- PyQt5 (included with QGIS)




