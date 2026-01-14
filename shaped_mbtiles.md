# Shaped MBTiles Generator - Technical Documentation

Technical design and implementation approach for the shaped MBTiles generator. For user documentation, see [README.md](README.md).

## Overview

This tool generates MBTiles from arbitrary polygon shapes using spatial filtering and clipping. The approach focuses on efficiency: only tiles that spatially intersect the polygon are generated, and rendering is restricted to the polygon area using Qt's painter clipping system.

## Design Philosophy

**Efficiency over simplicity:** The tool pre-filters tiles by spatial intersection before rendering, avoiding wasted computation on tiles that will be empty or fully masked.

**Standard compatibility:** Follows the MBTiles specification and uses the same configuration options as QGIS's built-in raster tile generator for consistency.

**Progressive enhancement:** Supports basic use cases (simple polygon, default settings) while offering advanced options (custom DPI, metatiling, background colors) for power users.

## Core Concepts

### 1. Spatial Tile Filtering

The tool uses a two-stage approach to determine which tiles to generate:

**Stage 1: Bounding Box Culling**
The polygon's bounding box is transformed to Web Mercator and used to calculate the minimum and maximum tile coordinates at each zoom level. This eliminates the vast majority of the tile grid from consideration.

**Stage 2: Geometric Intersection Testing**
For each tile within the bounding box, a spatial intersection test determines if the tile's geographic extent actually overlaps the polygon. Only tiles that pass this test are added to the generation queue.

**Why this approach:**
- Pre-filtering avoids rendering and encoding tiles that would be entirely outside the polygon
- For irregular polygons, this can reduce tile count by 50-90% compared to generating the entire bounding box
- The cost of intersection testing is negligible compared to rendering time

**Trade-off:**
For very large zoom ranges (e.g., 10-22 on a large area), the intersection testing phase itself can take time. The tool calculates all tiles upfront to provide accurate progress tracking, rather than discovering tiles during generation.

### 2. Meta-Tiling Strategy

**The Problem:**
When maps are divided into 256×256 pixel tiles, labels and symbols near tile edges often get clipped. A label that starts in one tile but extends into the next appears truncated, creating visible seams when tiles are displayed together.

**The Solution:**
Meta-tiling renders each tile at a larger size (e.g., 1024×1024 for metatile_size=4), then crops the center 256×256 pixels. The geographic extent is expanded proportionally to match the larger pixel count.

**Why this works:**
- Labels near the edges have space to render completely within the larger canvas
- When the center is cropped, these labels appear intact (though they may extend to the edge)
- Adjacent tiles render the same labels with their buffers, ensuring continuity

**Trade-offs:**
- **Higher metatile values:** More buffer space -> better label continuity -> longer render time
- **Metatile size 1:** No buffer, fastest rendering, visible label clipping
- **Metatile size 4 (default):** Good balance for most use cases
- **Metatile size 8+:** Minimal clipping, suitable for maps with large labels/symbols, 4-8× slower

The metatile size is user-configurable (1-20) to accommodate different map styles and performance requirements.

### 3. Painter-Based Clipping

**The Approach:**
We use Qt's QPainter clipping system to restrict rendering to only the polygon area before any map layers are drawn.

**How it works:**
The polygon geometry is transformed to pixel coordinates and converted to a QPainterPath (Qt's vector graphics primitive). This path is set as the painter's clip region before QGIS renders the map layers. The QGIS renderer only draws pixels that fall within the clip region.

**Key optimization:**
Tiles that are fully contained within the polygon skip clipping entirely. They're rendered normally without the overhead of clip path calculation and application. This is determined by a simple geometric containment test.

**Why this is efficient:**

1. **Pre-render clipping vs post-render masking:**
   - Pre-render: Only processes pixels inside the polygon
   - Post-render: Renders everything, then discards pixels outside polygon
   - Savings: ~50% for tiles that are half-covered by the polygon

2. **No intermediate layers:**
   - Alternative approach: Render tile, render mask, composite
   - This approach: Single render pass with built-in clipping
   - Avoids memory allocation for mask layers


**Background handling:**
- PNG + no custom color = transparent background (Format_ARGB32)
- JPG or custom color = solid background (Format_RGB32)
- Background is filled before clipping, so areas outside polygon show the background

### 4. MBTiles Format and Storage

**Format choice:**
The tool outputs to MBTiles, a SQLite-based format for storing tile pyramids. This format is:
- **Widely supported**: Works with Mapbox, Leaflet, QGIS, MapTiler, and most web mapping libraries
- **Self-contained**: Single file contains all tiles and metadata
- **Efficient**: SQLite's B-tree structure provides fast random access
- **Portable**: Works across platforms without conversion

**Coordinate system conversion:**
MBTiles uses TMS (Tile Map Service) coordinates with origin at bottom-left, while most tile calculations use XYZ (origin at top-left). The Y coordinate must be flipped when writing tiles.

**Metadata strategy:**
The tool writes standard MBTiles metadata including bounds (in WGS84) and a center point with suggested zoom. The center point helps map viewers automatically position and zoom to the tiled area.

**Image format selection:**
- **PNG**: Lossless, supports transparency, larger files (~15-30 KB/tile typical)
- **JPEG**: Lossy compression, smaller files (~5-15 KB/tile), no transparency
- Quality slider trades file size for visual fidelity (lower = smaller files)

**Write performance:**
SQLite transactions are expensive. Committing after every tile would dominate export time for large tilesets. Batching commits (every 100 tiles) reduces transaction overhead with minimal risk.

## Performance Strategies

### Batch Database Writes
**Problem:** SQLite transactions have significant overhead. Committing after each tile write makes database I/O the bottleneck.
**Solution:** Batch commits every 100 tiles.
**Impact:** 40-60% reduction in write time for large exports.
**Trade-off:** Minimal as losing 100 tiles on crash is acceptable for a batch export tool.

### Geometric Containment Optimization
**Problem:** Converting geometries to painter paths and setting clip regions is expensive.
**Solution:** Test if tile is fully inside polygon; skip clipping for contained tiles.
**Impact:** ~30-40% of tiles in typical polygons are fully contained and skip clipping.
**Cost:** Negligible since the containment test is a simple bounding box check followed by point-in-polygon.

### Spatial Pre-filtering
**Problem:** Rendering and discarding tiles outside the polygon wastes computation.
**Solution:** Calculate intersecting tiles upfront using spatial queries.
**Impact:** For irregular polygons, 50-90% reduction in tiles compared to bounding box.
**Trade-off:** Upfront calculation time, but negligible compared to rendering.

### Configurable Quality/Speed Trade-offs
Users can adjust multiple parameters to trade quality for speed:

- **DPI (48-384):** Lower DPI = faster rendering, coarser output. Rendering time roughly proportional to pixel count (DPI^2).
- **Antialiasing:** Disabling saves ~20-30% render time but produces jaggy edges.
- **Metatile size (1-20):** Smaller values render faster; size 1 is 16× faster than size 4 but labels clip.
- **JPEG quality (1-100):** Lower quality = smaller files, faster encoding. Minimal render impact, affects file size and encoding time.

### Progressive Feedback
The tool calculates all tiles before rendering (rather than discovering them during generation) to provide:
- Accurate progress bars (N of M tiles)
- Time remaining estimates (based on average time per tile)
- Ability to cancel mid-generation without wasted work

**Trade-off:** For very large zoom ranges, the initial tile calculation can take time. This is acceptable because users need the tile count to decide whether to proceed.

## Architecture and Design Decisions

### Component Separation

The tool is structured around **separation of concerns**:

1. **Geometric calculations** (tile math) are pure functions with no UI dependencies
2. **MBTiles writing** is isolated from rendering. It simply receives image bytes and tile coordinates
3. **Tile rendering** is independent of the UI. It takes parameters and returns images
4. **UI components** (dialog, drawing tool) orchestrate the pipeline but don't handle rendering or writing

**Why this matters:**
- Each component can be tested independently
- Rendering strategy can be changed without touching UI code
- Alternative output formats (not just MBTiles) could be added by swapping the writer

### Drawing Tool State Management

**Challenge:** Users need to pause drawing to navigate the map, then resume without losing their polygon.

**Solution:** Global state dictionary that persists between tool activations. When pausing, the tool deactivates but leaves state intact. When resuming, a new tool instance reads the saved state.

**Alternative considered:** Keep single tool instance active. Rejected because it interferes with QGIS's tool model (tool stays "active" even when user switches to pan/zoom).

### Configuration Philosophy

The configuration dialog mirrors QGIS's standard raster MBTiles generator to maintain consistency with the existing QGIS UI. Users familiar with the built-in tool will immediately understand the options.

**Design principle:** "Defaults for the 80% use case, options for the 20%"
- Default settings (DPI 96, metatile 4, JPEG 75, antialiasing on) work well for most maps
- Advanced users can adjust for specific needs (high-DPI exports, large labels, low-bandwidth tiles)

### Progress Tracking Strategy

**Two-phase generation:**
1. Calculate all tiles upfront
2. Render tiles sequentially with progress

**Why not calculate on-the-fly?**
- Need total count for accurate progress (1000/10000 vs "working...")
- Users can see tile count before committing hours to generation
- Allows time estimation based on average tile render time

**Trade-off:** Initial calculation delay for large zoom ranges, but users appreciate knowing what they're getting into.

### Error Handling Philosophy

**Fail gracefully, preserve work:**
- Cancelled exports still produce a valid MBTiles file with partial data
- Failed tile renders skip that tile rather than aborting entire export
- Tool deactivation cleans up rubber bands to avoid visual artifacts

**User feedback:**
- Toast messages for common issues (no output file, min > max)
- Progress dialog shows which tile is rendering
- Time estimates help users decide whether to wait or cancel


## Technical Considerations

### Coordinate System Juggling

The tool works with three different coordinate reference systems:

1. **User's polygon:** Can be in any CRS (project CRS)
2. **Tile calculations:** Web Mercator (EPSG:3857) - required because tile systems are defined in meters
3. **MBTiles metadata:** WGS84 (EPSG:4326) - required by MBTiles specification

Transformations happen automatically, but users should be aware that Web Mercator distorts areas near the poles. This tool is not suitable for polar regions.

### Tile Coordinate Origins

Two standards exist for tile Y coordinates:

- **XYZ (Google/OSM):** Origin at top-left, Y increases downward
- **TMS (Tile Map Service):** Origin at bottom-left, Y increases upward

The tool calculates in XYZ internally (matching QGIS conventions) but writes to MBTiles in TMS format (required by specification). The Y coordinate flip happens during write.

### Memory Considerations

**Rendering is entirely in-memory**—no temporary files. Each tile allocates:
- Base image: ~256KB for 256×256 RGBA
- Meta-tile: ~1-4MB for 1024×1024 RGBA (metatile_size=4)
- QGIS render buffers: Variable, depends on layer complexity

**Practical impact:**
- Normal use: <100MB additional RAM
- Large metatile (20) + high DPI (300): Can use 500MB+ per tile
- Multiple threads rendering simultaneously would multiply memory use (not currently implemented)

### Performance Characteristics

**What's fast:**
- Tiles fully inside polygon (no clipping)
- Low DPI (48-96)
- Small metatile sizes (1-2)
- Simple vector layers

**What's slow:**
- Tiles crossing polygon boundaries (clipping overhead)
- High DPI (200+)
- Large metatile sizes (10+)
- Complex rendering (aerial imagery, complex symbols)
- Many overlapping vector layers

**Rough benchmarks** (typical hardware, simple vector map):
- 100 tiles: 10-30 seconds
- 1,000 tiles: 2-5 minutes  
- 10,000 tiles: 20-60 minutes
- 100,000 tiles: 3-10 hours

### Known Limitations

1. **Polar regions:** Web Mercator doesn't extend beyond ~85° latitude
2. **Tile count explosion:** High zoom levels generate exponentially more tiles
3. **Complex polygons:** 1000+ vertices slow down intersection testing
4. **No resume:** Cancelled exports must restart from beginning (no checkpoint/resume)
5. **Single-threaded:** Renders one tile at a time (parallelization not implemented)

### Future Enhancement Opportunities

- **Parallel rendering:** Multi-threaded tile generation could scale linearly with CPU cores
- **Zoom-dependent simplification:** Simplify polygon at lower zooms to speed intersection testing
- **R-tree spatial index:** For complex polygons, spatial indexing could accelerate tile filtering
- **Progressive MBTiles:** Write tiles as generated rather than holding full list in memory
- **Overzooming:** Generate high zoom levels by scaling lower zoom tiles (faster but lower quality)
