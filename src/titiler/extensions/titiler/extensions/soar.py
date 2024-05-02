"""rio-stac Extension."""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from fastapi import Depends, Query
from typing_extensions import Annotated, TypedDict

from cogeo_mosaic.models import Info as mosaicInfo
from cogeo_mosaic.mosaic import MosaicJSON
import rasterio
from fastapi import APIRouter, Body, Depends, Path, Query

from titiler.core.factory import BaseTilerFactory, FactoryExtension
from rio_tiler.io import COGReader
import json
import datetime
from typing import List

from cogeo_mosaic.utils import get_dataset_info

from pathlib import Path
import requests
import morecantile

import logging
# logger = logging.getLogger()
# logger = logging.getLogger(__name__)
logger = logging.getLogger('uvicorn.error')
# from fastapi.logger import logger

WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")

try:
    from rio_cogeo.cogeo import cog_info
    from rio_cogeo.models import Info
    import pystac
    from pystac.utils import datetime_to_str, str_to_datetime
    from rio_stac.stac import create_stac_item
    from pystac import Catalog, Collection, Asset, Item, Extent, Link
except ImportError:  # pragma: nocover
    cog_info = None  # type: ignore
    Info = None
    create_stac_item = None  # type: ignore
    pystac = None  # type: ignore
    str_to_datetime = datetime_to_str = None  # type: ignore
    # Catalog = Collection = Asset = Item = None  # type: ignore


class CreateBody(TypedDict):
    """POST Body for /create endpoint."""

    links: List[str]

class StacExtent(TypedDict):
    """STAC Extent."""
    spatial: Optional[list[list[float]]]
    temporal: Optional[list[list[str]]]

class TitilerLayerMetadata(TypedDict):
    """Titiler Layer metadata for Soar listing."""

    id: str
    title: str
    description: str
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
    children_urls: Optional[List[str]]
    total_children: Optional[int]

class StacCollection(TypedDict):
    """Simplified data object of STAC collection"""
    id: str
    title: str
    description: str
    extent: StacExtent
    catalog_type: str
    url: str

@dataclass
class soarExtension(FactoryExtension):
    """Add /create endpoint to a Mosaic TilerFactory."""

    def register(self, factory: BaseTilerFactory):
        """Register endpoint to the tiler factory."""

        assert (
            cog_info is not None
        ), "'rio-cogeo' must be installed to use CogValidateExtension"

        @factory.router.post(
            "/createFromList", 
            response_model=MosaicJSON, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_list(
            data: Annotated[CreateBody, Body(description="COGs details.")],
        ):
            """Return basic info."""
            return MosaicJSON.from_urls(data["links"])

        @factory.router.get(
            "/soar/createFromStacCollection", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_collection(
            src_path=Depends(factory.path_dependency),
            dest_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            return_only: Annotated[bool, Query(description="Return metadata dto and don't save/send data to destination")] = False,
        ):
            """Return basic info."""
            logger.info(F"Collection loading from {src_path}.")
            collection = Collection.from_file(src_path)
            logger.info(F"Collection {collection.id} loaded.")

            root_catalog = collection.get_root()

            children_links = collection.get_child_links()
            logger.info(F"Collection {collection.title} has {len(children_links)} children.")
            children_urls = [child.get_absolute_href() for child in children_links]

            assets_features = []
            assets_features_cog = []
            items_links = collection.get_item_links()
            logger.info(F"Collection {collection.title} has {len(items_links)} items.")

            for index, link in enumerate(items_links):
                # print(link.to_dict())
                item = Item.from_file(link.absolute_href)
                if(item.assets["visual"] is not None):
                    url = item.assets["visual"].get_absolute_href()
                    geojson_feature = create_geojson_feature(item, url)
                    assets_features.append(geojson_feature)
                    current_count = len(assets_features)
                    if(current_count == 1 or current_count % 25 == 24 or (index + 1) == len(items_links)):
                        logger.info(F"Fetching cog feature: {url}")
                        cog_feature = get_dataset_info(url, WEB_MERCATOR_TMS)
                        assets_features_cog.append(cog_feature)

                if(index % 5 == 4):
                    progress = (index + 1) / len(items_links) * 100  # Calculate progress as a percentage
                    logger.info(f"Progress: {progress:.2f}% - index: {index + 1} of {len(items_links)} items processed.")

            logger.info(F"Collection {collection.title} has {len(assets_features)} items with visual asset.")

            data_min_zoom = {feat["properties"]["minzoom"] for feat in assets_features_cog}
            data_max_zoom = {feat["properties"]["maxzoom"] for feat in assets_features_cog}
            min_zoom = max(data_min_zoom)
            max_zoom = max(data_max_zoom)
            logger.info(F"Collection {collection.title} has min_zoom: {min_zoom} and max_zoom: {max_zoom}.")

            data: MosaicJSON | None = None
            if(len(assets_features) > 0):
                data = MosaicJSON.from_features(assets_features, min_zoom, max_zoom)
            
            logger.info(F"MosaicJSON created for {collection.title}.")

            metadata : TitilerLayerMetadata = {
                "id": collection.id,
                "title": collection.title,
                "description": collection.description,
                "stac_url": collection.get_self_href(),
                "license": collection.license,
                "extent": create_stac_extent(collection.extent),
                "extra_fields": collection.extra_fields,
                "keywords": collection.keywords,
                "total_assets": len(assets_features),
                "root_catalog_id": root_catalog.id,
                "root_catalog_title": root_catalog.title,
                "root_catalog_url": root_catalog.get_self_href(),
                "app_region": os.getenv("APP_REGION"),
                "app_provider": os.getenv("APP_PROVIDER"),
                "app_url": os.getenv("APP_URL"),
                "assets_features": assets_features,
                "children_urls": children_urls,
                "total_children": len(children_urls),
                "max_zoom": max_zoom,
                "min_zoom": min_zoom
            }

            if(data is not None):
                metadata["mosaic"] = data.model_dump()
                metadata["bounds"] = data.bounds
                metadata["center"] = data.center
                metadata["bounds_wkt"] = F"POLYGON(({data.bounds[0]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[1]}))"

            messages = []
            app_dest_path = os.getenv("APP_DEST_PATH")
            if (return_only == False and dest_path is not None and dest_path.startswith("https://")):
                requests.post(dest_path, data = json.dumps(metadata))
                logger.info(F"Sent as POST request to {dest_path}")
                messages.append(F"Sent as POST request to {dest_path}")
            else: 
                messages.append(F"Destination path is not valid or not provided for POST request.")
            if(return_only == False and app_dest_path is not None):
                output_file_metadata = Path(f"{app_dest_path}/metadata/{collection.id.lower()}.json")
                output_file_metadata.parent.mkdir(exist_ok=True, parents=True)
                output_file_metadata.write_text(json.dumps(metadata))
                logger.info(F"Metadata saved to {output_file_metadata.absolute()}")
                messages.append(F"Metadata saved to {output_file_metadata.absolute()}")
                if(data is not None):
                    output_file_mosaic = Path(f"{app_dest_path}/mosaic/{collection.id.lower()}.json")
                    output_file_mosaic.parent.mkdir(exist_ok=True, parents=True)
                    output_file_mosaic.write_text(data.model_dump_json())
                    logger.info(F"MosaicJSON saved to {output_file_mosaic.absolute()}")
                    messages.append(F"MosaicJSON saved to {output_file_mosaic.absolute()}")
            else:
                messages.append(F"Destination path is not valid or not provided for saving metadata and mosaicjson.")

            if(return_only == False):
                return messages
            else:
                return metadata

        @factory.router.get(
            "/soar/getStacCatalogDetails", 
            # response_model=Collection, 
            responses={200: {"description": "Return STAC catalog details"}},
        )
        def get_stac_catalog_details(
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Return basic info."""
            res = dict()
            root_catalog = Catalog.from_file(src_path)
            res["catalog_id"] = root_catalog.id
            res["catalog_title"] = root_catalog.title
            res["catalog_description"] = root_catalog.description
            res["collections"] = []
            collections = list(root_catalog.get_collections())
            res["total_collections"] = len(collections)

            for collection in collections:
                stacCollection : StacCollection = {
                    "id": collection.id,
                    "title": collection.title,
                    "description": collection.description,
                    "extent": create_stac_extent(collection.extent),
                    "href": collection.get_self_href(),
                    "catalog_type": collection.catalog_type,

                }
                res["collections"].append(stacCollection)

            return res
        
def create_geojson_feature(
    stac_item: Item,
    url: str,
    tms: morecantile.TileMatrixSet = WEB_MERCATOR_TMS,
    ) -> Dict:
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

def transform_link(link: Link) -> object:
    return {
        "href": link.href,
        "rel": link.rel,
        "title": link.title,
        "mediaType": link.media_type,
    }