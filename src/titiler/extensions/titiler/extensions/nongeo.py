"""NonGeo Tiler Factory.

Serves raster files (including "fake-geo" COGs that carry world-extent
coordinates but are really plain images) using pixel-space tile coordinates
via rio_tiler's ImageReader + LocalTileMatrixSet.

This avoids any CRS reprojection or geographic-bounds look-ups that can
crash when the image has invalid / nonsensical geographic metadata.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Type

import rasterio
from fastapi import APIRouter, Depends, Path, Query
from pydantic import Field
from rio_tiler.io import ImageReader
from starlette.responses import Response
from typing_extensions import Annotated

from titiler.core.dependencies import (
    BidxExprParams,
    ColorMapParams,
    DatasetPathParams,
    DefaultDependency,
    ImageRenderingParams,
)
from titiler.core.resources.enums import ImageType
from titiler.core.resources.responses import JSONResponse
from titiler.core.utils import render_image
from .soar_util import APP_OSS_PATH, APP_NAS_PATH, encode_url_path_segments

# ---------------------------------------------------------------------------
# Minimal dataset-params for ImageReader (no reproject_method / nodata)
# ---------------------------------------------------------------------------

@dataclass
class NonGeoDatasetParams(DefaultDependency):
    """Dataset parameters accepted by ImageReader.tile / .part."""

    unscale: Annotated[
        Optional[bool],
        Query(
            title="Apply internal Scale/Offset",
            description="Apply 'scales' and 'offsets' on output data values.",
        ),
    ] = None

    resampling_method: Annotated[
        Optional[str],
        Query(
            alias="resampling",
            description=(
                "Resampling algorithm. "
                "Defaults to `nearest`."
            ),
        ),
    ] = None


# ---------------------------------------------------------------------------
# Response params for image endpoints
# ---------------------------------------------------------------------------

img_endpoint_params: Dict[str, Any] = {
    "responses": {
        200: {
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/jpg": {},
                "image/webp": {},
                "image/jp2": {},
                "image/tiff; application=geotiff": {},
                "application/x-binary": {},
            },
            "description": "Return an image.",
        }
    },
    "response_class": Response,
}

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@dataclass
class NonGeoTilerFactory:
    """Non-Geographic Tiler Factory.

    Registers tile/info endpoints that serve any raster file via
    pixel-space (LocalTileMatrixSet) coordinates.  Uses
    ``rio_tiler.io.ImageReader`` so no CRS transformations are attempted.

    Attributes:
        router_prefix: URL prefix where the router is mounted.
        path_dependency: FastAPI dependency that extracts the dataset path.
        layer_dependency: Dependency for band/expression selection.
        dataset_dependency: Dependency for ImageReader-compatible options.
        colormap_dependency: Dependency for colormap selection.
        render_dependency: Dependency for image rendering options.
        environment_dependency: Dependency to set GDAL environment variables.
    """

    router_prefix: str = ""

    path_dependency: Callable[..., Any] = DatasetPathParams
    layer_dependency: Type[DefaultDependency] = BidxExprParams
    dataset_dependency: Type[DefaultDependency] = NonGeoDatasetParams
    colormap_dependency: Callable[..., Any] = ColorMapParams
    render_dependency: Type[DefaultDependency] = ImageRenderingParams
    environment_dependency: Callable[..., Dict] = field(default=lambda: {})

    # Populated in __post_init__
    router: APIRouter = field(init=False)

    def __post_init__(self):
        self.router = APIRouter()
        self.info()
        self.tile()

    # ------------------------------------------------------------------
    # /info
    # ------------------------------------------------------------------

    def info(self):
        """Register GET /info endpoint."""

        @self.router.get(
            "/info",
            response_class=JSONResponse,
            responses={200: {"description": "Return dataset's basic info."}},
        )
        def nongeo_info(
            src_path=Depends(self.path_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Return pixel-space info for a non-geo dataset."""
            src_path = encode_url_path_segments(src_path)
            with rasterio.Env(**env):
                with ImageReader(src_path) as dst:
                    return {
                        "width": dst.dataset.width,
                        "height": dst.dataset.height,
                        "minzoom": dst.minzoom,
                        "maxzoom": dst.maxzoom,
                        "bounds": list(dst.bounds),
                        "band_descriptions": dst.dataset.descriptions,
                        "nodata_type": "None",
                    }

    # ------------------------------------------------------------------
    # /tiles
    # ------------------------------------------------------------------

    def tile(self):
        """Register GET /tiles/{z}/{x}/{y}[.format] endpoints."""

        @self.router.get(
            "/tiles/{z}/{x}/{y}",
            name="nongeo_tile",
            **img_endpoint_params,
        )
        @self.router.get(
            "/tiles/{z}/{x}/{y}.{format}",
            name="nongeo_tile_format",
            **img_endpoint_params,
        )
        @self.router.get(
            "/tiles/{z}/{x}/{y}@{scale}x",
            name="nongeo_tile_scale",
            **img_endpoint_params,
        )
        @self.router.get(
            "/tiles/{z}/{x}/{y}@{scale}x.{format}",
            name="nongeo_tile_scale_format",
            **img_endpoint_params,
        )
        def nongeo_tile(
            z: Annotated[int, Path(description="Zoom level (pixel space).")],
            x: Annotated[int, Path(description="Tile column (pixel space).")],
            y: Annotated[int, Path(description="Tile row (pixel space).")],
            scale: Annotated[
                int,
                Field(
                    gt=0,
                    le=4,
                    description="Tile size scale. 1=256x256, 2=512x512...",
                ),
            ] = 1,
            format: Annotated[
                Optional[ImageType],
                Field(
                    description="Output image format. Defaults to auto-detect.",
                ),
            ] = None,
            src_path=Depends(self.path_dependency),
            layer_params=Depends(self.layer_dependency),
            dataset_params=Depends(self.dataset_dependency),
            colormap=Depends(self.colormap_dependency),
            render_params=Depends(self.render_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Serve a pixel-space tile from any raster (non-geo mode)."""
            src_path = encode_url_path_segments(src_path)
            tilesize = scale * 256
            with rasterio.Env(**env):
                with ImageReader(src_path) as dst:
                    image = dst.tile(
                        x,
                        y,
                        z,
                        tilesize=tilesize,
                        **layer_params.as_dict(),
                        **dataset_params.as_dict(),
                    )
                    dst_colormap = getattr(dst, "colormap", None)

            content, media_type = render_image(
                image,
                output_format=format,
                colormap=colormap or dst_colormap,
                **render_params.as_dict(),
            )
            return Response(content, media_type=media_type)
