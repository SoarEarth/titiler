from typing_extensions import TypedDict
from typing import Any, Dict, List, Literal, Optional, Tuple

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

class StacAssetFeature(TypedDict):
    """STAC Asset Feature."""
    id: str
    title: str
    description: str
    type: str
    url: str
    extent: StacExtent
    extra_fields: Optional[dict[str, Any]]
    keywords: Optional[list[str]]
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    bounds_wkt: Optional[str]
    center: Optional[Tuple[float, float, int]]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    mosaic: dict[str, Any]
    children: Optional[List[StacChild]]
    total_children: Optional[int]
    root_catalog_url: Optional[str]

class StacCollectionMetadata(TypedDict):
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
    assets_features: List[Dict[str, Any]]
    total_assets: Optional[int]
    children: Optional[List[StacChild]]
    total_children: Optional[int]
    mosaic: dict[str, Any]
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    bounds_wkt: Optional[str]
    center: Optional[Tuple[float, float, int]]
    mosaic_path: str
    mosaic_layer_url: str

class StacCatalogMetadata(TypedDict):
    """STAC Catalog metadata for Soar integration"""
    id: str
    title: str
    description: str
    children: List[StacChild]
    total_children: int
    stac_url: str