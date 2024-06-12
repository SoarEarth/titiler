from typing_extensions import TypedDict
from typing import Any, Dict, List, Literal, Optional, Tuple
from rio_tiler.models import Info as InfoTiler
from rio_cogeo.models import Info as InfoCogeo

class GeojsonGemetry(TypedDict):
    """GeoJSON Geometry."""
    type: Literal["Polygon"]
    coordinates: List[List[Tuple[float, float]]]

class GeojsonProperties(TypedDict):
    """GeoJSON Properties."""
    path: str
    bounds: list[float]
    bounds_wkt: str
    stac_id: str
    stac_href: str
    stac_properties: Dict[str, Any]


class GeojsonFeature(TypedDict):
    """GeoJSON Feature."""
    type: Literal["Feature"]
    properties: GeojsonProperties
    geometry: GeojsonGemetry

class StacExtent(TypedDict):
    """STAC Extent."""
    spatial: Optional[list[list[float]]]
    temporal: Optional[list[list[str]]]

class StacChild(TypedDict):
    """Simplified data object of STAC collection"""
    id: str
    title: str
    description: str
    extent: StacExtent
    type: str
    stac_url: str

class StacAsset(TypedDict):
    key: str
    url: str
    title: str
    description: str
    media_type: str
    roles: list[str]
    extra_fields: Optional[dict[str, Any]]

class StacItem(TypedDict):
    id: str
    stac_url: str
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    bounds_wkt: Optional[str]
    datetime: str | None
    properties: dict[str, Any]
    extra_fields: Optional[dict[str, Any]]
    assets: Optional[List[StacAsset]]

class StacCatalogMetadata(TypedDict):
    """STAC Collection metadata for Soar integration."""
    id: str
    title: str
    description: str
    type: str
    stac_url: str
    license: str
    extent: StacExtent
    extra_fields: Optional[dict[str, Any]]
    keywords: Optional[list[str]]
    root_catalog_url: Optional[str]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    app_region: Optional[str]
    app_provider: Optional[str]
    app_url: Optional[str]
    items: Optional[List[StacItem]]
    total_items: Optional[int]
    children: Optional[List[StacChild]]
    total_children: Optional[int]
    mosaic: dict[str, Any]
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    bounds_wkt: Optional[str]
    center: Optional[Tuple[float, float, int]]
    mosaic_path: str
    mosaic_layer_url: str

class COGMetadata(TypedDict):
    """COG metadata."""
    info_tiler: Optional[InfoTiler]
    info_cogeo: Optional[InfoCogeo]
    is_valid: bool
    bounds_wkt: Optional[str]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    tile_url: Optional[str]
    errors: Optional[List[str]]
    warnings: Optional[List[str]]
