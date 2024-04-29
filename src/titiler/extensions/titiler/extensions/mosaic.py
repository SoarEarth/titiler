"""rio-stac Extension."""

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

from pathlib import Path
import requests
import morecantile
WEB_MERCATOR_TMS = morecantile.tms.get("WebMercatorQuad")

try:
    from rio_cogeo.cogeo import cog_info
    from rio_cogeo.models import Info
    import pystac
    from pystac.utils import datetime_to_str, str_to_datetime
    from rio_stac.stac import create_stac_item
    from pystac import Catalog, Collection, Asset
except ImportError:  # pragma: nocover
    cog_info = None  # type: ignore
    Info = None
    create_stac_item = None  # type: ignore
    pystac = None  # type: ignore
    str_to_datetime = datetime_to_str = None  # type: ignore
    Catalog = CollectionW = None  # type: ignore


class CreateBody(TypedDict):
    """POST Body for /create endpoint."""

    links: List[str]

class TitilerLayerMetadata(TypedDict):
    """Titiler Layer metadata for Soar listing."""

    id: str
    title: str
    description: str
    stac_url: str
    license: str
    bboxes:  Optional[list[list[float]]]
    intervals: Optional[list[list[str]]]
    extra_fields: Optional[dict[str, Any]]
    keywords: Optional[list[str]]
    bounds: Tuple[float, float, float, float] = [-180, -90, 180, 90]
    center: Optional[Tuple[float, float, int]]
    min_zoom: Optional[int]
    max_zoom: Optional[int]
    mosaic: dict[str, Any]

@dataclass
class mosaicExtension(FactoryExtension):
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
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Return basic info."""
            return MosaicJSON.from_urls(data["links"])
        
        @factory.router.get(
            "/createFromStacCollection", 
            # response_model=Collection, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_collection(
            src_path=Depends(factory.path_dependency),
            dest_path: Annotated[Optional[str], Query(description="Destination path to save the MosaicJSON.")] = None,
            use_metadata_only: Annotated[Optional[bool], Query(description="Destination path to save the MosaicJSON.")] = False,
        ):
            """Return basic info."""
            collection = Collection.from_file(src_path)
            items = list(collection.get_items())

            items = filter(lambda item: item.assets["visual"] is not None, items)
            data: MosaicJSON
            if(use_metadata_only):
                data = MosaicJSON.from_urls(assets)
            else:
                assets = []
                for item in items:
                    assets.append(item.assets["visual"].get_absolute_href())
                data = MosaicJSON.from_urls(assets)

            print(F"dest: {dest_path} - {dest_path is not None}")
            if(dest_path is not None):
                print(F"dest-startWith: {dest_path.startswith('/data/')} - {dest_path.startswith('https://')}")

            # map datetime into ISO format
            mapped_intervals : list[list[str]] = []
            for interval in collection.extent.temporal.intervals:
                mapped_intervals.append([datetime_to_str(interval[0]), datetime_to_str(interval[1])])

            metadata : TitilerLayerMetadata = {
                "id": collection.id,
                "title": collection.title,
                "description": collection.description,
                "stac_url": collection.get_self_href(),
                "license": collection.license,
                "bboxes": collection.extent.spatial.bboxes,
                "intervals": mapped_intervals,
                "extra_fields": collection.extra_fields,
                "keywords": collection.keywords,
                "mosaic": data.model_dump(),
                "bounds": data.bounds,
                "center": data.center,
                "min_zoom": data.minzoom,
                "max_zoom": data.maxzoom
            }



            if(dest_path is not None and (dest_path.startswith("/data/") or dest_path.startswith("data/"))):
                output_file = Path(f"{dest_path}/metadata/{collection.id.lower()}.json")
                output_file.parent.mkdir(exist_ok=True, parents=True)
                output_file.write_text(json.dumps(metadata))
                return 'MosaicJSON saved to ' + dest_path
            elif (dest_path is not None and dest_path.startswith("https://")):
                requests.post(dest_path, data = json.dumps(metadata))
                return 'MosaicJSON sent as POST request to ' + dest_path
            else:
                return metadata

        @factory.router.get(
            "/createFromStacCatalog", 
            # response_model=Collection, 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def create_mosaic_json_from_stac_catalog(
            src_path=Depends(factory.path_dependency),
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Return basic info."""
            res = dict()
            root_catalog = Catalog.from_file(src_path)
            res["root_catalog_id"] = root_catalog.id
            res["root_catalog_title"] = root_catalog.title
            res["root_catalog_description"] = root_catalog.description
            
            res["collections"] = []
            collections = list(root_catalog.get_collections())

            for collection in collections:
                res["collections"].append(collection.to_dict())

            return res
