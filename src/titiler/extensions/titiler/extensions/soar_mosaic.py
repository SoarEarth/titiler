"""rio-stac Extension."""
import pystac
import json
import rasterio
import logging
import time

from dataclasses import dataclass
from typing import List, Optional, cast
from typing_extensions import Annotated, TypedDict

from fastapi import Depends, Query, Body, Depends, Query
from titiler.extensions.soar_util import *
from titiler.extensions.soar_models import StacAsset, StacCatalogMetadata, StacItem, MosaicJSONMetadata
from titiler.core.factory import BaseTilerFactory, FactoryExtension

from cogeo_mosaic.mosaic import MosaicJSON
from cogeo_mosaic.utils import get_dataset_info
from pystac import Collection, Item, Catalog, Link
from pystac.utils import datetime_to_str
from cogeo_mosaic.models import Info as InfoMosaic
from datetime import datetime, timezone

logger = logging.getLogger('uvicorn.error')

class CreateBody(TypedDict):
    """POST Body for /create endpoint."""
    links: List[str]

class GenerateTilesBody(TypedDict):
    """POST Body for /create endpoint."""
    tiles: List[tuple[int, int, int]]

class MosaicJSONMetadataResponse(TypedDict):
    messages: List[str]
    data: Optional[MosaicJSONMetadata]

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
            "/soar/createFromStacCatalog", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_catalog(
            src_path=Depends(factory.path_dependency),
            is_collection: Annotated[bool, Query(description="Is STAC collection")] = False,
            metadata_path: Annotated[Optional[str], Query(description="Destination path to save the Soar metadata file.")] = None,
            mosaic_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Return basic info."""
            logger.info(F"Collection loading from {src_path}.")
            collection: Catalog | Collection
            if(is_collection):
                collection = Collection.from_file(src_path)
            else:
                collection = Catalog.from_file(src_path)
            logger.info(F"Collection {collection.id} loaded.")

            root_catalog_url = 'unknown'
            root_catalog_href = collection.get_root_link()
            if(root_catalog_href is not None):
                root_catalog_url = root_catalog_href.absolute_href
            logger.info(F"Collection {collection.title} is part of catalog {root_catalog_href}.")

            child_links = collection.get_child_links()
            logger.info(F"Collection {collection.title} has {len(child_links)} children.")
            children = [fetch_child(link) for link in child_links]

            assets_features = []
            assets_features_cog = []
            items_links = collection.get_item_links()
            logger.info(F"Collection {collection.title} has {len(items_links)} items.")
            items = []
            for index, link in enumerate(items_links):
                item = Item.from_file(link.absolute_href)
                bounds = item.bbox
                stac_item = StacItem(
                    id=item.id,
                    stac_url=link.absolute_href,
                    bounds=bounds,
                    properties=item.properties,
                    extra_fields=item.extra_fields
                )
                if(item.datetime is not None):
                    stac_item["datetime"] = datetime_to_str(item.datetime)
                if(bounds is not None):
                    stac_item["bounds_wkt"] = F"POLYGON(({bounds[0]} {bounds[1]}, {bounds[2]} {bounds[1]}, {bounds[2]} {bounds[3]}, {bounds[0]} {bounds[3]}, {bounds[0]} {bounds[1]}))"
                item_assets = []
                for i, k in enumerate(item.assets):
                    asset = item.assets[k]
                    item_assets.append(StacAsset(
                        key=k,
                        url=asset.get_absolute_href(),
                        title=asset.title,
                        description=asset.description,
                        type=asset.media_type,
                        roles=asset.roles,
                        extra_fields=asset.extra_fields
                    ))
                    if(k.lower() == "visual"):
                        url = asset.get_absolute_href()
                        geojson_feature = create_geojson_feature(bounds, url)
                        assets_features.append(geojson_feature)
                        current_count = len(assets_features)
                        if(current_count == 1 or current_count % 25 == 24 or (index + 1) == len(items_links)):
                            logger.info(F"Fetching cog feature: {url}")
                            assets_features_cog.append(get_dataset_info(url, WEB_MERCATOR_TMS))

                stac_item["assets"] = item_assets
                items.append(stac_item)
                if(index % 5 == 4):
                    progress = (index + 1) / len(items_links) * 100  # Calculate progress as a percentage
                    logger.info(f"Progress: {progress:.2f}% - index: {index + 1} of {len(items_links)} items processed.")

            logger.info(F"Collection {collection.title} has {len(assets_features)} items with visual asset.")

            data_min_zoom = {feat["properties"]["minzoom"] for feat in assets_features_cog}
            data_max_zoom = {feat["properties"]["maxzoom"] for feat in assets_features_cog}
            min_zoom = -1
            if(len(data_min_zoom) > 0):
                min_zoom = min(data_min_zoom)
            max_zoom = -1
            if(len(data_max_zoom) > 0):
                max_zoom = max(data_max_zoom)
            logger.info(F"Collection {collection.title} has min_zoom: {min_zoom} and max_zoom: {max_zoom}.")

            data: MosaicJSON | None = None
            if(len(assets_features) > 0):
                data = MosaicJSON.from_features(assets_features, min_zoom, max_zoom)
                logger.info(F"MosaicJSON created for {collection.title}.")

            metadata : StacCatalogMetadata = {
                "id": collection.id,
                "title": collection.title,
                "description": collection.description,
                "type": collection.STAC_OBJECT_TYPE,
                "stac_url": collection.get_self_href(),
                "extra_fields": collection.extra_fields,
                "root_catalog_url": root_catalog_url,
                "max_zoom": max_zoom,
                "min_zoom": min_zoom,
                "app_region": APP_REGION,
                "app_provider": APP_PROVIDER,
                "app_url": F"https://{APP_HOSTNAME}",
                "children_urls": [link.absolute_href for link in child_links],
                "children": [create_stac_child(child) for child in children],
                "total_children": len(child_links),
                "items": items,
                "total_items": len(items),
            }

            if(is_collection == True):
                metadata["license"] = collection.license
                metadata["extent"] = create_stac_extent(collection.extent)
                metadata["keywords"] = collection.keywords

            if(data is not None):
                metadata["mosaic"] = data.model_dump()
                metadata["bounds"] = data.bounds
                metadata["center"] = data.center
                metadata["bounds_wkt"] = F"POLYGON(({data.bounds[0]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[1]}, {data.bounds[2]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[3]}, {data.bounds[0]} {data.bounds[1]}))"

            messages = []
            if(mosaic_path is not None):
                output_file_mosaic = f"{mosaic_path.strip('/')}/{collection.id.lower()}.json"
                if(data is not None):
                    messages.append(save_or_post_data(mosaic_path, output_file_mosaic, data.model_dump_json()))
                    mosaic_path = F"{APP_DEST_PATH}/{output_file_mosaic}"
                    metadata["mosaic_path"] = mosaic_path
                    metadata["mosaic_layer_url"] = F"https://{APP_HOSTNAME}/mosaicjson/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={mosaic_path}"

            if(metadata_path is not None):
                formatted_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

                output_file_metadata = f"{metadata_path.strip('/')}/{collection.id.lower()}_{formatted_datetime}.json"
                messages.append(save_or_post_data(metadata_path, output_file_metadata, json.dumps(metadata)))

            response = {"messages": messages}
            if(return_data):
                response["data"] = metadata
            return response
        
        @factory.router.get(
            "/soar/getTiles", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def get_tiles(
            zoom: Annotated[int, Query(description="Zoom level")],
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Read a MosaicJSON and return z,x,y potential tiles for given zoom level"""
            with rasterio.Env(**env):
                with factory.reader(
                    src_path,
                    reader=factory.dataset_reader,
                    reader_options={**reader_params},
                    **backend_params,
                ) as src_dst:
                    mosaic : MosaicJSON = src_dst.mosaic_def
                    return bbox_to_tiles(mosaic.bounds, zoom)
                
        @factory.router.post(
            "/soar/generateTilesIntoCache", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def generate_tiles_into_cache(
            data: Annotated[GenerateTilesBody, Body(description="Tiles.")],
            cache_key: Annotated[str, Query(description="Cache key")],
            src_path=Depends(factory.path_dependency),
        ):
            """Generate tile into cache for given tiles"""
            tiles = data["tiles"]
            generate_tiles(tiles, cache_key, src_path)
            return F"Total of {len(tiles)} tiles were send to CF cache."
            
        @factory.router.get(
            "/soar/generateTilesIntoCache", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def generate_tiles_into_cache_by_zoom(
            cache_key: Annotated[str, Query(description="Cache key")],
            zoom: Annotated[int, Query(description="Zoom level")],
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Read a MosaicJSON"""
            with rasterio.Env(**env):
                with factory.reader(
                    src_path,
                    reader=factory.dataset_reader,
                    reader_options={**reader_params},
                    **backend_params,
                ) as src_dst:
                    mosaic : MosaicJSON = src_dst.mosaic_def
                    tiles = bbox_to_tiles(mosaic.bounds, zoom)
                    generate_tiles(tiles, cache_key, src_path)
                    return F"Total of {len(tiles)} tiles were send to CF cache."
        
        @factory.router.get(
            "/soar/metadata",
            response_model=MosaicJSONMetadataResponse,
            responses={200: {"description": "Return MosaicJSON Metadata"}},
        )
        def metadata(
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
            metadata_path: Annotated[Optional[str], Query(description="Destination path to save the Soar metadata file.")] = None,
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Read a MosaicJSON metadata"""
            src_path_encoded = encode_url_path_segments(src_path)
            with rasterio.Env(**env):
                with factory.reader(
                    src_path_encoded,
                    reader=factory.dataset_reader,
                    reader_options={**reader_params},
                    **backend_params,
                ) as src_dst:
                    info : InfoMosaic = src_dst.info()
                    bounds = info.bounds
                    bounds_wkt = F"POLYGON(({bounds[0]} {bounds[1]}, {bounds[2]} {bounds[1]}, {bounds[2]} {bounds[3]}, {bounds[0]} {bounds[3]}, {bounds[0]} {bounds[1]}))"
                    tile_url = F"https://{APP_HOSTNAME}/mosaicjson/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={encode_url_path_segments(src_path_encoded)}"
                    metadata:  MosaicJSONMetadata = {
                        "max_zoom": info.maxzoom,
                        "min_zoom": info.minzoom,
                        "bounds_wkt": bounds_wkt,
                        "tile_url": tile_url,
                        "tilematrixset": info.tilematrixset,
                        "center": info.center
                    }
                    messages = []
                    if(metadata_path is not None):
                        output_file_metadata = f"{metadata_path.strip('/')}/cog_metadata.json"
                        messages.append(save_or_post_data(metadata_path, output_file_metadata, to_json(metadata)))

                    response : MosaicJSONMetadataResponse = {"messages": messages}
                    if(return_data):
                        response["data"] = metadata
                    else:
                        response["data"] = None
                    return response

def generate_tiles(tiles, cache_key, src_path):
    total = len(tiles)
    skip_cache_check = False
    for idx, tile in enumerate(tiles):
        z, x, y = tile
        start_time = time.time()  # Record the start time
        if(skip_cache_check == True or exists_in_cache(cache_key, z, x, y) == False):
            fetch_tile_and_forward_to_cf_mosaic(cache_key, src_path, z, x, y)
            skip_cache_check = True
        end_time = time.time()  # Record the end time
        elapsed_time = end_time - start_time  # Calculate elapsed time
        logger.info(f"Processed {idx} of {total}. [{cache_key},{z},{x},{y}] took {elapsed_time:.2f} seconds")

def fetch_children(links: list[Link]) -> list[Collection | Catalog]:
    """Fetch STAC children."""
    children = []
    for link in links:
        child = fetch_child(link)
        if child is not None:
            children.append(child)
    return children         

def fetch_child(link: Link) -> Collection | Catalog | None:
    """Fetch a STAC child."""
    try:
        link.resolve_stac_object()
        return cast([Collection | Catalog], link.target)
    except:
        return None
