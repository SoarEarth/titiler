import morecantile
import os
from titiler.extensions.soar_models import GeojsonFeature, StacChild, StacExtent
from pystac import Catalog, Collection, Extent, Link
from pystac.utils import datetime_to_str, str_to_datetime
from pathlib import Path
import math
import logging
import requests

logger = logging.getLogger('uvicorn.error')

WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")
APP_DEST_PATH = os.getenv("APP_DEST_PATH")
APP_REGION = os.getenv("APP_REGION")
APP_PROVIDER = os.getenv("APP_PROVIDER")
APP_HOSTNAME = os.getenv("APP_HOSTNAME")
CF_HOSTNAME = os.getenv("CF_HOSTNAME")
CF_SECRET = os.getenv("CF_SECRET")
APP_SELF_URL = os.getenv("APP_SELF_URL")

def create_geojson_feature(
    bounds: list[float],
    url: str,
    tms: morecantile.TileMatrixSet = WEB_MERCATOR_TMS,
    ) -> GeojsonFeature:
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
                "bounds": bounds
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
        "stac_url": child.get_self_href(),
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


def latlon_to_tile(lat, lon, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def bbox_to_tiles(bbox, zoom):
    logger.info(F"bbox: {bbox}, zoom: {zoom}")
    min_lon, min_lat, max_lon, max_lat = bbox

    # Convert bounding box to tile coordinates
    min_tile = latlon_to_tile(min_lat, min_lon, zoom)
    max_tile = latlon_to_tile(max_lat, max_lon, zoom)
    logger.info(F"min_tile: {min_tile}, max_tile: {max_tile}")
    min_x, max_y = min_tile
    max_x, min_y = max_tile
    logger.info(F"min_x: {min_x}, min_y: {min_y}, max_x: {max_x}, max_y: {max_y}")
    tiles = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            tiles.append((zoom, x, y))
    logger.info(F"Total tiles: {len(tiles)}")
    return tiles


def fetch_tile_and_forward_to_cf(cache_key, src_path, zoom, x, y):
    headers = {
        'soar-secret-key': CF_SECRET,
        'Content-Type': 'image/png'
    }
    response = requests.get(F"{APP_SELF_URL}/mosaicjson/tiles/WebMercatorQuad/{zoom}/{x}/{y}.png?url={src_path}", stream=True)
    if response.status_code == 200 or response.status_code == 204:
        # Forwarding the PNG file to the new location with new headers
        cf_url = F"https://{CF_HOSTNAME}/tile-cache?cacheKey={cache_key}&z={zoom}&x={x}&y={y}"
        forward_response = requests.post(cf_url, headers=headers, data=response.raw)
        logger.info(F"Tile pushed: {cf_url} with response {forward_response.status_code}")

        # Checking if the forward request was successful
        if forward_response.status_code != 200:
            logger.info(f'Failed to forward data. Status code: {forward_response.status_code}')
            logger.info(forward_response.text)
    else:
        logger.info(f'Failed to fetch data from the original URL. Status code: {response.status_code}')
        logger.info(response.text)