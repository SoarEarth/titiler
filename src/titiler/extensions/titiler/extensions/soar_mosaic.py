"""rio-stac Extension."""

import os
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends, Query
from titiler.extensions.soar_util import create_geojson_feature, create_stac_extent, save_or_post_data, TitilerLayerMetadata
from typing_extensions import Annotated, TypedDict

from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import Body, Depends, Path, Query

from titiler.core.factory import BaseTilerFactory, FactoryExtension
import json
from typing import List

from cogeo_mosaic.utils import get_dataset_info

import morecantile

import logging
logger = logging.getLogger('uvicorn.error')

WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")

try:
    import pystac
    from pystac import Collection, Item
except ImportError:  # pragma: nocover
    pystac = None  # type: ignore


class CreateBody(TypedDict):
    """POST Body for /create endpoint."""

    links: List[str]

@dataclass
class soarMosaicExtension(FactoryExtension):
    """Add /create endpoint to a Mosaic TilerFactory."""

    def register(self, factory: BaseTilerFactory):
        """Register endpoint to the tiler factory."""

        assert pystac is not None, "'pystac' must be installed to use stacExtension"

        @factory.router.post(
            "/soar/createFromList", 
            response_model=MosaicJSON, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_list(
            data: Annotated[CreateBody, Body(description="COGs details.")],
            mosaic_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            return_result: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Create MosaicJSON from given list of COGs links."""
            data = MosaicJSON.from_urls(data["links"])

            messages = []
            output_file_mosaic = f"{mosaic_path}/mosaic.json"
            
            # if (metadata_path is not None):
            #     messages.append(save_or_send_file(output_file_metadata, json.dumps(metadata)))
            if(data is not None and mosaic_path is not None):
                messages.append(save_or_post_data(output_file_mosaic, data.model_dump_json()))
            
            if(return_result):
                return data
            return messages

        @factory.router.get(
            "/soar/createFromStacCollection", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_collection(
            src_path=Depends(factory.path_dependency),
            metadata_path: Annotated[Optional[str], Query(description="Destination path to save the Soar metadata file.")] = None,
            mosaic_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Return basic info."""
            logger.info(F"Collection loading from {src_path}.")
            collection = Collection.from_file(src_path)
            logger.info(F"Collection {collection.id} loaded.")

            root_catalog = collection.get_root()
            catalog_id = getattr(root_catalog, "id", 'unknown').lower()
            logger.info(F"Collection {collection.title} is part of catalog {catalog_id}.")

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
                "type": collection.STAC_OBJECT_TYPE,
                "stac_url": collection.get_self_href(),
                "license": collection.license,
                "extent": create_stac_extent(collection.extent),
                "extra_fields": collection.extra_fields,
                "keywords": collection.keywords,
                "total_assets": len(assets_features),
                "root_catalog_id": catalog_id,
                "app_region": os.getenv("APP_REGION"),
                "app_provider": os.getenv("APP_PROVIDER"),
                "app_url": os.getenv("APP_URL"),
                "assets_features": assets_features,
                "children_urls": children_urls,
                "total_children": len(children_urls),
                "max_zoom": max_zoom,
                "min_zoom": min_zoom
            }

            if(root_catalog is not None):
                metadata["root_catalog_title"] = root_catalog.title
                metadata["root_catalog_url"] = root_catalog.get_self_href()

            if(data is not None):
                metadata["mosaic"] = data.model_dump()
                metadata["bounds"] = data.bounds
                metadata["center"] = data.center
                metadata["bounds_wkt"] = F"POLYGON(({data.bounds[0]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[1]}))"

            messages = []
            output_file_metadata = f"{metadata_path}/metadata-{collection.id.lower()}.json"
            output_file_mosaic = f"{mosaic_path}/mosaic-{collection.id.lower()}.json"

            messages.append(save_or_post_data(metadata_path, output_file_metadata, json.dumps(metadata)))
            messages.append(save_or_post_data(mosaic_path, output_file_mosaic, data.model_dump_json()))

            response = {"messages": messages}
            if(return_data):
                response["data"] = metadata
            return response