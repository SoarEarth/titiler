import datetime
import morecantile
import os
from typing_extensions import Annotated, TypedDict
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from pystac import Catalog, Collection, Asset, Item, Extent, Link
from pystac.utils import datetime_to_str, str_to_datetime
from pathlib import Path
import logging
import requests

logger = logging.getLogger('uvicorn.error')

WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")
APP_DEST_PATH = os.getenv("APP_DEST_PATH")

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
    url: str

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
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    bounds_wkt: Optional[str]
    center: Optional[Tuple[float, float, int]]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    mosaic: dict[str, Any]
    assets_features: List[Dict[str, Any]]
    total_assets: Optional[int]
    app_region: Optional[str]
    app_provider: Optional[str]
    app_url: Optional[str]
    children: Optional[List[StacChild]]
    total_children: Optional[int]
    root_catalog_url: Optional[str]

class StacCatalogMetadata(TypedDict):
    """STAC Catalog metadata for Soar integration"""
    id: str
    title: str
    description: str
    children: List[StacChild]
    total_children: int

def create_geojson_feature(
    stac_item: Item,
    url: str,
    tms: morecantile.TileMatrixSet = WEB_MERCATOR_TMS,
    ) -> GeojsonFeature:
        """Get dataset meta from STACK asset."""
        bounds = stac_item.bbox
        return {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        tms.truncate_lnglat(bounds[0], bounds[3]),
                        tms.truncate_lnglat(bounds[0], bounds[1]),
                        tms.truncate_lnglat(bounds[2], bounds[1]),
                        tms.truncate_lnglat(bounds[2], bounds[3]),
                        tms.truncate_lnglat(bounds[0], bounds[3]),
                    ]
                ],
            },
            "properties": {
                "path": url,
                "bounds": bounds,
                "bounds_wkt": F"POLYGON(({bounds[0]} {bounds[1]}, {bounds[2]} {bounds[1]}, {bounds[2]} {bounds[3]}, {bounds[0]} {bounds[3]}, {bounds[0]} {bounds[1]}))",
                "stac_id": stac_item.id,
                "stac_href": stac_item.get_self_href(),
                "stac_properties": stac_item.properties,
            },
            "type": "Feature",
        }

def create_stac_extent(ext: Extent) -> StacExtent:
    """Create STAC Extent."""
    # map datetime into ISO format
    mapped_intervals : list[list[str]] = []
    for interval in ext.temporal.intervals:
        mapped_intervals.append([datetime_to_str(interval[0]), datetime_to_str(interval[1])])
    return {
        "spatial": ext.spatial.bboxes,
        "temporal": mapped_intervals,
    }

def create_stac_child(child: Catalog | Collection) -> StacChild:
    """Create StacChild from pystac"""
    stacChild : StacChild = {
        "id": child.id,
        "title": child.title,
        "description": child.description,
        "href": child.get_self_href(),
        "type": child.STAC_OBJECT_TYPE
    }
    if(child.STAC_OBJECT_TYPE == "Collection"):
        stacChild["extent"] = create_stac_extent(child.extent)
    return stacChild

def transform_link(link: Link) -> object:
    return {
        "href": link.href,
        "rel": link.rel,
        "title": link.title,
        "mediaType": link.media_type,
    }


def save_or_post_data(dest_path: str, file_path: str, content: str) -> str:
    msg = F"dest_path [{dest_path}] or file_path [{file_path}] are not defined or are invalid"
    if(dest_path is not None):
        if (dest_path.startswith("https://")):
            logger.info(F"Sending file via POST to: {dest_path}")
            requests.post(dest_path, data=content)
            msg = F"File sent:  {dest_path}"
        else:
            logger.info(F"Saving file: {file_path}")
            file = Path(F"{APP_DEST_PATH}/{file_path}")
            file.parent.mkdir(exist_ok=True, parents=True)
            file.write_text(content)
            msg = F"File saved:  {file.absolute()}"
    logger.info(msg)
    return msg