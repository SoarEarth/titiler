"""soar-stac Extension."""

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Query
from titiler.extensions.soar_util import StacExtent, create_stac_extent, save_or_post_data, send_post_request
from typing_extensions import Annotated, TypedDict

from fastapi import Depends, Query

from titiler.core.factory import BaseTilerFactory, FactoryExtension
import json

import requests

import logging
logger = logging.getLogger('uvicorn.error')

try:
    import pystac
    from pystac import Catalog
except ImportError:  # pragma: nocover
    pystac = None  # type: ignore

class StacCatalogChild(TypedDict):
    """Simplified data object of STAC collection"""
    id: str
    title: str
    description: str
    extent: StacExtent
    type: str
    url: str


@dataclass
class soarStacExtension(FactoryExtension):
    """Add Soar specific endpoints to read STAC"""

    def register(self, factory: BaseTilerFactory):
        """Register endpoint to the tiler factory."""

        assert pystac is not None, "'pystac' must be installed to use stacExtension"

        @factory.router.get(
            "/soar/createMetadataFromStacCatalog", 
            # response_model=Collection, 
            responses={200: {"description": "Return STAC catalog details"}},
        )
        def create_metadata_from_stac_catalog(
            src_path=Depends(factory.path_dependency),
            dest_path: Annotated[str, Query(description="Destination path to save or send via POST the MosaicJSON.")] = None,
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Return basic info."""
            res = dict()
            root_catalog = Catalog.from_file(src_path)
            res["catalog_id"] = root_catalog.id
            res["catalog_title"] = root_catalog.title
            res["catalog_description"] = root_catalog.description


            children = list(root_catalog.get_children())
            res["children"] = []
            res["total_children"] = len(children)

            for child in children:
                stacChild : StacCatalogChild = {
                    "id": child.id,
                    "title": child.title,
                    "description": child.description,
                    "href": child.get_self_href(),
                    "type": child.STAC_OBJECT_TYPE
                }
                if(child.STAC_OBJECT_TYPE == "Collection"):
                    stacChild["extent"] = create_stac_extent(child.extent)
                res["children"].append(stacChild)

            output_file_path = f"{dest_path}/catalog-{root_catalog.id.lower()}.json"
            messages = [save_or_post_data(dest_path, output_file_path, json.dumps(res))]

            response = {"messages": messages}
            if(return_data):
                response["data"] = res
            return response

