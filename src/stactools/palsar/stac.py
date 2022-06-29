import logging
import os
import re
from typing import Dict

import rasterio  # type: ignore
from dateutil.parser import isoparse
from pystac import (Asset, CatalogType, Collection, Extent, Item, Link,
                    MediaType, SpatialExtent, Summaries, TemporalExtent)
from pystac.extensions.item_assets import ItemAssetsExtension
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterBand, RasterExtension
from pystac.extensions.sar import SarExtension, ObservationDirection, Polarization
from pystac.extensions.sat import SatExtension, OrbitState
from pystac.extensions.version import VersionExtension
from shapely.geometry import box, mapping  # type: ignore

from stactools.palsar import constants as co

logger = logging.getLogger(__name__)

CLASSIFICATION_EXTENSION_URI = "https://stac-extensions.github.io/classification/v1.0.0/schema.json"


FILENAME_PARTS = re.compile(
    r"(?P<MODE>[F|U])"
    r"(?P<BEAM_NUMBER>\w{2})"
    r"(?P<POLARIZATIONS>[D|Q])"
    r"(?P<ORBIT>[A|D])"
    r"(?P<OBSERVATION>[R|L])"
)


def create_collection(product: str) -> Collection:
    """Create a STAC Collection

    This function includes logic to extract all relevant metadata from
    an asset describing the STAC collection and/or metadata coded into an
    accompanying constants.py file.

    See `Collection<https://pystac.readthedocs.io/en/latest/api.html#collection>`_.

    Args:
        Product (str): MOS for mosiac, FNF for Forest/Non-Forest

    Returns:
        Item: STAC Item object

    Returns:
        Collection: STAC Collection object
    """
    providers = co.ALOS_PALSAR_PROVIDERS

    summaries = {
        "platform": co.ALOS_PALSAR_PLATFORMS,
        "instruments": co.ALOS_PALSAR_INSTRUMENTS,
    }

    if product == 'FNF':
        id = "alos-fnf-mosaic"
        title = "ALOS Forest/Non-Forest Annual Mosaic"
        description = co.ALOS_FNF_DESCRIPTION
        keywords = ['ALOS', 'JAXA', 'Forest', 'Land Cover', 'Global']
        extent = Extent(SpatialExtent(co.ALOS_SPATIAL_EXTENT),
                        TemporalExtent([co.ALOS_FNF_TEMPORAL_EXTENT]))
    else:
        id = "alos-palsar-mosaic"
        title = "ALOS PALSAR Annual Mosaic"
        description = co.ALOS_MOS_DESCRIPTION
        keywords = ['ALOS', 'JAXA', 'Remote Sensing', 'Global']
        extent = Extent(SpatialExtent(co.ALOS_SPATIAL_EXTENT),
                        TemporalExtent([co.ALOS_MOS_TEMPORAL_EXTENT]))
        summaries = {
            **summaries,
            **{
                "sar:observation_direction": [ObservationDirection.LEFT.value, ObservationDirection.RIGHT.value],
                "sar:instrument_mode": ["F", "U"],
                "sar:polarizations": [
                    co.ALOS_DUAL_POLARIZATIONS,
                    co.ALOS_QUAD_POLARIZATIONS,
                ],
                "sat:orbit_state": [OrbitState.ASCENDING.value, OrbitState.DESCENDING.value],
                "palsar:number_of_polarizations": ["D", "Q"],
            }
        }

    collection = Collection(
        id=id,
        title=title,
        description=description,
        license="proprietary",
        providers=providers,
        extent=extent,
        keywords=keywords,
        catalog_type=CatalogType.RELATIVE_PUBLISHED,
        summaries=Summaries(summaries),
        stac_extensions=[
            ItemAssetsExtension.get_schema_uri(),
            SarExtension.get_schema_uri(),
            SatExtension.get_schema_uri(),
            ProjectionExtension.get_schema_uri(),
            RasterExtension.get_schema_uri(),
            VersionExtension.get_schema_uri(),
            CLASSIFICATION_EXTENSION_URI,
        ],
    )

    version = VersionExtension.ext(collection, add_if_missing=True)

    assets = ItemAssetsExtension.ext(collection, add_if_missing=True)
    if product == 'FNF':
        assets.item_assets = co.ALOS_FNF_ASSETS
        version.version = co.ALOS_FNF_REVISION
        collection.add_links(co.ALOS_FNF_LINKS)
    else:
        assets.item_assets = co.ALOS_MOS_ASSETS
        version.version = co.ALOS_MOS_REVISION
        collection.add_links(co.ALOS_MOS_LINKS)

    return collection


def create_item_from_href(asset_href: str, read_href_modifier=None):
    """
    Create a STAC item from a single asset's HREF.

    `asset_href` should be either the "C.tif" asset for the FNF mosaic,
    or the XML metadata file.

    """
    filename = os.path.basename(asset_href)

    if filename.endswith("_C.tif"):
        assets_hrefs = {"C": asset_href}
    elif filename.endswith(".xml"):
        prefix = asset_href.rsplit("/", 1)[0]  # like https://.../year/group/
        filename_prefix, filename_suffix = filename.rsplit("_", 1)  # like <tile>_<year>
        filename_suffix = os.path.splitext(filename_suffix)[0]  # like F02DAR
        kinds = ["date", "linci", "mask", "sl_HH", "sl_HV"]
        if filename_suffix[3] == "Q":
            # "quad" mode
            kinds.extend(["sl_VH", "sl_VV"])

        assets_hrefs = {
            kind.split("_")[-1]: f"{prefix}/{filename_prefix}_{kind}_{filename_suffix}.tif"
            for kind in kinds
        }
        assets_hrefs["metadata"] = asset_href
    else:
        raise ValueError("Unknown type")

    return create_item(assets_hrefs, read_href_modifier=read_href_modifier)


def create_item(assets_hrefs: Dict, root_href: str = '', read_href_modifier=None) -> Item:
    """Create a STAC Item

    This function should include logic to extract all relevant metadata from an
    asset, metadata asset, and/or a constants.py file.

    See `Item<https://pystac.readthedocs.io/en/latest/api.html#item>`_.

    Args:
        assets_hrefs (dict): The HREF pointing to an asset associated with the item

    Returns:
        Item: STAC Item object
    """
    if read_href_modifier is None:

        def read_href_modifier(x):
            return x

    # Get the general parameters from the first asset
    asset_href = list(assets_hrefs.values())[0]
    filename = os.path.basename(os.path.splitext(asset_href)[0])
    year = os.path.basename(asset_href).split("_")[1]
    is_fnf = filename.split("_")[2] == "C"

    if is_fnf:
        item_root = '_'.join((os.path.basename(asset_href)).split("_")[0:2])
    else:
        *a, _, b = filename.split("_")
        item_root = "_".join(a + [b])

    with rasterio.open(read_href_modifier(asset_href)) as dataset:
        if dataset.crs.to_epsg() != 4326:
            raise ValueError(
                f"Dataset {asset_href} is not EPSG:4326, which is required for ALOS data"
            )
        bbox = list(dataset.bounds)
        geometry = mapping(box(*bbox))
        transform = list(dataset.transform)
        shape = dataset.shape

    start_datetime = f"20{year}-01-01T00:00:00Z"
    end_datetime = f"20{year}-12-31T23:59:59Z"

    if filename.split("_")[2] == "C":
        item_id = f"{item_root}_FNF"
        properties = {
            "title": item_id,
            "description": "Forest/Non-Forest Classification",
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
        }
        collection = 'alos-fnf-mosaic'
    else:
        item_id = f"{item_root}_MOS"
        properties = {
            "title": item_id,
            "description": "Annual PALSAR Mosaic",
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
        }
        collection = 'alos-palsar-mosaic'

    item = Item(
        id=item_id,
        geometry=geometry,
        bbox=bbox,
        datetime=isoparse(start_datetime),
        properties=properties,
        stac_extensions=[],
    )

    item.collection_id = collection
    item.links.append(
        Link(rel="collection",
             target=os.path.join(root_href, f"{collection}.json")))

    # Data before 2015 is PALSAR, after PALSAR-2
    if int(year) >= 15:
        item.common_metadata.platform = co.ALOS_PALSAR_PLATFORMS[1]
        item.common_metadata.instruments = [co.ALOS_PALSAR_INSTRUMENTS[1]]
    else:
        item.common_metadata.platform = co.ALOS_PALSAR_PLATFORMS[0]
        item.common_metadata.instruments = [co.ALOS_PALSAR_INSTRUMENTS[0]]
    item.common_metadata.gsd = co.ALOS_PALSAR_GSD

    # It is a good idea to include proj attributes to optimize for libs like stac-vrt
    proj_attrs = ProjectionExtension.ext(item, add_if_missing=True)
    proj_attrs.epsg = co.ALOS_PALSAR_EPSG
    proj_attrs.bbox = bbox
    proj_attrs.shape = shape  # Raster shape
    proj_attrs.transform = transform  # Raster GeoTransform

    if collection == "alos-palsar-mosaic":
        # For MOS product use SAR extension
        sar = SarExtension.ext(item, add_if_missing=True)
        sat = SatExtension.ext(item, add_if_missing=True)
        
        palsar_parts = FILENAME_PARTS.match(filename.split("_")[-1])
        if not palsar_parts:
            raise ValueError(
                f"Asset filename {filename} from href {asset_href} doesn't match the expected pattern."
            )
        m = palsar_parts.groupdict()

        sar.instrument_mode = m["MODE"]
        if m["OBSERVATION"] == "L":
            sar.observation_direction = ObservationDirection.LEFT
        else:
            sar.observation_direction = ObservationDirection.RIGHT

        sar.frequency_band = co.ALOS_FREQUENCY_BAND
        if m["POLARIZATIONS"] == "D":
            sar.polarizations = co.ALOS_DUAL_POLARIZATIONS
        else:
            sar.polarizations = co.ALOS_QUAD_POLARIZATIONS

        sar.product_type = co.ALOS_PRODUCT_TYPE
        # Append Correction Factor to convert DN to dB
        item.properties["cf"] = co.ALOS_PALSAR_CF

        if m["ORBIT"] == "A":
            sat.orbit_state = OrbitState.ASCENDING
        else:
            sat.orbit_state = OrbitState.DESCENDING

        item.properties["palsar:beam_number"] = m["BEAM_NUMBER"]
        item.properties["palsar:number_of_polarizations"] = m["POLARIZATIONS"]

    # Add an asset to the item (COG for example)
    # For assets in item loop over
    # ["date","xml","linci", "mask", "HH", "HV"]
    for key, value in assets_hrefs.items():
        if root_href:
            href = os.path.join(root_href, os.path.basename(value))
        else:
            href = value

        if href.endswith(".xml"):
            media_type = MediaType.XML
            roles = ["metadata"]
        else:
            media_type = MediaType.COG
            roles = ["data"]

        if is_fnf:
            title = "FNF"
        else:
            title = key

        item.add_asset(
            key,
            Asset(
                # TODO: add relative or absolute url
                href=href,
                media_type=media_type,
                roles=roles,
                title=title,
            ),
        )

        if item.assets[key].media_type == MediaType.COG:
            cog_asset = item.assets[key]
            raster = RasterExtension.ext(cog_asset, add_if_missing=True)
            raster_band = co.ALOS_BANDS.get(key)
            if raster_band:
                if int(year) >= 17:
                    # NoData value changed in 2019 from 0 to 1 for some
                    # Revision M 2017+ now matches
                    nodata_by_band = {
                        "HH": 1,
                        "HV": 1,
                        "mask": 0,
                        "linci": 1,
                        "date": 1,
                        "C": 0
                    }
                    nodata = nodata_by_band.get(key, 0)
                else:
                    nodata = 0
                raster.bands = [
                    RasterBand.create(nodata=nodata,
                                      data_type=raster_band.get('data_type'))
                ]

            if key == "C":
                cog_asset.extra_fields["classification:classes"] = co.ALOS_FNF_CLASSIFICATION_CLASSES
            elif key== "mask":
                cog_asset.extra_fields["classification:classes"] = co.ALOS_MASK_CLASSIFICATION_CLASSES

    return item
