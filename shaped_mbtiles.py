"""
Shaped MBTiles Generator for QGIS

Interactive tool for generating MBTiles from user-drawn polygons. Uses spatial
filtering (only renders intersecting tiles) and painter-based clipping for efficient
tile generation.

Key features:
- Draw arbitrary polygon shapes on the map
- Pre-filter tiles by spatial intersection before rendering
- Render tiles with polygon clipping using QPainterPath
- Meta-tiling to prevent label clipping at tile edges
- Pause/resume drawing functionality
- Standard QGIS MBTiles options (DPI, format, quality, etc.)
- Incremental tile generation using QTimer (non-blocking UI with progress dialog)
- Pre-flight tile count estimates
- Memory safety limits for metatile size
- Batch database commits for performance

Architecture:
1. Tile math utilities - coordinate conversions and spatial filtering
2. MBTilesWriter - SQLite database with MBTiles schema
3. ShapedTileRenderer - renders tiles with polygon clipping
4. IncrementalTileGenerator - QTimer-based non-blocking generation with progress
5. ShapedTileConfigDialog - configuration UI with estimates
6. ShapedMBTilesTool - interactive drawing tool
7. Launch code - toolbar buttons and keyboard shortcuts

See shaped_mbtiles.md for detailed technical documentation.
See README.md for user documentation.
"""

import os
import math
import sqlite3
import time
from io import BytesIO
from qgis.core import (QgsProject, QgsVectorLayer, QgsGeometry, 
                       QgsFeature, QgsMapSettings, QgsMapRendererCustomPainterJob,
                       QgsWkbTypes, QgsCoordinateTransform, QgsPointXY,
                       QgsCoordinateReferenceSystem, QgsRectangle)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.utils import iface
from PyQt5.QtCore import Qt, QSize, QBuffer, QIODevice, QByteArray, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QKeyEvent
from PyQt5.QtWidgets import (QAction, QDialog, QSpinBox, QPushButton, 
                             QFileDialog, QFormLayout, QDialogButtonBox, 
                             QLabel, QProgressDialog, QComboBox, QMessageBox,
                             QApplication, QCheckBox, QColorDialog, QGroupBox,
                             QVBoxLayout, QHBoxLayout)

# ======================================================
# CONSTANTS
# ======================================================
TILE_SIZE = 256  # Standard web map tile size (TMS specification)
ORIGIN_SHIFT = 20037508.342789244  # Web Mercator (EPSG:3857) extent in meters
WORLD_CIRCUMFERENCE = 40075016.686  # Earth's circumference at equator in meters

# Memory safety limits
MAX_RENDER_PIXELS = 4096  # Maximum render canvas size in pixels (prevents OOM)
MAX_METATILE_SIZE = 16    # Capped metatile size (16 * 256 = 4096 pixels)
MEMORY_WARNING_MB = 200   # Warn if estimated memory usage exceeds this

# Global state to persist drawing between pause/resume cycles
# This dict allows the drawing tool to be deactivated (for pan/zoom) while
# preserving the polygon points and rubber band. When resuming, a new tool
# instance reads this state to continue where the user left off.
_drawing_state = {
    'points': [],              # List of QgsPointXY vertices
    'paused': False,           # True when drawing is paused
    'tool': None,              # Reference to active ShapedMBTilesTool instance
    'rubber_band': None,       # Reference to QgsRubberBand for visual feedback
    'intentional_pause': False # Flag to distinguish pause from accidental tool switch
}

# ======================================================
# 1. TILE MATH UTILITIES
# ======================================================
def lon_lat_to_meters(lon, lat):
    """
    Convert WGS84 longitude/latitude to Web Mercator meters.
    
    Args:
        lon: Longitude in degrees (-180 to 180)
        lat: Latitude in degrees (-85.05 to 85.05 for Web Mercator)
    
    Returns:
        Tuple of (mx, my) in meters from origin
    """
    mx = lon * ORIGIN_SHIFT / 180.0
    my = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    my = my * ORIGIN_SHIFT / 180.0
    return mx, my

def meters_to_tile(mx, my, zoom):
    """
    Convert Web Mercator meters to XYZ tile coordinates at given zoom level.
    
    Args:
        mx, my: Coordinates in Web Mercator meters
        zoom: Zoom level (0-22)
    
    Returns:
        Tuple of (tile_x, tile_y) in XYZ coordinates
    """
    resolution = WORLD_CIRCUMFERENCE / (TILE_SIZE * (2 ** zoom))
    px = (mx + ORIGIN_SHIFT) / resolution
    py = (ORIGIN_SHIFT - my) / resolution
    tx = int(px / TILE_SIZE)
    ty = int(py / TILE_SIZE)
    return tx, ty

def tile_to_extent(z, x, y):
    """
    Calculate the geographic extent of a tile in Web Mercator meters.
    
    Args:
        z, x, y: XYZ tile coordinates
    
    Returns:
        QgsRectangle representing the tile's extent
    """
    tile_size_meters = WORLD_CIRCUMFERENCE / (2 ** z)
    x_min = x * tile_size_meters - ORIGIN_SHIFT
    x_max = (x + 1) * tile_size_meters - ORIGIN_SHIFT
    y_max = ORIGIN_SHIFT - y * tile_size_meters
    y_min = ORIGIN_SHIFT - (y + 1) * tile_size_meters
    return QgsRectangle(x_min, y_min, x_max, y_max)

def tile_to_geometry(z, x, y):
    """
    Convert tile coordinates to a QgsGeometry polygon in Web Mercator.
    
    Args:
        z, x, y: XYZ tile coordinates
    
    Returns:
        QgsGeometry representing the tile as a rectangle
    """
    extent = tile_to_extent(z, x, y)
    return QgsGeometry.fromRect(extent)

def get_intersecting_tiles(polygon_geom, source_crs, zoom_min, zoom_max):
    """
    Calculate all tiles that spatially intersect a polygon across zoom levels.
    
    This is a two-stage process:
    1. Transform polygon to Web Mercator
    2. For each zoom level:
       - Calculate tile range from bounding box
       - Test each candidate tile for intersection
       - Keep only tiles that actually intersect
    
    Args:
        polygon_geom: QgsGeometry polygon in source_crs
        source_crs: QgsCoordinateReferenceSystem of the polygon
        zoom_min, zoom_max: Zoom level range (inclusive)
    
    Returns:
        Tuple of:
        - List of (z, x, y) tuples for intersecting tiles
        - Transformed polygon in Web Mercator (EPSG:3857)
    """
    web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(source_crs, web_mercator, QgsProject.instance())
    
    # Transform polygon to Web Mercator for tile calculations
    poly_3857 = QgsGeometry(polygon_geom)
    poly_3857.transform(transform)
    
    tiles = []
    bbox = poly_3857.boundingBox()
    
    # For each zoom level, find tiles in bounding box and test for intersection
    for z in range(zoom_min, zoom_max + 1):
        x_min, y_min = meters_to_tile(bbox.xMinimum(), bbox.yMaximum(), z)
        x_max, y_max = meters_to_tile(bbox.xMaximum(), bbox.yMinimum(), z)
        
        # Clamp to valid tile range for this zoom level
        max_tile = (2 ** z) - 1
        x_min = max(0, x_min)
        x_max = min(max_tile, x_max)
        y_min = max(0, y_min)
        y_max = min(max_tile, y_max)
        
        # Test each candidate tile for actual intersection with polygon
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tile_geom = tile_to_geometry(z, x, y)
                if tile_geom.intersects(poly_3857):
                    tiles.append((z, x, y))
    
    return tiles, poly_3857

def estimate_tile_count_fast(polygon_geom, source_crs, zoom_min, zoom_max):
    """
    Quick estimate of tile count using bounding box only (no intersection test).
    
    This is much faster than get_intersecting_tiles() and useful for UI feedback.
    The actual count will be lower for irregular polygons.
    
    Args:
        polygon_geom: QgsGeometry polygon
        source_crs: Source CRS of polygon
        zoom_min, zoom_max: Zoom level range
    
    Returns:
        Tuple of (estimated_count, is_exact). is_exact=False for estimates.
    """
    web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(source_crs, web_mercator, QgsProject.instance())
    
    poly_3857 = QgsGeometry(polygon_geom)
    poly_3857.transform(transform)
    bbox = poly_3857.boundingBox()
    
    total = 0
    for z in range(zoom_min, zoom_max + 1):
        x_min, y_min = meters_to_tile(bbox.xMinimum(), bbox.yMaximum(), z)
        x_max, y_max = meters_to_tile(bbox.xMaximum(), bbox.yMinimum(), z)
        
        max_tile = (2 ** z) - 1
        x_min = max(0, x_min)
        x_max = min(max_tile, x_max)
        y_min = max(0, y_min)
        y_max = min(max_tile, y_max)
        
        cols = x_max - x_min + 1
        rows = y_max - y_min + 1
        total += cols * rows
    
    return total, False  # is_exact=False because this is bbox estimate

def estimate_memory_usage(metatile_size, dpi):
    """
    Estimate memory usage per tile render in MB.
    
    Args:
        metatile_size: Metatile multiplier
        dpi: DPI setting
    
    Returns:
        Estimated MB per tile
    """
    # Render size in pixels (capped at MAX_RENDER_PIXELS)
    render_size = min(TILE_SIZE * metatile_size, MAX_RENDER_PIXELS)
    # 4 bytes per pixel (RGBA), plus some overhead
    bytes_per_tile = render_size * render_size * 4 * 1.5  # 1.5x for overhead
    return bytes_per_tile / (1024 * 1024)

# ======================================================
# 2. MBTILES DATABASE HANDLER
# ======================================================
class MBTilesWriter:
    """
    Writes tiles to an MBTiles SQLite database.
    
    Handles:
    - Schema initialization (tiles and metadata tables)
    - Metadata writing (name, bounds, format, zoom levels, etc.)
    - Tile writing with XYZ to TMS coordinate conversion
    - Batch commits for performance
    
    MBTiles spec: https://github.com/mapbox/mbtiles-spec
    """
    
    def __init__(self, path, name="Shaped Export", description="Generated by QGIS", 
                 tile_format="png", bounds=None, min_zoom=0, max_zoom=14):
        """
        Initialize MBTiles database.
        
        Args:
            path: Output file path (.mbtiles)
            name: Tileset name for metadata
            description: Tileset description for metadata
            tile_format: "png" or "jpg"
            bounds: WGS84 bounds tuple (lon_min, lat_min, lon_max, lat_max)
            min_zoom, max_zoom: Zoom level range
        """
        self.path = path
        self.conn = sqlite3.connect(path)
        self.cursor = self.conn.cursor()
        self._init_schema()
        self._write_metadata(name, description, tile_format, bounds, min_zoom, max_zoom)
    
    def _init_schema(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tiles (
                zoom_level INTEGER,
                tile_column INTEGER,
                tile_row INTEGER,
                tile_data BLOB,
                PRIMARY KEY (zoom_level, tile_column, tile_row)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                name TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.commit()
    
    def _write_metadata(self, name, description, tile_format, bounds, min_zoom, max_zoom):
        metadata = {
            'name': name,
            'type': 'baselayer',
            'version': '1.0',
            'description': description,
            'format': tile_format,
            'minzoom': str(min_zoom),
            'maxzoom': str(max_zoom)
        }
        if bounds:
            metadata['bounds'] = f"{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}"
            # Add center point (lon, lat, zoom)
            center_lon = (bounds[0] + bounds[2]) / 2
            center_lat = (bounds[1] + bounds[3]) / 2
            center_zoom = (min_zoom + max_zoom) // 2
            metadata['center'] = f"{center_lon},{center_lat},{center_zoom}"
        
        for key, value in metadata.items():
            self.cursor.execute(
                "INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)",
                (key, value)
            )
        self.conn.commit()
    
    def write_tile(self, z, x, y, image_data):
        """
        Write a single tile to the database.
        
        Converts XYZ coordinates to TMS (flips Y axis) as required by MBTiles spec.
        Uses INSERT OR REPLACE to overwrite existing tiles.
        
        Args:
            z, x, y: XYZ tile coordinates
            image_data: Tile image as bytes (PNG or JPEG)
        """
        # Convert XYZ (origin top-left) to TMS (origin bottom-left)
        tms_y = (2 ** z) - 1 - y
        self.cursor.execute(
            "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
            (z, x, tms_y, image_data)
        )
    
    def commit(self):
        self.conn.commit()
    
    def close(self):
        """Close the database connection."""
        self.conn.commit()
        self.conn.close()

# ======================================================
# 3. TILE RENDERER
# ======================================================
class ShapedTileRenderer:
    """
    Renders map tiles with polygon clipping using QPainter paths.
    
    Rendering approach:
    1. Create image buffer with background color
    2. Set QPainter clip path to polygon (if needed)
    3. Render map layers - only pixels inside clip path are drawn
    4. Crop to final tile size (if using metatiling)
    
    Optimizations:
    - Skips clipping for tiles fully inside polygon (containment test)
    - Uses meta-tiling to prevent label clipping at tile edges
    - Configurable DPI and antialiasing for quality/speed trade-offs
    - Reuses QgsMapSettings across all tiles (only extent/size change per tile)
    """
    
    def __init__(self, polygon_geom_3857, layers, tile_format='png', background_color=None, 
                 dpi=96, antialias=True, metatile_size=4):
        """
        Initialize renderer.
        
        Args:
            polygon_geom_3857: QgsGeometry polygon in Web Mercator (EPSG:3857)
            layers: List of QgsMapLayers to render (set once for all tiles)
            tile_format: "png" or "jpg"
            background_color: QColor for background (None = transparent for PNG, white for JPG)
            dpi: Dots per inch for rendering (48-384)
            antialias: Enable antialiasing (slower but smoother)
            metatile_size: Render multiplier for edge buffering (1-20, default 4)
        """
        self.polygon = polygon_geom_3857
        self.tile_format = tile_format
        self.background_color = background_color
        self.dpi = dpi
        self.antialias = antialias
        self.metatile_size = metatile_size
        self.web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
        
        # Pre-configure map settings (reused across all tiles)
        # Only extent and output size change per tile
        self.map_settings = QgsMapSettings()
        self.map_settings.setDestinationCrs(self.web_mercator)
        self.map_settings.setOutputDpi(dpi)
        self.map_settings.setLayers(layers)
        self.map_settings.setFlag(QgsMapSettings.Antialiasing, antialias)
        self.map_settings.setFlag(QgsMapSettings.UseAdvancedEffects, antialias)
        
    def render_tile(self, z, x, y):
        """
        Render a single tile with polygon clipping.
        
        Process:
        1. Calculate tile extent and check if fully inside polygon
        2. Calculate render size with metatile buffer
        3. Create image buffer with background
        4. Set clip path if needed (tiles crossing polygon boundary)
        5. Render map with QgsMapRendererCustomPainterJob
        6. Crop to final 256×256 tile size
        
        Args:
            z, x, y: XYZ tile coordinates
        
        Returns:
            QImage of size 256×256 pixels
        """
        extent = tile_to_extent(z, x, y)
        tile_geom = QgsGeometry.fromRect(extent)
        
        # Optimization: Skip clipping overhead for tiles fully inside polygon
        tile_fully_inside = self.polygon.contains(tile_geom)
        
        # Calculate render size based on metatile size
        render_size = TILE_SIZE * self.metatile_size
        
        # Calculate expanded extent for meta-tiling
        buffer_ratio = (render_size - TILE_SIZE) / (2 * TILE_SIZE)
        expand_x = extent.width() * buffer_ratio
        expand_y = extent.height() * buffer_ratio
        render_extent = QgsRectangle(
            extent.xMinimum() - expand_x,
            extent.yMinimum() - expand_y,
            extent.xMaximum() + expand_x,
            extent.yMaximum() + expand_y
        )
        
        # Update only the per-tile settings (extent and size)
        self.map_settings.setOutputSize(QSize(render_size, render_size))
        self.map_settings.setExtent(render_extent)
        
        # Determine background color
        if self.background_color:
            bg_color = self.background_color
        else:
            # Default to transparent for PNG, white for JPG
            bg_color = Qt.transparent if self.tile_format == 'png' else QColor("white")
        
        # Create image with appropriate format
        if self.tile_format == 'png' and not self.background_color:
            render_image = QImage(render_size, render_size, QImage.Format_ARGB32)
            render_image.fill(Qt.transparent)
        else:
            render_image = QImage(render_size, render_size, QImage.Format_RGB32)
            render_image.fill(bg_color)
        
        # Set DPI for the image
        render_image.setDotsPerMeterX(int(self.dpi / 0.0254))
        render_image.setDotsPerMeterY(int(self.dpi / 0.0254))
        
        painter = QPainter(render_image)
        if self.antialias:
            painter.setRenderHint(QPainter.Antialiasing, True)
        
        # If tile is NOT fully inside, set clip path BEFORE rendering
        if not tile_fully_inside:
            clip_path = self._get_clip_path(render_extent, render_size)
            if clip_path:
                painter.setClipPath(clip_path)
        
        # Render map - with clipping, only pixels inside the path are drawn
        job = QgsMapRendererCustomPainterJob(self.map_settings, painter)
        job.start()
        job.waitForFinished()
        
        painter.end()
        
        # Crop to final tile size
        buffer_px = (render_size - TILE_SIZE) // 2
        final_image = render_image.copy(buffer_px, buffer_px, TILE_SIZE, TILE_SIZE)
        
        return final_image
    
    def _get_clip_path(self, extent, render_size):
        """
        Calculate QPainterPath for clipping this tile to the polygon.
        
        Process:
        1. Intersect polygon with render extent
        2. Convert resulting geometry to QPainterPath in pixel coordinates
        
        Args:
            extent: QgsRectangle of render area in Web Mercator meters
            render_size: Size of render buffer in pixels
        
        Returns:
            QPainterPath for clipping, or None if no intersection
        """
        render_geom = QgsGeometry.fromRect(extent)
        clipped = self.polygon.intersection(render_geom)
        
        if clipped.isEmpty():
            # Tile is entirely outside polygon - nothing to render
            return None
        
        return self._geometry_to_path(clipped, extent, render_size)
    
    def _geometry_to_path(self, geom, extent, image_size=TILE_SIZE):
        """
        Convert QgsGeometry polygon to QPainterPath in pixel coordinates.
        
        Handles:
        - Single polygons and multipolygons
        - Outer rings and holes (inner rings)
        - Coordinate transformation from meters to pixels
        
        Args:
            geom: QgsGeometry in Web Mercator meters
            extent: QgsRectangle defining the geographic extent
            image_size: Size of image in pixels
        
        Returns:
            QPainterPath with polygon(s) in pixel coordinates
        """
        path = QPainterPath()
        
        x_scale = image_size / extent.width()
        y_scale = image_size / extent.height()
        
        def to_pixel(point):
            px = (point.x() - extent.xMinimum()) * x_scale
            py = (extent.yMaximum() - point.y()) * y_scale
            return px, py
        
        if geom.type() == QgsWkbTypes.PolygonGeometry:
            if geom.isMultipart():
                polygons = geom.asMultiPolygon()
            else:
                polygons = [geom.asPolygon()]
            
            for polygon in polygons:
                for ring_idx, ring in enumerate(polygon):
                    if len(ring) < 3:
                        continue
                    
                    px, py = to_pixel(ring[0])
                    path.moveTo(px, py)
                    
                    for point in ring[1:]:
                        px, py = to_pixel(point)
                        path.lineTo(px, py)
                    
                    path.closeSubpath()
        
        return path

# ======================================================
# 4. INCREMENTAL TILE GENERATOR
# ======================================================
class IncrementalTileGenerator(QObject):
    """
    Generates tiles incrementally using QTimer to keep UI responsive.
    
    Unlike QgsTask (which runs in a background thread), this runs on the
    main thread but yields control back to Qt's event loop between tiles.
    This is necessary because QPainter and QgsMapRendererCustomPainterJob
    require main thread execution.
    
    Benefits over blocking loop with processEvents():
    - Proper event loop integration (no re-entrancy bugs)
    - Clean cancellation via dialog
    - Progress updates without blocking
    """
    
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, settings, layers, on_complete_callback):
        super().__init__()
        self.settings = settings
        self.layers = layers
        self.on_complete_callback = on_complete_callback
        
        self.tiles = settings['TILES']
        self.current_index = 0
        self.tiles_generated = 0
        self.start_time = None
        self.cancelled = False
        self.finished = False  # Prevents double-finish from dialog close signal
        
        # Initialize components
        self._init_writer()
        self._init_renderer()
        self._init_progress_dialog()
        
        # Timer for incremental processing
        self.timer = QTimer()
        self.timer.timeout.connect(self._process_next_tile)
    
    def _init_writer(self):
        """Initialize MBTiles database writer."""
        polygon = self.settings['POLYGON_3857']
        output_path = self.settings['OUTPUT_FILE']
        tile_format = self.settings['TILE_FORMAT']
        
        # Calculate bounds in WGS84
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
        to_wgs84 = QgsCoordinateTransform(web_mercator, wgs84, QgsProject.instance())
        bbox_wgs84 = to_wgs84.transformBoundingBox(polygon.boundingBox())
        bounds = (bbox_wgs84.xMinimum(), bbox_wgs84.yMinimum(), 
                  bbox_wgs84.xMaximum(), bbox_wgs84.yMaximum())
        
        self.writer = MBTilesWriter(
            output_path,
            name="Shaped Export",
            description="Generated by QGIS",
            tile_format=tile_format,
            bounds=bounds,
            min_zoom=self.settings['ZOOM_MIN'],
            max_zoom=self.settings['ZOOM_MAX']
        )
    
    def _init_renderer(self):
        """Initialize tile renderer with pre-configured map settings."""
        polygon = self.settings['POLYGON_3857']
        metatile_size = min(self.settings.get('METATILE_SIZE', 4), MAX_METATILE_SIZE)
        
        # Pass layers to renderer - they're set once and reused for all tiles
        self.renderer = ShapedTileRenderer(
            polygon,
            self.layers,  # Layers set once for all tiles (optimization)
            tile_format=self.settings['TILE_FORMAT'],
            background_color=self.settings.get('BACKGROUND_COLOR'),
            dpi=self.settings.get('DPI', 96),
            antialias=self.settings.get('ANTIALIAS', True),
            metatile_size=metatile_size
        )
        self.tile_format = self.settings['TILE_FORMAT']
        self.jpeg_quality = self.settings.get('JPEG_QUALITY', 75)
    
    def _init_progress_dialog(self):
        """Initialize progress dialog."""
        self.progress = QProgressDialog(
            "Generating MBTiles...", 
            "Cancel", 
            0, 
            len(self.tiles), 
            iface.mainWindow()
        )
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.setValue(0)
        self.progress.canceled.connect(self._on_cancel)
    
    def start(self):
        """Start tile generation."""
        self.start_time = time.time()
        self.timer.start(0)  # Process as fast as possible, yielding to event loop
    
    def _on_cancel(self):
        """Handle cancel button click."""
        # Only process cancel if we haven't already finished
        # (QProgressDialog emits canceled when closed, even after completion)
        if not self.finished and not self.cancelled:
            self.cancelled = True
            self.timer.stop()
            self._finish(False, "Generation cancelled by user")
    
    def _process_next_tile(self):
        """Process the next tile in the queue."""
        if self.cancelled:
            return
        
        if self.current_index >= len(self.tiles):
            # All tiles processed
            self.timer.stop()
            self._finish(True, f"Generated {self.tiles_generated} tiles")
            return
        
        try:
            z, x, y = self.tiles[self.current_index]
            
            # Render tile (layers already set in renderer)
            image = self.renderer.render_tile(z, x, y)
            
            # Encode image
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if self.tile_format == 'png':
                image.save(buffer, "PNG")
            else:
                image.save(buffer, "JPEG", self.jpeg_quality)
            
            self.writer.write_tile(z, x, y, bytes(buffer.data()))
            
            # Batch commit every 100 tiles
            if (self.current_index + 1) % 100 == 0:
                self.writer.commit()
            
            self.current_index += 1
            self.tiles_generated += 1
            
            # Update progress
            self._update_progress(z)
            
        except Exception as e:
            self.timer.stop()
            self._finish(False, f"Error at tile {self.current_index}: {str(e)}")
    
    def _update_progress(self, current_zoom):
        """Update progress dialog with ETA."""
        elapsed = time.time() - self.start_time
        remaining_tiles = len(self.tiles) - self.current_index
        
        if self.current_index > 0:
            per_tile = elapsed / self.current_index
            remaining_secs = per_tile * remaining_tiles
            
            if remaining_secs < 60:
                eta_str = f" (~{int(remaining_secs)}s left)"
            elif remaining_secs < 3600:
                eta_str = f" (~{int(remaining_secs/60)}m left)"
            else:
                eta_str = f" (~{remaining_secs/3600:.1f}h left)"
        else:
            eta_str = ""
        
        self.progress.setValue(self.current_index)
        self.progress.setLabelText(f"Tile {self.current_index}/{len(self.tiles)} (Z{current_zoom}){eta_str}")
    
    def _finish(self, success, message):
        """Clean up and call completion callback."""
        # Prevent double-finish
        if self.finished:
            return
        self.finished = True
        
        try:
            self.writer.close()
        except:
            pass
        
        self.progress.close()
        self.on_complete_callback(success, message)

# ======================================================
# 5. CONFIGURATION DIALOG  
# ======================================================
class ShapedTileConfigDialog(QDialog):
    """
    Configuration dialog for shaped MBTiles export.
    
    Provides standard QGIS MBTiles options:
    - Zoom level range (min/max)
    - DPI for rendering
    - Optional background color
    - Antialiasing toggle
    - Tile format (PNG/JPG)
    - JPEG quality (when using JPG)
    - Metatile size for edge buffering
    - Output file selection
    - Tile count estimate (pre-flight check)
    - Memory usage warning
    
    Validates inputs (min <= max zoom) before accepting.
    """
    
    def __init__(self, polygon_geometry, parent=None):
        """
        Initialize configuration dialog.
        
        Args:
            polygon_geometry: QgsGeometry of drawn polygon
            parent: Parent widget (typically iface.mainWindow())
        """
        super().__init__(parent)
        self.setWindowTitle("Shaped MBTiles Configuration")
        self.resize(420, 520)
        self.poly = polygon_geometry
        self.web_mercator = QgsCoordinateReferenceSystem("EPSG:3857")
        self.source_crs = QgsProject.instance().crs()
        self.background_color = None  # Optional background color
        self._update_timer = QTimer()
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._do_update_estimate)
        
        layout = QFormLayout(self)
        
        # Zoom levels
        self.min_zoom = QSpinBox()
        self.min_zoom.setRange(0, 22)
        self.min_zoom.setValue(10)
        self.min_zoom.valueChanged.connect(self.schedule_update_estimate)
        layout.addRow("Minimum zoom:", self.min_zoom)
        
        self.max_zoom = QSpinBox()
        self.max_zoom.setRange(0, 22)
        self.max_zoom.setValue(14)
        self.max_zoom.valueChanged.connect(self.schedule_update_estimate)
        layout.addRow("Maximum zoom:", self.max_zoom)
        
        # Tile estimate (pre-flight check)
        self.estimate_label = QLabel("Calculating...")
        self.estimate_label.setStyleSheet("font-weight: bold; padding: 5px; background: #f0f0f0; border-radius: 3px;")
        layout.addRow("Estimated tiles:", self.estimate_label)
        
        # DPI
        self.dpi = QSpinBox()
        self.dpi.setRange(48, 384)
        self.dpi.setValue(96)
        self.dpi.valueChanged.connect(self.update_memory_warning)
        layout.addRow("DPI:", self.dpi)
        
        # Background color (optional)
        self.bg_color_btn = QPushButton("Select Color (Optional)")
        self.bg_color_btn.clicked.connect(self.select_background_color)
        self.bg_color_label = QLabel("No background color")
        layout.addRow("Background color [optional]:", self.bg_color_btn)
        layout.addRow("", self.bg_color_label)
        
        # Enable antialiasing
        self.antialias_check = QCheckBox("Enable antialiasing")
        self.antialias_check.setChecked(True)
        layout.addRow(self.antialias_check)
        
        # Tile format
        self.tile_format = QComboBox()
        self.tile_format.addItems(["PNG", "JPG"])
        self.tile_format.currentIndexChanged.connect(self.update_format_options)
        layout.addRow("Tile format:", self.tile_format)
        
        # Quality (JPG only)
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(75)
        self.jpeg_quality_label = QLabel("Quality (JPG only):")
        layout.addRow(self.jpeg_quality_label, self.jpeg_quality)
        
        # Metatile size (capped for memory safety)
        self.metatile_size = QSpinBox()
        self.metatile_size.setRange(1, MAX_METATILE_SIZE)
        self.metatile_size.setValue(4)
        self.metatile_size.valueChanged.connect(self.update_memory_warning)
        layout.addRow("Metatile size:", self.metatile_size)
        
        # Memory warning label
        self.memory_label = QLabel("")
        self.memory_label.setWordWrap(True)
        layout.addRow("", self.memory_label)
        
        # Output file
        self.file_btn = QPushButton("Select Output File...")
        self.file_btn.clicked.connect(self.select_file)
        self.output_path = ""
        self.file_label = QLabel("No file selected")
        self.file_label.setWordWrap(True)
        layout.addRow("Output:", self.file_btn)
        layout.addRow("", self.file_label)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        
        self.update_format_options()
        self.schedule_update_estimate()
        self.update_memory_warning()
    
    def schedule_update_estimate(self):
        """Debounce tile count updates to prevent freezing while typing."""
        self._update_timer.start(300)  # 300ms delay
    
    def _do_update_estimate(self):
        """Actually calculate and display tile estimate."""
        min_z = self.min_zoom.value()
        max_z = self.max_zoom.value()
        
        if min_z > max_z:
            self.estimate_label.setText("Invalid: min > max")
            self.estimate_label.setStyleSheet("font-weight: bold; padding: 5px; background: #ffcccc; border-radius: 3px;")
            return

        # Quick bbox-based estimate (doesn't freeze)
        count, _ = estimate_tile_count_fast(self.poly, self.source_crs, min_z, max_z)
        
        # Color based on count
        if count < 10000:
            self.estimate_label.setStyleSheet("font-weight: bold; padding: 5px; background: #ccffcc; border-radius: 3px;")
            time_est = f" (~{count * 0.1:.0f}s)"
        elif count < 100000:
            self.estimate_label.setStyleSheet("font-weight: bold; padding: 5px; background: #ffffcc; border-radius: 3px;")
            time_est = f" (~{count * 0.1 / 60:.0f}min)"
        else:
            self.estimate_label.setStyleSheet("font-weight: bold; padding: 5px; background: #ffcccc; border-radius: 3px;")
            time_est = f" (~{count * 0.1 / 3600:.1f}h)"
        
        self.estimate_label.setText(f"~{count:,} tiles (max){time_est}")
    
    def update_memory_warning(self):
        """Update memory usage warning based on metatile size and DPI."""
        mem_mb = estimate_memory_usage(self.metatile_size.value(), self.dpi.value())
        
        if mem_mb > MEMORY_WARNING_MB:
            self.memory_label.setText(f"High memory usage: ~{mem_mb:.0f}MB per tile")
            self.memory_label.setStyleSheet("color: #cc0000;")
        elif mem_mb > 50:
            self.memory_label.setText(f"Memory: ~{mem_mb:.0f}MB per tile")
            self.memory_label.setStyleSheet("color: #666666;")
        else:
            self.memory_label.setText("")
    
    def validate_and_accept(self):
        """Validate inputs before accepting"""
        min_z = self.min_zoom.value()
        max_z = self.max_zoom.value()
        
        if min_z > max_z:
            QMessageBox.warning(
                self, 
                "Invalid Zoom Levels", 
                f"Minimum zoom ({min_z}) cannot be greater than maximum zoom ({max_z}).\n\nPlease adjust the zoom levels."
            )
            return
        
        # If validation passes, accept the dialog
        self.accept()

    def select_file(self):
        f, _ = QFileDialog.getSaveFileName(self, "Save MBTiles", "", "MBTiles (*.mbtiles)")
        if f:
            if not f.endswith('.mbtiles'):
                f += '.mbtiles'
            self.output_path = f
            self.file_label.setText(os.path.basename(f))
    
    def select_background_color(self):
        """Select optional background color"""
        color = QColorDialog.getColor()
        if color.isValid():
            self.background_color = color
            self.bg_color_label.setText(f"RGB({color.red()}, {color.green()}, {color.blue()})")
            self.bg_color_label.setStyleSheet(f"background-color: {color.name()}; padding: 5px;")
        else:
            self.background_color = None
            self.bg_color_label.setText("No background color")
            self.bg_color_label.setStyleSheet("")
    
    def update_format_options(self):
        """Show/hide format-specific options"""
        is_jpg = self.tile_format.currentText() == "JPG"
        self.jpeg_quality.setVisible(is_jpg)
        self.jpeg_quality_label.setVisible(is_jpg)

    def get_settings(self):
        tiles, poly_3857 = get_intersecting_tiles(
            self.poly, self.source_crs, 
            self.min_zoom.value(), self.max_zoom.value()
        )
        return {
            'ZOOM_MIN': self.min_zoom.value(),
            'ZOOM_MAX': self.max_zoom.value(),
            'DPI': self.dpi.value(),
            'BACKGROUND_COLOR': self.background_color,
            'ANTIALIAS': self.antialias_check.isChecked(),
            'TILE_FORMAT': self.tile_format.currentText().lower(),
            'JPEG_QUALITY': self.jpeg_quality.value(),
            'METATILE_SIZE': min(self.metatile_size.value(), MAX_METATILE_SIZE),
            'OUTPUT_FILE': self.output_path,
            'TILES': tiles,
            'POLYGON_3857': poly_3857
        }

# ======================================================
# 6. DRAW TOOL
# ======================================================
class ShapedMBTilesTool(QgsMapTool):
    """
    Interactive map tool for drawing polygons and generating shaped MBTiles.
    
    Features:
    - Left-click to add polygon vertices
    - Middle-click or Delete/Backspace to undo last point
    - Right-click to finish and open export dialog
    - ESC to cancel and exit
    - Pause/resume functionality (preserves state in global _drawing_state)
    - Visual feedback via QgsRubberBand
    
    State management:
    Uses global _drawing_state dict to persist points across pause/resume cycles.
    When pausing, tool deactivates but state remains. When resuming, new instance
    reads saved state.
    """
    
    def __init__(self, canvas, resume_points=None):
        """
        Initialize drawing tool.
        
        Args:
            canvas: QgsMapCanvas to draw on
            resume_points: List of QgsPointXY to resume from (for pause/resume)
        """
        self.canvas = canvas
        QgsMapTool.__init__(self, self.canvas)
        
        # Setup rubber band for visual feedback
        self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubberBand.setColor(QColor(50, 200, 50, 180))
        self.rubberBand.setWidth(3)
        
        # Restore points if resuming
        if resume_points:
            self.points = resume_points
            for pt in self.points:
                self.rubberBand.addPoint(pt)
        else:
            self.points = []
        
        # Update global state
        _drawing_state['tool'] = self
        _drawing_state['rubber_band'] = self.rubberBand
        _drawing_state['paused'] = False

    def canvasPressEvent(self, e):
        if e.button() == Qt.LeftButton:
            point = self.toMapCoordinates(e.pos())
            self.points.append(point)
            self.rubberBand.addPoint(point)
            # Keep global state in sync
            _drawing_state['points'] = self.points
        elif e.button() == Qt.RightButton:
            # Shift+Right-click = undo, regular right-click = finish
            if e.modifiers() & Qt.ShiftModifier:
                self._undo_last_point()
            elif len(self.points) > 2:
                self.finish_drawing()
            else:
                self.reset()
        elif e.button() == Qt.MiddleButton:
            # Undo last point
            self._undo_last_point()
    
    def keyPressEvent(self, e):
        """Handle keyboard shortcuts for undo and exit"""
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._undo_last_point()
        elif e.key() == Qt.Key_Escape:
            # ESC key to cancel drawing and exit
            iface.messageBar().pushMessage(
                "Drawing Cancelled", 
                "Exited drawing mode",
                level=0, duration=2
            )
            self.reset()
        else:
            QgsMapTool.keyPressEvent(self, e)
    
    def _undo_last_point(self):
        """Remove the last point from the polygon"""
        if self.points:
            self.points.pop()
            # Rebuild rubber band
            self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)
            for pt in self.points:
                self.rubberBand.addPoint(pt)
            _drawing_state['points'] = self.points
            iface.messageBar().pushMessage(
                "Point Removed", 
                f"{len(self.points)} points remaining",
                level=0, duration=1
            )

    def reset(self):
        self.points = []
        self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)
        self.canvas.unsetMapTool(self)
        # Clear global state
        _drawing_state['points'] = []
        _drawing_state['paused'] = False
        _drawing_state['tool'] = None
        _drawing_state['rubber_band'] = None
        # Update pause button text
        if _pause_action:
            _pause_action.setText("Pause Drawing")

    def pause(self):
        """
        Pause drawing and release the tool.
        
        Saves current points to global state, sets intentional_pause flag to
        prevent cleanup in deactivate(), and unsets the tool. Rubber band
        remains visible so user can see their work.
        """
        _drawing_state['points'] = self.points.copy()
        _drawing_state['paused'] = True
        _drawing_state['intentional_pause'] = True  # Flag to prevent deactivate cleanup
        # Don't reset rubber band - keep it visible
        self.canvas.unsetMapTool(self)
        iface.messageBar().pushMessage(
            "Drawing Paused", 
            f"{len(self.points)} points saved. Click 'Resume Drawing' to continue.",
            level=0, duration=3
        )

    def deactivate(self):
        """
        Called when tool is deactivated (user switches to another tool).
        
        Cleanup behavior:
        - If intentional_pause=True: Keep state, rubber band stays visible
        - If intentional_pause=False: User switched away, clean everything up
        
        This ensures switching tools (without pause) removes the polygon, but
        explicit pause preserves it.
        """
        # If this wasn't an intentional pause, clean up everything
        if not _drawing_state.get('intentional_pause', False):
            self.rubberBand.reset(QgsWkbTypes.PolygonGeometry)
            _drawing_state['points'] = []
            _drawing_state['paused'] = False
            _drawing_state['tool'] = None
            _drawing_state['rubber_band'] = None
            # Update button text back to default
            if _pause_action:
                _pause_action.setText("Pause Drawing")
        # Reset the flag
        _drawing_state['intentional_pause'] = False
        QgsMapTool.deactivate(self)

    def finish_drawing(self):
        poly_geom = QgsGeometry.fromPolygonXY([self.points])
        
        dlg = ShapedTileConfigDialog(poly_geom, iface.mainWindow())
        if dlg.exec_() != QDialog.Accepted:
            # User cancelled - ensure tool stays active and polygon remains visible
            # Reactivate the tool in case dialog deactivated it
            iface.mapCanvas().setMapTool(self)
            return
        
        settings = dlg.get_settings()
        
        if not settings['OUTPUT_FILE']:
            QMessageBox.warning(None, "Error", "Please select an output file.")
            # User didn't select file - ensure tool stays active and continue drawing
            iface.mapCanvas().setMapTool(self)
            return
        
        # Only proceed with generation if everything is set
        self.generate_tiles(settings)

    def generate_tiles(self, settings):
        """
        Generate MBTiles from the drawn polygon and current map layers.
        
        Uses IncrementalTileGenerator with QTimer for non-blocking generation.
        Shows a progress dialog with time estimates and cancel button.
        
        Args:
            settings: Dict from ShapedTileConfigDialog.get_settings() containing
                      all configuration options and pre-calculated tile list
        """
        tiles = settings['TILES']
        
        if not tiles:
            QMessageBox.warning(None, "No Tiles", "No tiles intersect the drawn polygon.")
            return
        
        # Get current map layers
        layers = [l for l in iface.mapCanvas().layers() if l.isValid()]
        
        # Store reference to self for callback
        tool_ref = self
        
        def on_complete(success, message):
            """Callback when generation completes."""
            if success:
                iface.messageBar().pushMessage(
                    "Success", 
                    f"{message} to {os.path.basename(settings['OUTPUT_FILE'])}", 
                    level=3, duration=5
                )
                tool_ref.reset()
            else:
                iface.messageBar().pushMessage("Generation", message, level=1, duration=5)
        
        # Create and start incremental generator
        # Store reference to prevent garbage collection
        self._generator = IncrementalTileGenerator(settings, layers, on_complete)
        self._generator.start()

# ======================================================
# 7. LAUNCH
# ======================================================

# Store action reference for updating button text
_pause_action = None

def update_pause_button_text():
    """
    Update the pause/resume button text based on current drawing state.
    
    Button text states:
    - "Resume Drawing" when paused with points saved
    - "Pause Drawing" when actively drawing or idle
    """
    global _pause_action
    if _pause_action:
        if _drawing_state['paused']:
            _pause_action.setText("Resume Drawing")
        elif _drawing_state['tool'] and _drawing_state['points']:
            _pause_action.setText("Pause Drawing")
        else:
            _pause_action.setText("Pause Drawing")

def activate_shaped_tool():
    """
    Start a new drawing session.
    
    If there's an existing paused drawing, it's cleared first to ensure
    a clean slate. Activates ShapedMBTilesTool and updates button text.
    """
    # If there's an existing paused drawing, clear it first
    if _drawing_state['paused'] and _drawing_state['rubber_band']:
        _drawing_state['rubber_band'].reset(QgsWkbTypes.PolygonGeometry)
        _drawing_state['points'] = []
        _drawing_state['paused'] = False
    
    tool = ShapedMBTilesTool(iface.mapCanvas())
    iface.mapCanvas().setMapTool(tool)
    update_pause_button_text()

def toggle_pause_resume():
    """
    Toggle between paused and active drawing states.
    
    Behavior:
    - If paused with saved points: Resume drawing with those points
    - If actively drawing: Pause and save points
    - If no drawing: Show message to start drawing first
    """
    if _drawing_state['paused'] and _drawing_state['points']:
        # Currently paused - RESUME
        if _drawing_state['rubber_band']:
            _drawing_state['rubber_band'].reset(QgsWkbTypes.PolygonGeometry)
        tool = ShapedMBTilesTool(iface.mapCanvas(), resume_points=_drawing_state['points'])
        iface.mapCanvas().setMapTool(tool)
        iface.messageBar().pushMessage(
            "Drawing Resumed", 
            f"Continuing with {len(_drawing_state['points'])} points.",
            level=0, duration=2
        )
        update_pause_button_text()
    elif _drawing_state['tool'] and _drawing_state['points']:
        # Currently drawing - PAUSE
        _drawing_state['tool'].pause()
        update_pause_button_text()
    else:
        iface.messageBar().pushMessage(
            "No Drawing", 
            "Start drawing first with 'Draw Shaped MBTiles'.", 
            level=1, duration=2
        )

# Setup toolbar buttons
main_window = iface.mainWindow()

# Remove old actions if they exist
for action_text in ["Draw Shaped MBTiles", "Pause/Cancel Drawing", "Pause Drawing", "Resume Drawing"]:
    for a in main_window.findChildren(QAction):
        if a.text() == action_text:
            iface.removeToolBarIcon(a)
            a.deleteLater()

# Add Draw button (starts new drawing)
draw_action = QAction("Draw Shaped MBTiles", main_window)
draw_action.setShortcut("Ctrl+Shift+M")
draw_action.triggered.connect(activate_shaped_tool)
iface.addToolBarIcon(draw_action)

# Add Pause/Resume toggle button
_pause_action = QAction("Pause Drawing", main_window)
_pause_action.setShortcut("Ctrl+Shift+P")
_pause_action.triggered.connect(toggle_pause_resume)
iface.addToolBarIcon(_pause_action)

print("Shaped MBTiles Tool loaded!")
print("- Click 'Draw Shaped MBTiles' (Ctrl+Shift+M) to start a new drawing")
print("- Click 'Pause Drawing' (Ctrl+Shift+P) to pause/resume")
print("- Left-click to add points")
print("- Delete/Backspace key OR Shift+Right-click OR middle-click to undo last point")
print("- Right-click to finish drawing")
print("- ESC key to cancel and exit drawing mode")
