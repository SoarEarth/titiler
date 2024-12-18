"""titiler.extensions"""

__version__ = "0.18.3"

from .cogeo import cogValidateExtension  # noqa
from .stac import stacExtension  # noqa
from .viewer import cogViewerExtension, stacViewerExtension  # noqa
from .wms import wmsExtension  # noqa
from .soar_mosaic import soarMosaicExtension  # noqa
from .soar_cog import soarCogExtension  # noqa