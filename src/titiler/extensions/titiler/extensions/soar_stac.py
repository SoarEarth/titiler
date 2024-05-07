"""soar-stac Extension."""

from dataclasses import dataclass

from fastapi import Depends, Query
from titiler.extensions.soar_util import StacCatalogMetadata, create_stac_child, save_or_post_data
from typing_extensions import Annotated

from fastapi import Depends, Query

from titiler.core.factory import BaseTilerFactory, FactoryExtension
import json

import logging
logger = logging.getLogger('uvicorn.error')

try:
    import pystac
    from pystac import Catalog
except ImportError:  # pragma: nocover
    pystac = None  # type: ignore




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
            logger.info(f"Creating metadata from STAC catalog: {src_path}")
            root_catalog = Catalog.from_file(src_path)
            logger.info(f"STAC catalog {root_catalog.id} loaded.")
            children = list(root_catalog.get_children())
            logger.info(f"STAC catalog {root_catalog.id} has {len(children)} children.")
            metadata : StacCatalogMetadata = {
                "id": root_catalog.id,
                "title": root_catalog.title,
                "description": root_catalog.description,
                "children": [create_stac_child(child) for child in children],
                "total_children": len(children)
            }
            output_file_path = f"{dest_path.strip('/')}/{root_catalog.id.lower()}.json"
            messages = [save_or_post_data(dest_path, output_file_path, json.dumps(metadata))]

            response = {"messages": messages}
            if(return_data):
                response["data"] = metadata
            return response

