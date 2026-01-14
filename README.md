# Shaped MBTiles Generator for QGIS

Generate MBTiles from a polygon you draw on the map. Only creates tiles that actually intersect your shape. Uses clipping approach for efficient rendering.

## Features

- Draw any polygon shape, generate tiles for just that area
- **Pause/Resume drawing** - pause to pan/zoom, then resume where you left off
- **Undo last point** - Delete/Backspace key, Shift+Right-click, or middle-click
- **ESC to exit** - Press ESC to cancel and exit drawing mode
- Pre-filters tiles before rendering - only intersecting tiles are created
- Uses clipping to render only pixels inside the polygon (more efficient)
- **Configurable metatile size** - prevents labels from being cut off at edges
- **Standard QGIS options** - DPI, antialiasing, tile format (PNG/JPG), background color
- **JPEG quality control** (1-100%, default 75)
- **Time remaining estimate** during generation
- **Keyboard shortcuts** for quick access
- **Batch database commits** for better performance

You can find more info on how it works in the shaped_mbtiles.md file.

## Installation

### Option 1: Install as Plugin

1. **Find your QGIS plugins folder:**
   - Windows: `C:\Users\<YourUsername>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

   If you can't find it, open QGIS, go to **Plugins** → **Python Console** and run:
   ```python
   from qgis.utils import plugin_dirs
   print(plugin_dirs[0])
   ```

2. **Create a new folder** called `shaped_mbtiles` inside the plugins folder.

3. **Create these 3 files** inside the `shaped_mbtiles` folder:

**metadata.txt:**
```ini
[general]
name=Shaped MBTiles Generator
qgisMinimumVersion=3.0
description=Generate MBTiles from polygon shapes
version=1.0
```

**__init__.py:**
```python
import os

def classFactory(iface):
    from .shaped_mbtiles_plugin import ShapedMBTilesPlugin
    return ShapedMBTilesPlugin(iface)
```

**shaped_mbtiles_plugin.py:**
```python
import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.utils import iface
from .shaped_mbtiles import activate_shaped_tool

class ShapedMBTilesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        
        self.action = QAction(icon, "Draw Shaped MBTiles", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Shaped MBTiles", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Shaped MBTiles", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        activate_shaped_tool()
```

4. **Copy the main script**: Copy `mbtiles/shaped_mbtiles.py` from this repository into your `shaped_mbtiles` folder.

5. **Edit the script**: Open `shaped_mbtiles.py` in a text editor. Scroll to the very bottom of the file (around line 489-511). You'll see code that looks like this:

```python
# ======================================================
# 6. LAUNCH
# ======================================================
def activate_shaped_tool():
    tool = ShapedMBTilesTool(iface.mapCanvas())
    iface.mapCanvas().setMapTool(tool)

action_name = "Draw Shaped MBTiles (Clipping)"
main_window = iface.mainWindow()
... (more code that adds toolbar button)
```

Delete everything from line 496 onwards (all the code that creates the toolbar button). Keep only the `activate_shaped_tool()` function. The bottom of your file should end with just:

```python
# ======================================================
# 6. LAUNCH
# ======================================================
def activate_shaped_tool():
    tool = ShapedMBTilesTool(iface.mapCanvas())
    iface.mapCanvas().setMapTool(tool)
```

Why? The plugin wrapper (`shaped_mbtiles_plugin.py`) already handles adding the toolbar button, so you don't need that code in the main script.

6. **Enable the plugin**: Restart QGIS, then go to **Plugins** → **Manage and Install Plugins** → **Installed** tab. Find "Shaped MBTiles Generator" and check the box to enable it.

### Option 2: Direct Script Loading (No Plugin Setup)

If you don't want to set up a plugin, just load the script directly:

1. Open QGIS
2. Go to **Plugins** → **Python Console**
3. Show editor
4. Paste the contents of the python file and click run


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
   - Set zoom levels (min/max)
   - Set DPI (48-384, default: 96)
   - Optionally select a background color
   - Enable/disable antialiasing
   - Choose tile format (PNG or JPG)
   - Adjust JPEG quality if using JPG (1-100%, default: 75)
   - Set metatile size (1-20, default: 4)
   - Select output file location
6. Click OK and wait for generation to complete
   - Progress bar shows time remaining estimate
   - You can cancel at any time

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

## Troubleshooting

**Plugin doesn't show up:** Check files are in the right folder, restart QGIS, check Python console for errors.

**Blank tiles:** Make sure layers are visible and styled, check polygon is in the right place.

**Slow generation:** Normal for large zoom ranges. Generate in smaller chunks if needed.


## Requirements

- QGIS 3.0+
- Python 3.x (included with QGIS)
- PyQt5 (included with QGIS)

