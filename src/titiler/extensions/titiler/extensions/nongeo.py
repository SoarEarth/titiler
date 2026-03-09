"""NonGeo Tiler Factory.

Serves raster files (including "fake-geo" COGs that carry world-extent
coordinates but are really plain images) using pixel-space tile coordinates
via rio_tiler's ImageReader + LocalTileMatrixSet.

This avoids any CRS reprojection or geographic-bounds look-ups that can
crash when the image has invalid / nonsensical geographic metadata.
"""

import abc
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

import jinja2
import rasterio
from fastapi import APIRouter, Depends, Path, Query
from pydantic import Field
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.params import Depends as DependsFunc
from rio_tiler.io import ImageReader
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Match
from starlette.templating import Jinja2Templates
from typing_extensions import Annotated

from titiler.core.dependencies import (
    BidxExprParams,
    ColorMapParams,
    DatasetPathParams,
    DefaultDependency,
    ImageRenderingParams,
)
from titiler.core.models.mapbox import TileJSON
from titiler.core.resources.enums import ImageType, MediaType
from titiler.core.resources.responses import JSONResponse
from titiler.core.routing import EndpointScope
from titiler.core.utils import render_image

# ---------------------------------------------------------------------------
# Path dependency that resolves local volume paths
# ---------------------------------------------------------------------------

_OSS_PATH = os.environ.get("APP_OSS_PATH", "").rstrip("/")
_NAS_PATH = os.environ.get("APP_NAS_PATH", "").rstrip("/")

_URL_SCHEMES = ("http://", "https://", "s3://", "/vsi", "file://")


def _resolve_local_path(url: str) -> str:
    """Resolve a relative or local path against the mounted volume.

    Paths that already carry a URL scheme (http/https/s3/vsi*) are
    returned unchanged.  Everything else is treated as a path relative
    to APP_OSS_PATH (falling back to APP_NAS_PATH, then as-is).
    """
    if any(url.startswith(s) for s in _URL_SCHEMES):
        return url
    # Already absolute on the filesystem
    if url.startswith("/"):
        return url
    # Relative path — anchor to the configured volume root
    if _OSS_PATH:
        return f"{_OSS_PATH}/{url}"
    if _NAS_PATH:
        return f"{_NAS_PATH}/{url}"
    return url


def NonGeoPathParams(
    url: Annotated[str, Query(description="Dataset URL or volume-relative path")]
) -> str:
    """Resolve the dataset path, expanding volume-relative paths."""
    return _resolve_local_path(url)

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
# Jinja2 templates (the viewer template lives here)
# ---------------------------------------------------------------------------

_jinja2_env = jinja2.Environment(
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    loader=jinja2.ChoiceLoader(
        [jinja2.PackageLoader("titiler.extensions", "templates")]
    ),
)
_DEFAULT_TEMPLATES = Jinja2Templates(env=_jinja2_env)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@dataclass
class NonGeoTilerFactory:
    """Non-Geographic Tiler Factory.

    Registers tile/tilejson/info/viewer endpoints that serve any raster
    file via pixel-space (LocalTileMatrixSet) coordinates.  Uses
    ``rio_tiler.io.ImageReader`` so no CRS transformations are attempted.

    Attributes:
        router_prefix: URL prefix where the router is mounted (used to
            construct absolute tile URLs).
        add_viewer: Whether to register the ``/viewer`` HTML endpoint.
        path_dependency: FastAPI dependency that extracts the dataset path.
        layer_dependency: Dependency for band/expression selection.
        dataset_dependency: Dependency for ImageReader-compatible options.
        colormap_dependency: Dependency for colormap selection.
        render_dependency: Dependency for image rendering options.
        environment_dependency: Dependency to set GDAL environment variables.
        route_dependencies: Extra ``(scopes, deps)`` pairs injected.
        templates: Jinja2 templates used by the viewer.
    """

    router_prefix: str = ""
    add_viewer: bool = True

    path_dependency: Callable[..., Any] = NonGeoPathParams
    layer_dependency: Type[DefaultDependency] = BidxExprParams
    dataset_dependency: Type[DefaultDependency] = NonGeoDatasetParams
    colormap_dependency: Callable[..., Any] = ColorMapParams
    render_dependency: Type[DefaultDependency] = ImageRenderingParams
    environment_dependency: Callable[..., Dict] = field(default=lambda: {})

    route_dependencies: List[Tuple[List[EndpointScope], List[DependsFunc]]] = field(
        default_factory=list
    )

    templates: Jinja2Templates = field(default_factory=lambda: _DEFAULT_TEMPLATES)

    # Populated in __post_init__
    router: APIRouter = field(init=False)

    def __post_init__(self):
        self.router = APIRouter()
        self.register_routes()
        for scopes, dependencies in self.route_dependencies:
            self.add_route_dependencies(scopes=scopes, dependencies=dependencies)

    # ------------------------------------------------------------------
    # Route registration helpers
    # ------------------------------------------------------------------

    def register_routes(self):
        """Register all routes."""
        self.info()
        self.tile()
        self.tilejson()
        if self.add_viewer:
            self.viewer()

    def url_for(self, request: Request, name: str, **path_params: Any) -> str:
        """Return absolute URL for a named endpoint, respecting the prefix."""
        from starlette.routing import compile_path, replace_params

        url_path = self.router.url_path_for(name, **path_params)
        base_url = str(request.base_url)
        if self.router_prefix:
            prefix = self.router_prefix.lstrip("/")
            if "{" in prefix:
                _, path_format, param_convertors = compile_path(prefix)
                prefix, _ = replace_params(
                    path_format, param_convertors, request.path_params.copy()
                )
            base_url += prefix
        return str(url_path.make_absolute_url(base_url=base_url))

    def add_route_dependencies(
        self,
        *,
        scopes: List[EndpointScope],
        dependencies: List[DependsFunc],
    ):
        """Inject extra dependencies into already-registered routes."""
        for route in self.router.routes:
            for scope in scopes:
                match, _ = route.matches({"type": "http", **scope})
                if match != Match.FULL:
                    continue
                for dep in dependencies[::-1]:
                    route.dependant.dependencies.insert(  # type: ignore[attr-defined]
                        0,
                        get_parameterless_sub_dependant(
                            depends=dep,
                            path=route.path_format,  # type: ignore[attr-defined]
                        ),
                    )
                route.dependencies.extend(dependencies)  # type: ignore[attr-defined]

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

    # ------------------------------------------------------------------
    # /tilejson.json
    # ------------------------------------------------------------------

    def tilejson(self):
        """Register GET /tilejson.json endpoint."""

        @self.router.get(
            "/tilejson.json",
            response_model=TileJSON,
            response_model_exclude_none=True,
            responses={200: {"description": "Return a TileJSON document."}},
        )
        def nongeo_tilejson(
            request: Request,
            tile_format: Annotated[
                Optional[ImageType],
                Query(description="Output image type. Default is auto."),
            ] = None,
            tile_scale: Annotated[
                int,
                Query(
                    gt=0,
                    lt=4,
                    description="Tile size scale. 1=256x256, 2=512x512...",
                ),
            ] = 1,
            minzoom: Annotated[
                Optional[int],
                Query(description="Overwrite default minzoom."),
            ] = None,
            maxzoom: Annotated[
                Optional[int],
                Query(description="Overwrite default maxzoom."),
            ] = None,
            src_path=Depends(self.path_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Return TileJSON document with pixel-space bounds and zoom levels."""
            route_params: Dict[str, Any] = {
                "z": "{z}",
                "x": "{x}",
                "y": "{y}",
            }
            if tile_scale and tile_scale > 1:
                route_params["scale"] = tile_scale
            if tile_format:
                route_params["format"] = tile_format.value

            # Choose the right named route depending on params present
            if "scale" in route_params and "format" in route_params:
                tiles_url = self.url_for(
                    request, "nongeo_tile_scale_format", **route_params
                )
            elif "scale" in route_params:
                tiles_url = self.url_for(
                    request, "nongeo_tile_scale", **route_params
                )
            elif "format" in route_params:
                tiles_url = self.url_for(
                    request, "nongeo_tile_format", **route_params
                )
            else:
                tiles_url = self.url_for(request, "nongeo_tile", **route_params)

            qs_key_to_remove = {"tile_format", "tile_scale", "minzoom", "maxzoom"}
            qs = [
                (k, v)
                for k, v in request.query_params._list
                if k.lower() not in qs_key_to_remove
            ]
            if qs:
                tiles_url += f"?{urllib.parse.urlencode(qs)}"

            with rasterio.Env(**env):
                with ImageReader(src_path) as dst:
                    # bounds returned in pixel space: (0, height, width, 0)
                    # Reformat as (left, bottom, right, top) = (0, 0, width, height)
                    px_bounds = [0.0, 0.0, float(dst.dataset.width), float(dst.dataset.height)]
                    return {
                        "bounds": px_bounds,
                        "minzoom": minzoom if minzoom is not None else dst.minzoom,
                        "maxzoom": maxzoom if maxzoom is not None else dst.maxzoom,
                        "tiles": [tiles_url],
                        "attribution": os.environ.get("TITILER_DEFAULT_ATTRIBUTION"),
                    }

    # ------------------------------------------------------------------
    # /viewer
    # ------------------------------------------------------------------

    def viewer(self):
        """Register GET /viewer endpoint (HTML)."""

        @self.router.get(
            "/viewer",
            response_class=HTMLResponse,
        )
        def nongeo_viewer(
            request: Request,
            src_path=Depends(self.path_dependency),
            env=Depends(self.environment_dependency),
        ):
            """Return a simple pixel-space viewer for a non-geo raster."""
            tilejson_url = self.url_for(request, "nongeo_tilejson")
            qs = request.query_params._list
            if qs:
                tilejson_url += f"?{urllib.parse.urlencode(qs)}"

            return self.templates.TemplateResponse(
                name="nongeo_viewer.html",
                context={
                    "request": request,
                    "tilejson_endpoint": tilejson_url,
                },
                media_type=MediaType.html.value,
            )
