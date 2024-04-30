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
    center: Optional[Tuple[float, float, int]]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    mosaic: dict[str, Any]
    assets_urls: List[str]
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

        DEST_PATH = os.getenv("DEST_PATH")

        @factory.router.post(
            "/createFromList", 
            response_model=MosaicJSON, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_list(
            data: Annotated[CreateBody, Body(description="COGs details.")],
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Return basic info."""
            return MosaicJSON.from_urls(data["links"])
        
        @factory.router.get(
            "/soar/createFromStacCollection", 
            # response_model=Collection, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_collection(
            src_path=Depends(factory.path_dependency),
            dest_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            use_metadata_only: Annotated[Optional[bool], Query(description="Destination path to save the MosaicJSON.")] = False,
            min_zoom: Annotated[Optional[int], Query(description="Min zoom to be used if use_metadata_only is true, default 12")] = 12,
            max_zoom: Annotated[Optional[int], Query(description="Min zoom to be used if use_metadata_only is true, default 20")] = 20,
        ):
            """Return basic info."""
            logger.info(F"Collection loading from {src_path}.")
            collection = Collection.from_file(src_path)
            logger.info(F"Collection {collection.id} loaded.")


            children_links = collection.get_child_links()
            logger.info(F"Collection {collection.title} has {len(children_links)} children.")
            children_urls = [child.get_absolute_href() for child in children_links]

            assets_urls = []
            assets_features = []
            items_links = collection.get_item_links()
            logger.info(F"Collection {collection.title} has {len(items_links)} items.")

            for index, link in enumerate(items_links):
                # print(link.to_dict())
                item = Item.from_file(link.absolute_href)
                if(item.assets["visual"] is not None):
                        url = item.assets["visual"].get_absolute_href()
                        geojson_feature = create_geojson_feature(item.bbox, url)
                        assets_urls.append(url)
                        assets_features.append(geojson_feature)
                        count = index + 1
                        if(count % 5 == 0 or count == len(items_links)):
                            progress = count / len(items_links) * 100  # Calculate progress as a percentage
                            logger.info(f"Progress: {progress:.2f}% - index: {count} of {len(items_links)} items processed.")

            logger.info(F"Collection {collection.title} has {len(assets_urls)} items with visual asset.")

            data: MosaicJSON | None = None
            if(use_metadata_only and len(assets_features) > 0):
                data = MosaicJSON.from_features(assets_features, min_zoom, max_zoom)
            elif (len(assets_urls) > 0):
                data = MosaicJSON.from_urls(assets_urls)

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
                "total_assets": len(assets_urls),
                "app_region": os.getenv("APP_REGION"),
                "app_provider": os.getenv("APP_PROVIDER"),
                "app_url": os.getenv("APP_URL"),
                "assets_urls": assets_urls,
                "children_urls": children_urls,
                "total_children": len(children_urls)
            }

            if(data is not None):
                metadata["mosaic"] = data.model_dump()
                metadata["bounds"] = data.bounds
                metadata["center"] = data.center
                metadata["min_zoom"] = data.minzoom
                metadata["max_zoom"] = data.maxzoom


            if (dest_path is not None and dest_path.startswith("https://")):
                requests.post(dest_path, data = json.dumps(metadata))
                return 'Sent as POST request to ' + dest_path
            elif(DEST_PATH is not None):
                output_file_metadata = Path(f"{DEST_PATH}/metadata/{collection.id.lower()}.json")
                output_file_metadata.parent.mkdir(exist_ok=True, parents=True)
                output_file_metadata.write_text(json.dumps(metadata))
                if(data is not None):
                    output_file_mosaic = Path(f"{DEST_PATH}/mosaic/{collection.id.lower()}.json")
                    output_file_mosaic.parent.mkdir(exist_ok=True, parents=True)
                    output_file_mosaic.write_text(data.model_dump_json())
                return f"Saved to {DEST_PATH}"
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
    bounds: Tuple[float, float, float, float],
    url: str,
    tms: morecantile.TileMatrixSet = WEB_MERCATOR_TMS,
    ) -> Dict:
        """Get dataset meta from STACK asset."""
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