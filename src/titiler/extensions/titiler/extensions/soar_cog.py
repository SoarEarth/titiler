"""rio-cogeo Extension."""

from dataclasses import dataclass
from typing import List, Optional, Type
from .soar_util import APP_HOSTNAME, APP_OSS_PATH, bbox_to_tiles, exists_in_cache, fetch_tile_and_forward_to_cf_cog, save_or_post_data, to_json, fetch_preview, save_or_post_bytes, encode_url_path_segments, prepare_cog_translation

from typing_extensions import TypedDict
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
import logging
import time

from pathlib import Path

from fastapi import Depends, Query, Response
from titiler.extensions.soar_models import COGMetadata
from typing_extensions import Annotated

from titiler.core.factory import BaseFactory, FactoryExtension, TilerFactory
from titiler.core.dependencies import DefaultDependency, PreviewParams
import os
import shutil
import morecantile

try:
    from rio_cogeo.cogeo import cog_info, cog_translate
    from rio_cogeo.profiles import cog_profiles

except ImportError:  # pragma: nocover
    cog_info = None  # type: ignore
    cog_translate = None  # type: ignore
    cog_profiles = None  # type: ignore

logger = logging.getLogger('uvicorn.error')

class COGMetadataResponse(TypedDict):
    messages: List[str]
    data: Optional[COGMetadata]

@dataclass
class soarCogExtension(FactoryExtension):
    """Add /soar endpoints to a COG TilerFactory."""

    backend_dependency: Type[DefaultDependency] = DefaultDependency
    img_preview_dependency: Type[DefaultDependency] = PreviewParams


    def register(self, factory: TilerFactory):
        """Register endpoint to the tiler factory."""

        assert (
            cog_info is not None
        ), "'rio-cogeo' must be installed to use CogValidateExtension"

        @factory.router.get(
            "/soar/metadata",
            # response_model=COGMetadataResponse,
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
            try:
                src_path_encoded = encode_url_path_segments(src_path)
                info_cogeo = cog_info(src_path_encoded)
                bbox = info_cogeo.GEO.BoundingBox #  Tuple[float, float, float, float]

                # Transform bbox to EPSG:4326 if needed
                from rasterio.warp import transform_bounds
                src_crs = info_cogeo.GEO.CRS
                bbox_4326 = transform_bounds(src_crs, "EPSG:4326", *bbox)
                bounds_wkt = f"POLYGON(({bbox_4326[0]} {bbox_4326[1]}, {bbox_4326[0]} {bbox_4326[3]}, {bbox_4326[2]} {bbox_4326[3]}, {bbox_4326[2]} {bbox_4326[1]}, {bbox_4326[0]} {bbox_4326[1]}))"
                tile_url =  F"https://{APP_HOSTNAME}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={encode_url_path_segments(src_path_encoded)}"
                metadata: COGMetadata = {
                    "info_cogeo": info_cogeo,
                    "is_valid": info_cogeo.COG,
                    "max_zoom": info_cogeo.GEO.MaxZoom,
                    "min_zoom": info_cogeo.GEO.MinZoom,
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

            except Exception as err:
                error_message = f"Failed to read COG metadata. {str(err)}"
                metadata : COGMetadata = {
                    "is_valid": False,
                    "errors": [error_message]
                }
                response : COGMetadataResponse = {"messages": [error_message]}
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
            logger.info( f"Generating preview: src_path: {src_path}" )
            content = fetch_preview(src_path, image_params)
            if(preview_path is not None):
                output_file = f"{preview_path.strip('/')}/preview_s{image_params.max_size}.png"
                save_or_post_bytes(preview_path, output_file, content)
            if(return_data):
                return Response(content, media_type="image/png")
            return Response(None, media_type="image/png")

        @factory.router.get(
            "/soar/generateTilesIntoCache", 
            responses={200: {"description": "Return created MosaicJSON"}},
        )
        def generate_tiles_into_cache_by_zoom(
            cache_key: Annotated[str, Query(description="Cache key")],
            zoom: Annotated[int, Query(description="Zoom level")],
            src_path=Depends(factory.path_dependency),
            reader_params=Depends(factory.reader_dependency),
            env=Depends(factory.environment_dependency),
            offset: Annotated[int, Query(description="Offset")] = -1,
            limit: Annotated[int, Query(description="Limit")] = -1,
        ):
            """Read a COG and pre-tile requested zoom level"""
            with rasterio.Env(**env):
                with factory.reader(src_path, **reader_params.as_dict()) as src_dst:
                    info = src_dst.info()
                    tiles = bbox_to_tiles(info.bounds, zoom)
                    if(offset > 0):
                        tiles = tiles[offset:]
                    if(limit > 0):
                        tiles = tiles[:limit]
                    generate_tiles(tiles, cache_key, src_path)
                    return F"Total of {len(tiles)} tiles were send to CF cache."

        @factory.router.get(
            "/soar/cog_translate",
            responses={200: {"description": "Return message"}},
        )
        def translate(
            src_path: Annotated[Optional[str], Query(description="Source of the main file to translate")] = None,
            dest_path: Annotated[Optional[str], Query(description="Destination path to save the COG file.")] = None,
            cog_profile: Annotated[Optional[str], Query(description="COG profile to use.")] = "webp",
            scale: Annotated[Optional[float], Query(description="Scale factor for downsampling (e.g. 0.5 for 50%).")] = None,
            use_nas: Annotated[bool, Query(description="Use NAS for temp files.")] = False,
        ):
            """Create COG and save into dest_path"""
            logger.info( f"Translating to COG: src_path: {src_path}, dest_path: {dest_path}, profile: {cog_profile}, scale: {scale}, use_nas: {use_nas}" )

            # Copy source and dest paths to local temp files
            input_file_tmp, dest_file_tmp, dest_file_path = prepare_cog_translation(src_path, dest_path, use_nas)

            # print absolute paths for debugging
            logger.info( f"Input file local path: {input_file_tmp.absolute()}" )
            logger.info( f"Destination file local path: {dest_file_tmp.absolute()}" )

            # Perform COG translation with optional scaling
            cog_profile = cog_profiles.get(cog_profile)
            tms = morecantile.tms.get("WebMercatorQuad")
            if scale and scale < 1.0:
                with rasterio.open(input_file_tmp) as src:
                    dst_height = int(src.height * scale)
                    dst_width = int(src.width * scale)
                    dst_transform = src.transform * src.transform.scale(
                        (src.width / dst_width),
                        (src.height / dst_height)
                    )
                    
                    with WarpedVRT(
                        src,
                        width=dst_width,
                        height=dst_height,
                        transform=dst_transform,
                        resampling=Resampling.bilinear
                    ) as vrt:
                        cog_translate(
                            vrt,
                            dest_file_tmp,
                            cog_profile,
                            use_cog_driver=True,
                            tms=tms
                        )
            else:
                # Convert to COG with the selected profile
                cog_translate(
                    input_file_tmp,
                    dest_file_tmp,
                    cog_profile,
                    use_cog_driver=True,
                    tms=tms
                )

            # Move the temp dest file to the final destination atomically
            dest_file = Path(dest_file_path)
            dest_file.parent.mkdir(exist_ok=True, parents=True)
            shutil.move(dest_file_tmp, dest_file)

            # Clean up temp files
            try:
                if dest_file_tmp.exists(): dest_file_tmp.unlink()
                if input_file_tmp.exists(): input_file_tmp.unlink()
            except Exception as e:
                print(f"Warning: Failed to clean up temp files: {e}")
            
            return Response('ok', media_type="text")


def generate_tiles(tiles, cache_key, src_path):
    total = len(tiles)
    skip_cache_check = False
    for idx, tile in enumerate(tiles):
        z, x, y = tile
        start_time = time.time()  # Record the start time
        if(skip_cache_check == True or exists_in_cache(cache_key, z, x, y) == False):
            fetch_tile_and_forward_to_cf_cog(cache_key, src_path, z, x, y)
            skip_cache_check = True
        end_time = time.time()  # Record the end time
        elapsed_time = end_time - start_time  # Calculate elapsed time
        logger.info(f"Processed {idx} of {total}. [{cache_key},{z},{x},{y}] took {elapsed_time:.2f} seconds")
