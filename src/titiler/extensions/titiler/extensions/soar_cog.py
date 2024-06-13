"""rio-cogeo Extension."""

from dataclasses import dataclass
from typing import List, Optional, Type
from titiler.extensions.soar_util import APP_HOSTNAME, save_or_post_data, to_json, fetch_preview, save_or_post_bytes
from typing_extensions import TypedDict
import rasterio
import logging

from fastapi import Depends, Query, Response
from titiler.extensions.soar_models import COGMetadata
from typing_extensions import Annotated

from titiler.core.factory import BaseTilerFactory, FactoryExtension
from titiler.core.dependencies import DefaultDependency, PreviewParams

try:
    from rio_cogeo.cogeo import cog_info
except ImportError:  # pragma: nocover
    cog_info = None  # type: ignore

logger = logging.getLogger('uvicorn.error')

class COGMetadataResponse(TypedDict):
    messages: List[str]
    data: Optional[COGMetadata]

@dataclass
class soarCogExtension(FactoryExtension):
    """Add /soar endpoints to a COG TilerFactory."""

    backend_dependency: Type[DefaultDependency] = DefaultDependency
    img_preview_dependency: Type[DefaultDependency] = PreviewParams


    def register(self, factory: BaseTilerFactory):
        """Register endpoint to the tiler factory."""

        assert (
            cog_info is not None
        ), "'rio-cogeo' must be installed to use CogValidateExtension"

        @factory.router.get(
            "/soar/metadata",
            response_model=COGMetadataResponse,
            responses={200: {"description": "Return created COG Metadata file"}},
        )
        def metadata(
            src_path=Depends(factory.path_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
            metadata_path: Annotated[Optional[str], Query(description="Destination path to save the Soar metadata file.")] = None,
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
                ):
            """Read a COG info"""
            info_cogeo = cog_info(src_path)
            with rasterio.Env(**env):
                with factory.reader(src_path, **reader_params) as src_dst:
                    info = src_dst.info()
                    bounds = info.bounds
                    bounds_wkt = f"POLYGON(({bounds.left} {bounds.bottom}, {bounds.left} {bounds.top}, {bounds.right} {bounds.top}, {bounds.right} {bounds.bottom}, {bounds.left} {bounds.bottom}))"
                    tile_url =  F"https://{APP_HOSTNAME}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={src_path}"
                    metadata:  COGMetadata = {
                        "info_cogeo": info_cogeo,
                        "info_tiler": info,
                        "is_valid": info_cogeo.COG,
                        "max_zoom": info.maxzoom,
                        "min_zoom": info.minzoom,
                        "bounds_wkt": bounds_wkt,
                        "tile_url": tile_url,
                        "errors": info_cogeo.COG_errors,
                        "warnings": info_cogeo.COG_warnings
                    }
                    messages = []
                    if(metadata_path is not None):
                        output_file_metadata = f"{metadata_path.strip('/')}/cog_metadata.json"
                        messages.append(save_or_post_data(metadata_path, output_file_metadata, to_json(metadata)))

                    response : COGMetadataResponse = {"messages": messages}
                    if(return_data):
                        response["data"] = metadata
                    else:
                        response["data"] = None
                    return response


        @factory.router.get(
            "/soar/preview",
            responses={200: {"description": "Return created COG Metadata file"}},
        )
        def preview(
            src_path=Depends(factory.path_dependency),
            preview_path: Annotated[Optional[str], Query(description="Destination path to save the preview PNG file.")] = None,
            image_params=Depends(self.img_preview_dependency),
            return_data: Annotated[bool, Query(description="Return metadata as response too")] = False,
        ):
            """Create preview and save into dest_path"""
            content = fetch_preview(src_path, image_params)
            if(preview_path is not None):
                output_file = f"{preview_path.strip('/')}/preview_s{image_params.max_size}.png"
                save_or_post_bytes(preview_path, output_file, content)
            if(return_data):
                return Response(content, media_type="image/png")
            return Response(None, media_type="image/png")
