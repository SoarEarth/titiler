"""rio-stac Extension."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, Query
from typing_extensions import Annotated, TypedDict

from cogeo_mosaic.models import Info as mosaicInfo
from cogeo_mosaic.mosaic import MosaicJSON
import rasterio
from fastapi import APIRouter, Body, Depends, Path, Query

from titiler.core.factory import BaseTilerFactory, FactoryExtension
from rio_tiler.io import COGReader

try:
    from rio_cogeo.cogeo import cog_info
    from rio_cogeo.models import Info
    import pystac
    from pystac.utils import datetime_to_str, str_to_datetime
    from rio_stac.stac import create_stac_item
    from pystac import Catalog, Collection
except ImportError:  # pragma: nocover
    cog_info = None  # type: ignore
    Info = None
    create_stac_item = None  # type: ignore
    pystac = None  # type: ignore
    str_to_datetime = datetime_to_str = None  # type: ignore
    Catalog = Collection = None  # type: ignore


class CreateBody(TypedDict, total=False):
    """STAC Item."""

    links: List[str]

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
            backend_params=Depends(factory.backend_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
        ):
            """Return basic info."""
            collection = Collection.from_file(src_path)
            # collection.make_all_asset_hrefs_absolute()
            # return collection.to_dict()
            items = list(collection.get_items())
            # return items
            assets = []
            for item in items:
                assets.append(item.assets["visual"].get_absolute_href())
            # return assets
            return MosaicJSON.from_urls(assets)

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
