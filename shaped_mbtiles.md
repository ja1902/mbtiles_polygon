# shaped_mbtiles_clipping.py

QGIS plugin that generates MBTiles from a polygon you draw. Only creates tiles that actually intersect your shape, so no wasted tiles. Uses clipping approach for efficient rendering.

## Features

- Draw a polygon, get tiles for just that area
- Pre-calculates which tiles intersect before rendering
- Renders tiles at 384x384 then crops to 256x256 to prevent label clipping
- Skips expensive masking for tiles fully inside the polygon
- Three mask styles: transparent, white, or black outside the polygon
- Shows tile count before you start
- Progress bar you can cancel

## How It Works

### Tile Calculation

The script figures out which tiles actually touch your polygon:

1. Transform polygon to Web Mercator (EPSG:3857)
2. For each zoom level, get the tile range that covers the polygon's bounding box
3. For each tile in that range, check if it intersects the polygon using `tile_geom.intersects(polygon)`
4. Only add intersecting tiles to the list

Returns a list of `(z, x, y)` tuples.

### Meta-Tiling

Renders each tile at 384x384 pixels with a 64px buffer on all sides, then crops the center 256x256. This keeps labels from getting cut off at tile edges. The geographic extent is expanded by 25% to match.

### Clipping

For tiles that cross the polygon boundary:

1. Check if tile is fully inside polygon - if so, skip clipping
2. Intersect polygon with tile extent to get clipped geometry
3. Convert to QPainterPath in pixel coordinates
4. Set clip path on painter BEFORE rendering
5. Render map - only pixels inside the clip path are drawn
6. Background color (transparent/white/black) shows outside the polygon

### MBTiles Output

Writes directly to SQLite using the MBTiles spec:
- Standard tiles and metadata tables
- Converts XYZ Y coordinates to TMS (Y=0 at bottom)
- Writes metadata: name, format, zoom levels, bounds

Image encoding: PNG for transparent masks, JPEG (85% quality) for solid colors.

## Code Structure

- Tile math utilities: coordinate conversions, tile-to-geometry
- `MBTilesWriter`: handles SQLite database creation and tile writing
- `ShapedTileRenderer`: renders individual tiles with clipping
- `ShapedTileConfigDialog`: settings UI with tile count estimate
- `ShapedMBTilesTool`: drawing tool and generation orchestration

The renderer uses `painter.setClipPath()` to restrict rendering to only the polygon area, which is more efficient than rendering everything and then masking.


## Requirements

- QGIS 3.0+
- Python 3.x (comes with QGIS)
- PyQt5 (included with QGIS)
