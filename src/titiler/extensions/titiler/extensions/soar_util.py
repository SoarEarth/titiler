import morecantile
import os
from titiler.extensions.soar_models import GeojsonFeature, StacChild, StacExtent
from pystac import Catalog, Collection, Extent, Link
from pystac.utils import datetime_to_str, str_to_datetime
from pathlib import Path

import logging
import requests

logger = logging.getLogger('uvicorn.error')

WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")
APP_DEST_PATH = os.getenv("APP_DEST_PATH")
APP_REGION = os.getenv("APP_REGION")
APP_PROVIDER = os.getenv("APP_PROVIDER")
APP_HOSTNAME = os.getenv("APP_HOSTNAME")

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