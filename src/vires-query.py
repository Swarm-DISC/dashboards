import asyncio
import datetime as dt
import re
import traceback
from typing import Dict, List, Optional, Tuple

import holoviews as hv
from holoviews import opts
import hvplot.xarray  # noqa: F401
import panel as pn
import param
from viresclient import SwarmRequest
try:
    from viresclient._client import NRECORDS_LIMIT as _NRECORDS_LIMIT, MAX_CHUNK_DURATION as _MAX_CHUNK_DURATION
except ImportError:
    _NRECORDS_LIMIT = 4320000  # 50 days at 1Hz
    _MAX_CHUNK_DURATION = dt.timedelta(days=25 * 365)
try:
    from viresclient._client_swarm import COLLECTION_REFERENCES as _COLLECTION_REFERENCES
except ImportError:
    _COLLECTION_REFERENCES = {}

pn.extension("codeeditor")
if pn.state.curdoc is not None:
    pn.state.curdoc.title = "VirES Query Builder"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VOBS_MAGNETIC_TAB = 1   # index of the GVO/VOBS tab within the Magnetic card sub-tabs
_GROUND_MAGNETIC_TAB = 2  # index of the Ground tab within the Magnetic card sub-tabs

_SECTION_STYLES = {
    "collection": {
        "border": "1px solid #c7d2fe",
        "background": "#eef2ff",
        "border-radius": "8px",
        "padding": "12px",
    },
    "parameters": {
        "border": "1px solid #a7f3d0",
        "background": "#ecfdf3",
        "border-radius": "8px",
        "padding": "12px",
    },
    "code": {
        "border": "1px solid #fde68a",
        "background": "#fffbeb",
        "border-radius": "8px",
        "padding": "12px",
    },
    "time": {
        "border": "1px solid #ddd6fe",
        "background": "#f5f3ff",
        "border-radius": "8px",
        "padding": "12px",
    },
    "preview": {
        "border": "1px solid #fbcfe8",
        "background": "#fdf2f8",
        "border-radius": "8px",
        "padding": "12px",
    },
}
_EXCLUDED_AUXILIARIES = {"Timestamp", "Latitude", "Longitude", "Radius", "Spacecraft"}

# ---------------------------------------------------------------------------
# Request templates
# ---------------------------------------------------------------------------

REQUEST_TEMPLATE = """import datetime as dt
from viresclient import SwarmRequest

request = SwarmRequest()
request.set_collection('{collection}', verbose=False)
request.set_products(
    measurements={measurements},{models_line}{auxiliaries_line}
)
data = request.get_between(
    start_time={time_range[0]!r},
    end_time={time_range[1]!r},
    asynchronous=False,  # NB: For longer requests, change to True
    show_progress=False,
)
ds = data.as_xarray({as_xarray_args})"""

# ---------------------------------------------------------------------------
# Collection data
# ---------------------------------------------------------------------------

MAG_COLLECTIONS = {
    "Swarm": {
        "Swarm-A": {
            "OPER LR (1Hz)": "SW_OPER_MAGA_LR_1B",
            "OPER HR (50Hz)": "SW_OPER_MAGA_HR_1B",
            "FAST LR (1Hz)": "SW_FAST_MAGA_LR_1B",
            "FAST HR (50Hz)": "SW_FAST_MAGA_HR_1B",
        },
        "Swarm-B": {
            "OPER LR (1Hz)": "SW_OPER_MAGB_LR_1B",
            "OPER HR (50Hz)": "SW_OPER_MAGB_HR_1B",
            "FAST LR (1Hz)": "SW_FAST_MAGB_LR_1B",
            "FAST HR (50Hz)": "SW_FAST_MAGB_HR_1B",
        },
        "Swarm-C": {
            "OPER LR (1Hz)": "SW_OPER_MAGC_LR_1B",
            "OPER HR (50Hz)": "SW_OPER_MAGC_HR_1B",
            "FAST LR (1Hz)": "SW_FAST_MAGC_LR_1B",
            "FAST HR (50Hz)": "SW_FAST_MAGC_HR_1B",
        },
    },
    "Cryosat-2": {
        "Cryosat-2": {"OPER": "CS_OPER_MAG"},
    },
    "GRACE": {
        "GRACE-A": {"OPER": "GRACE_A_MAG"},
        "GRACE-B": {"OPER": "GRACE_B_MAG"},
    },
    "GRACE-FO": {
        "GRACE-FO-1": {"OPER": "GF1_OPER_FGM_ACAL_CORR"},
        "GRACE-FO-2": {"OPER": "GF2_OPER_FGM_ACAL_CORR"},
    },
    "GOCE": {
        "GOCE": {"OPER": "GO_MAG_ACAL_CORR", "ML": "GO_MAG_ACAL_CORR_ML"},
    },
}

VOBS_COLLECTIONS = {
    "Swarm (1-monthly)": "SW_OPER_VOBS_1M_2_",
    "Swarm (4-monthly)": "SW_OPER_VOBS_4M_2_",
    "Ørsted (1-monthly)": "OR_OPER_VOBS_1M_2_",
    "CHAMP (1-monthly)": "CH_OPER_VOBS_1M_2_",
    "Cryosat-2 (1-monthly)": "CR_OPER_VOBS_1M_2_",
    "Composite (1-monthly)": "CO_OPER_VOBS_1M_2_",
}

VOBS_SV_COLLECTIONS = {
    label: f"{col_id}:SecularVariation"
    for label, col_id in VOBS_COLLECTIONS.items()
}

AUX_OBS_COLLECTIONS = {
    "AUX_OBSH (hourly)": "SW_OPER_AUX_OBSH2_",
    "AUX_OBSM (minute)": "SW_OPER_AUX_OBSM2_",
    "AUX_OBSS (second)": "SW_OPER_AUX_OBSS2_",
}


def _load_metadata() -> tuple[Dict[str, List[str]], Dict[str, List[str]], List[str], List[str], Dict[str, str], Dict[str, str]]:
    vires = SwarmRequest()
    collection_map = vires.available_collections(details=False)
    measurements_by_collection = {
        collection_type: vires.available_measurements(collection_type)
        for collection_type in collection_map.keys()
    }
    auxiliaries = [
        aux
        for aux in vires.available_auxiliaries()
        if aux not in _EXCLUDED_AUXILIARIES
    ]
    mag_models = vires.available_models(details=False)
    collections_to_types = vires._available["collections_to_keys"]
    collection_sampling_steps = SwarmRequest.COLLECTION_SAMPLING_STEPS
    return collection_map, measurements_by_collection, auxiliaries, mag_models, collections_to_types, collection_sampling_steps


COLLECTION_MAP, MEASUREMENTS_BY_COLLECTION, AUXILIARIES, MAG_MODELS, COLLECTIONS_TO_TYPES, COLLECTION_SAMPLING_STEPS = _load_metadata()

# Derive VirES collection-type keys for all magnetic space collections at startup.
# COLLECTIONS_TO_TYPES maps collection IDs → type keys.
_MAG_COLLECTION_TYPES: frozenset = frozenset(filter(None, (
    COLLECTIONS_TO_TYPES.get(col_id)
    for mission in MAG_COLLECTIONS.values()
    for spacecraft in mission.values()
    for col_id in spacecraft.values()
)))
_MAG_HR_COLLECTION_TYPES: frozenset = frozenset(filter(None, (
    COLLECTIONS_TO_TYPES.get(col_id)
    for mission in MAG_COLLECTIONS.values()
    for spacecraft in mission.values()
    for variant, col_id in spacecraft.items()
    if "HR" in variant
)))

# Fallback singleton for VOBS (used as default when type lookup fails).
_VOBS_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get(
    next(iter(VOBS_COLLECTIONS.values())), "VOBS"
)
_VOBS_COLLECTION_TYPES: frozenset = frozenset(filter(None, (
    COLLECTIONS_TO_TYPES.get(col_id) for col_id in VOBS_COLLECTIONS.values()
)))
_VOBS_SV_COLLECTION_TYPES: frozenset = frozenset(filter(None, (
    COLLECTIONS_TO_TYPES.get(col_id) for col_id in VOBS_SV_COLLECTIONS.values()
)))
_ALL_VOBS_COLLECTION_TYPES: frozenset = _VOBS_COLLECTION_TYPES | _VOBS_SV_COLLECTION_TYPES
_GROUND_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get(
    next(iter(AUX_OBS_COLLECTIONS.values())), "AUX_OBS_2_"
)
_AUX_OBSH_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get("SW_OPER_AUX_OBSH2_", "")
_AUX_OBSM_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get("SW_OPER_AUX_OBSM2_", "")
_AUX_OBSS_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get("SW_OPER_AUX_OBSS2_", "")

# All collection IDs accessible via the Magnetic card (Space + GVO/VOBS + Ground sub-tabs).
_MAGNETIC_COLLECTION_IDS: frozenset = frozenset(
    col_id
    for mission_data in MAG_COLLECTIONS.values()
    for spacecraft_data in mission_data.values()
    for col_id in spacecraft_data.values()
) | frozenset(VOBS_COLLECTIONS.values()) | frozenset(VOBS_SV_COLLECTIONS.values()) | frozenset(AUX_OBS_COLLECTIONS.values())


# Handbook/reference URLs keyed by collection ID, derived from viresclient's
# COLLECTION_REFERENCES (which is keyed by collection type) via COLLECTIONS_TO_TYPES.
_HANDBOOK_URLS: Dict[str, str] = {
    col_id: _COLLECTION_REFERENCES[col_type][0].strip()
    for col_id, col_type in COLLECTIONS_TO_TYPES.items()
    if col_type in _COLLECTION_REFERENCES and _COLLECTION_REFERENCES[col_type]
}

_NB_BASE = "https://notebooks.vires.services/notebooks/"
# Notebook slug keyed by VirES collection type.  Used to build _NOTEBOOK_URLS below.
_NOTEBOOK_BY_TYPE: Dict[str, str] = {
    # Magnetometer
    "MAG":          "03a1_demo-magx_lr_1b",
    "MAG_HR":       "03a2_demo-magx_hr_1b",
    "MAG_CHAMP":    "03y1_multi-mission-intro",
    "MAG_CS":       "03y1_multi-mission-intro",
    "MAG_GRACE":    "03y1_multi-mission-intro",
    "MAG_GFO":      "03y1_multi-mission-intro",
    "MAG_GFO_ML":   "03y1_multi-mission-intro",
    "MAG_GOCE":     "03y1_multi-mission-intro",
    "MAG_GOCE_ML":  "03y1_multi-mission-intro",
    # Electric field / ion drift (Langmuir probe)
    "EFI":          "03b__demo-efix_lp_1b",
    "EFI:B06":      "03b__demo-efix_lp_1b",
    "EFI_IDM":      "03k3_demo-efixidm",
    "EFI_TIE":      "03k1_demo-efixtie",
    "EFI_TCT02":    "03k2_demo-efixtct",
    "EFI_TCT16":    "03k2_demo-efixtct",
    # Ionosphere
    "IBI":          "03g__demo-ibixtms_2f",
    "TEC":          "03d__demo-tecxtms_2f",
    "FAC":          "03e1_demo-facxtms_2f",
    "EEF":          "03f__demo-eefxtms_2f",
    "IPD":          "03c__demo-ipdxirr_2f",
    # Auroral electrojets
    "AEJ_LPL":                          "03h1_demo-aebs-aejxlpl",
    "AEJ_LPL:Quality":                  "03h1_demo-aebs-aejxlpl",
    "AEJ_LPS":                          "03h2_demo-aebs-aejxlps",
    "AEJ_LPS:Quality":                  "03h2_demo-aebs-aejxlps",
    "AEJ_PBL":                          "03h2_demo-aebs-aejxlps",
    "AEJ_PBS":                          "03h2_demo-aebs-aejxlps",
    "AEJ_PBS:GroundMagneticDisturbance": "03h2_demo-aebs-aejxlps",
    "AOB_FAC":      "03h3_demo-aebs-aobxfac",
    # VOBS / GVO
    **{t: "03i1_demo-vobs" for t in _VOBS_COLLECTION_TYPES},
    **{t: "03i1_demo-vobs" for t in _VOBS_SV_COLLECTION_TYPES},
    # Ground observatories
    "AUX_OBSH":     "04c2_geomag-ground-data-vires",
    "AUX_OBSM":     "04c2_geomag-ground-data-vires",
    "AUX_OBSS":     "04c2_geomag-ground-data-vires",
    # Mid-latitude irregularities / plasma physics
    "MIT_LP":       "03j1_demo-prism-mitx",
    "MIT_LP:ID":    "03j1_demo-prism-mitx",
    "MIT_TEC":      "03j1_demo-prism-mitx",
    "MIT_TEC:ID":   "03j1_demo-prism-mitx",
    "PPI_FAC":      "03j2_demo-prism-ppixfac",
    "PPI_FAC:ID":   "03j2_demo-prism-ppixfac",
    # Neutral density / wind
    "DNS_POD":          "03l1_demo-dns",
    "DNS_ACC":          "03l1_demo-dns",
    "DNS_ACC_CHAMP":    "03l1_demo-dns",
    "DNS_ACC_GRACE":    "03l1_demo-dns",
    "DNS_ACC_GFO":      "03l1_demo-dns",
    "WND_ACC_CHAMP":    "03l2_demo-wnd",
    "WND_ACC_GRACE":    "03l2_demo-wnd",
    "WND_ACC_GFO":      "03l2_demo-wnd",
    # Conjunctions
    "MM_CON_EPH_2_:crossover":        "03l3_demo-conjunctions-toleos",
    "MM_CON_EPH_2_:plane_alignment":  "03l3_demo-conjunctions-toleos",
}

# Notebook URLs keyed by collection ID, resolved via COLLECTIONS_TO_TYPES.
_NOTEBOOK_URLS: Dict[str, str] = {
    col_id: _NB_BASE + slug
    for col_id, col_type in COLLECTIONS_TO_TYPES.items()
    if (slug := _NOTEBOOK_BY_TYPE.get(col_type))
}
# Override FAST MAG collections (share type keys with OPER but have own notebook).
_NOTEBOOK_URLS.update({
    col_id: _NB_BASE + "06a1_fast-intro"
    for mission in MAG_COLLECTIONS.values()
    for col_id in (c for sc in mission.values() for c in sc.values() if "FAST" in c)
})

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

_TIME_EXTENT_CACHE: Dict[str, Optional[Tuple[str, str]]] = {}


def _parse_iso8601_duration(duration_str: str) -> dt.timedelta:
    """Parse ISO 8601 duration string to timedelta.

    Supports formats like:
    - PT1S (1 second)
    - PT0.019S (0.019 seconds)
    - PT60M (60 minutes)
    - P31D (31 days)
    - P122D (122 days)
    """
    pattern = r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?'
    match = re.match(pattern, duration_str)

    if not match:
        raise ValueError(f"Invalid ISO 8601 duration: {duration_str}")

    days, hours, minutes, seconds = match.groups()

    return dt.timedelta(
        days=int(days) if days else 0,
        hours=int(hours) if hours else 0,
        minutes=int(minutes) if minutes else 0,
        seconds=float(seconds) if seconds else 0,
    )


def _extract_time_extent(value: object) -> Optional[Tuple[str, str]]:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (str, int, float)) for item in value[:2]):
            return str(value[0]), str(value[1])
        for item in value:
            found = _extract_time_extent(item)
            if found:
                return found
        return None

    if not isinstance(value, dict):
        return None

    extent_keys = {"timeExtent", "time_extent", "temporalExtent", "temporal_extent"}
    for key in extent_keys:
        if key in value:
            found = _extract_time_extent(value[key])
            if found:
                return found

    for start_key, end_key in (
        ("start", "end"),
        ("begin", "end"),
        ("start", "stop"),
        ("timeStart", "timeEnd"),
        ("time_start", "time_end"),
        ("start_time", "end_time"),
        ("timeCoverageStart", "timeCoverageEnd"),
        ("time_coverage_start", "time_coverage_end"),
    ):
        if start_key in value and end_key in value:
            return str(value[start_key]), str(value[end_key])

    for nested in value.values():
        found = _extract_time_extent(nested)
        if found:
            return found

    return None


def _get_collection_time_extent(collection: str) -> Optional[Tuple[str, str]]:
    if collection in _TIME_EXTENT_CACHE:
        return _TIME_EXTENT_CACHE[collection]

    vires = SwarmRequest()
    info = vires.get_collection_info(collection)
    value = _extract_time_extent(info)

    # Sub-collections (e.g. :SecularVariation) may not carry time extent metadata;
    # fall back to the base collection (strip the colon-suffix) in that case.
    if value is None and ":" in collection:
        base = collection.split(":")[0]
        value = _get_collection_time_extent(base)

    _TIME_EXTENT_CACHE[collection] = value
    return value


def _format_duration(td: Optional[dt.timedelta]) -> str:
    if td is None:
        return ""
    total_seconds = td.total_seconds()
    for seconds_per_unit, unit in (
        (365.25 * 86400, "year"),
        (30.44 * 86400, "month"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
        (1, "second"),
    ):
        n = total_seconds / seconds_per_unit
        if n >= 1:
            rounded = round(n)
            exact = abs(n - rounded) < 0.01
            label = f"{rounded}\u00a0{unit}{'s' if rounded != 1 else ''}"
            return label if exact else f"~{label}"
    return str(td)


def _format_time_extent_label(
    time_extent: Optional[Tuple[str, str]],
    max_duration: Optional[dt.timedelta] = None,
) -> str:
    availability = "unknown"
    if time_extent:
        start_day = time_extent[0][:10]
        end_day = time_extent[1][:10]
        availability = f"{start_day} \u2013 {end_day}"
    hint_style = "color:#6b7280;font-size:11px;margin:0"
    lines = [f"<p style='{hint_style}'>Available: {availability}</p>"]
    if max_duration is not None:
        lines.append(f"<p style='{hint_style}'>Max allowed: {_format_duration(max_duration)}</p>")
    hints = "".join(lines)
    return (
        f"<div style='margin-bottom:4px'>"
        f"<span style='font-weight:600'>Select times</span>"
        f"<br>{hints}"
        f"</div>"
    )


def _calculate_auto_time_range(collection: str) -> tuple[dt.datetime, dt.datetime, dt.timedelta]:
    """Calculate automatic time range and the viresclient max chunk duration.

    Anchors to the END of available data and subtracts the span.
    VOBS/GVO: 1 year. MAG HR: 5 min. MAG LR: 45 min.
    AUX_OBSH: 3 days. AUX_OBSM: 30 min. AUX_OBSS: 5 min."""
    collection_type = COLLECTIONS_TO_TYPES.get(collection, "MAG")
    is_vobs = collection_type in _ALL_VOBS_COLLECTION_TYPES

    is_mag = collection_type in _MAG_COLLECTION_TYPES
    is_mag_hr = collection_type in _MAG_HR_COLLECTION_TYPES
    is_obsh = collection_type == _AUX_OBSH_COLLECTION_TYPE
    is_obsm = collection_type == _AUX_OBSM_COLLECTION_TYPE
    is_obss = collection_type == _AUX_OBSS_COLLECTION_TYPE
    is_obs = is_obsh or is_obsm or is_obss
    start_obs = dt.datetime(2015, 1, 1)
    time_extent = _get_collection_time_extent(collection)
    if not time_extent:
        end = dt.datetime(2024, 3, 1)
        if is_vobs:
            span = dt.timedelta(days=365)
        elif is_mag_hr:
            span = dt.timedelta(minutes=5)
        elif is_mag:
            span = dt.timedelta(minutes=45)
        elif is_obsh:
            span = dt.timedelta(days=3)
        elif is_obsm:
            span = dt.timedelta(minutes=30)
        elif is_obss:
            span = dt.timedelta(minutes=5)
        else:
            span = dt.timedelta(minutes=1)
        start = start_obs if is_obs else end - span
        return start, start + span, _MAX_CHUNK_DURATION

    _, end_str = time_extent
    try:
        end = dt.datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        end = dt.datetime(2024, 3, 1)

    sampling_step_str = COLLECTION_SAMPLING_STEPS.get(collection_type, "PT1S")
    try:
        sampling_step = _parse_iso8601_duration(sampling_step_str)
    except Exception:
        sampling_step = dt.timedelta(seconds=1)

    max_duration = min(_NRECORDS_LIMIT * sampling_step, _MAX_CHUNK_DURATION)
    if is_vobs:
        span = dt.timedelta(days=5 * 365)
    elif is_mag_hr:
        span = dt.timedelta(minutes=5)
    elif is_mag:
        span = dt.timedelta(minutes=45)
    elif is_obsh:
        span = dt.timedelta(days=3)
    elif is_obsm:
        span = dt.timedelta(minutes=30)
    elif is_obss:
        span = dt.timedelta(minutes=5)
    else:
        span = sampling_step * 10
    if is_obs:
        return start_obs, start_obs + span, max_duration
    return end - span, end, max_duration


def _render_request_snippet(
    collection: str,
    measurements: List[str],
    auxiliaries: List[str],
    time_range: tuple[dt.datetime, dt.datetime],
    magnetic_model: str,
    is_vobs: bool = False,
    is_ground: bool = False,
) -> str:
    models_line = f"\n    models=['{magnetic_model}']," if magnetic_model else ""
    auxiliaries_line = f"\n    auxiliaries={auxiliaries},"
    as_xarray_args = "reshape=True" if (is_vobs or is_ground) else ""
    return REQUEST_TEMPLATE.format(
        collection=collection,
        measurements=measurements,
        models_line=models_line,
        auxiliaries_line=auxiliaries_line,
        time_range=time_range,
        as_xarray_args=as_xarray_args,
    ).replace("datetime.datetime", "dt.datetime")


def _build_preview_dataset(
    collection: str,
    measurements: List[str],
    auxiliaries: List[str],
    time_range: tuple[dt.datetime, dt.datetime],
    magnetic_model: str,
    reshape: bool = False,
):
    request = SwarmRequest()
    request.set_collection(collection, verbose=False)
    request.set_products(
        measurements=measurements,
        models=[magnetic_model] if magnetic_model else [],
        auxiliaries=auxiliaries,
    )
    data = request.get_between(
        start_time=time_range[0],
        end_time=time_range[1],
        asynchronous=False,
        show_progress=False,
    )
    return data.as_xarray(reshape=reshape)


def _build_plot(ds, measurements: List[str]) -> Optional[object]:
    """Generate holoviz plots from xarray dataset for selected measurements."""
    if ds is None or not measurements:
        return None

    try:
        plots = []
        for measurement in measurements:
            if measurement in ds.data_vars:
                var = ds[measurement]
                if not hasattr(var, 'values') or var.dtype.kind not in 'biufc':
                    continue
                if "Timestamp" in var.dims:
                    p = var.hvplot.line(
                        x="Timestamp",
                        label=measurement,
                        height=250,
                        width=700,
                    )
                    plots.append(p)

        if not plots:
            return None

        if len(plots) == 1:
            return plots[0]
        return hv.Layout(plots).cols(1).opts(opts.Layout(shared_axes=False))
    except Exception as e:
        print(f"Plot generation failed: {e}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# State class
# ---------------------------------------------------------------------------

class QueryState(param.Parameterized):
    # --- Generic collection selection ---
    collection_type = param.Selector(default="MAG", objects=list(COLLECTION_MAP.keys()))
    collection = param.Selector(objects=COLLECTION_MAP["MAG"])
    measurements = param.ListSelector(default=[], objects=MEASUREMENTS_BY_COLLECTION["MAG"])
    magnetic_model = param.Selector(default="", objects=[""] + MAG_MODELS)
    auxiliaries = param.ListSelector(default=[], objects=AUXILIARIES)
    time_range = param.DateRange(default=(dt.datetime(2024, 3, 1), dt.datetime(2024, 3, 1, 0, 1)))
    time_extent_label = param.String("")
    max_duration = param.Parameter(default=None)
    about_data_html = param.String("")
    code_snippet = param.String("")
    is_loading = param.Boolean(default=False)
    preview_dataset_html = param.String("")
    preview_plot = param.Parameter(default=None)
    plot_measurements = param.ListSelector(default=[], objects=[])

    # --- Magnetic (space) collection selection ---
    mission = param.Selector(default="Swarm", objects=list(MAG_COLLECTIONS.keys()))
    spacecraft = param.Selector()
    variant = param.Selector()

    # --- VOBS/GVO collection selection ---
    vobs_collection_label = param.Selector(
        default="Swarm (1-monthly)",
        objects=list(VOBS_COLLECTIONS.keys()),
        label="Collection",
    )
    vobs_secular_variation = param.Boolean(default=False, label="Secular Variation")

    # --- Ground collection selection ---
    ground_collection_label = param.Selector(
        default="AUX_OBSM (minute)",
        objects=list(AUX_OBS_COLLECTIONS.keys()),
        label="Collection",
    )

    _last_dataset = None
    _default_measurements_by_type = {
        **{mag_type: ["B_NEC"] for mag_type in _MAG_COLLECTION_TYPES},
        **{vobs_type: ["SiteCode", "B_OB", "B_CF"] for vobs_type in _VOBS_COLLECTION_TYPES},
        **{sv_type: ["SiteCode", "B_SV"] for sv_type in _VOBS_SV_COLLECTION_TYPES},
        _GROUND_COLLECTION_TYPE: ["B_NEC", "IAGA_code"],
    }
    _OBSH_COLLECTION_ID = AUX_OBS_COLLECTIONS["AUX_OBSH (hourly)"]

    def _ground_default_measurements(self) -> list:
        if self.collection == self._OBSH_COLLECTION_ID:
            return ["B_NEC", "IAGA_code", "ObsIndex"]
        return ["B_NEC", "IAGA_code"]

    @property
    def is_vobs(self) -> bool:
        return self.collection_type in _ALL_VOBS_COLLECTION_TYPES

    @property
    def is_ground(self) -> bool:
        return self.collection_type == _GROUND_COLLECTION_TYPE

    # --- Generic tab handlers ---

    @param.depends("collection_type", watch=True, on_init=True)
    def _update_collections_and_measurements(self) -> None:
        # Reset model/auxiliaries before collection changes to avoid stale
        # values leaking into requests triggered downstream.
        self.magnetic_model = ""
        self.auxiliaries = []
        # Update objects before values so ListSelector validation doesn't drop them.
        self.param["measurements"].objects = MEASUREMENTS_BY_COLLECTION[self.collection_type]
        self.measurements = list(self._default_measurements_by_type.get(self.collection_type, []))
        if self.is_vobs:
            is_sv = self.collection_type in _VOBS_SV_COLLECTION_TYPES
            src = VOBS_SV_COLLECTIONS if is_sv else VOBS_COLLECTIONS
            collections = COLLECTION_MAP.get(self.collection_type) or list(src.values())
            self.param["collection"].objects = collections
            preferred = src.get(self.vobs_collection_label)
            self.collection = preferred if preferred in collections else collections[0]
        elif self.is_ground:
            collections = list(AUX_OBS_COLLECTIONS.values())
            preferred = AUX_OBS_COLLECTIONS.get(self.ground_collection_label)
            self.param["collection"].objects = collections
            self.collection = preferred if preferred in collections else collections[0]
            self.measurements = self._ground_default_measurements()
        else:
            collections = COLLECTION_MAP[self.collection_type]
            self.param["collection"].objects = collections
            self.collection = collections[0]

    @param.depends("collection", "measurements", "time_range", watch=True, on_init=True)
    def _update_about_data(self) -> None:
        col = self.collection
        doc_url = _HANDBOOK_URLS.get(col)
        nb_url = _NOTEBOOK_URLS.get(col)
        doc_link = (
            f' <a href="{doc_url}" target="_blank" style="color:#2563eb;">Documentation ↗</a>'
            if doc_url else ""
        )
        if self.is_ground:
            doc_link += ' <a href="https://auxobs-api.bgs.ac.uk/docs" target="_blank" style="color:#2563eb;">AuxObs API ↗</a>'
        nb_link = (
            f' <a href="{nb_url}" target="_blank" style="color:#7c3aed;">Notebook ↗</a>'
            if nb_url else ""
        )
        hapi_link = ""
        if self.time_range and not self.is_vobs and not self.is_ground:
            start = self.time_range[0].strftime("%Y-%m-%dT%H:%M:%S.000Z")
            stop = self.time_range[1].strftime("%Y-%m-%dT%H:%M:%S.000Z")
            params_part = f"&parameters={','.join(self.measurements)}" if self.measurements else ""
            hapi_url = (
                f"https://hapi-server.org/servers/#server=VirES-for-Swarm"
                f"&dataset={col}{params_part}"
                f"&start={start}&stop={stop}&return=script&format=python"
            )
            hapi_link = f' <a href="{hapi_url}" target="_blank" style="color:#059669;">HAPI ↗</a>'
        self.about_data_html = (
            f"<p style='margin:0;font-size:12px;color:#374151'>"
            f"{col}{doc_link}{nb_link}{hapi_link}"
            f"</p>"
        )

    @param.depends(
        "collection",
        "measurements",
        "auxiliaries",
        "time_range",
        "magnetic_model",
        watch=True,
        on_init=True,
    )
    def _update_code_snippet(self) -> None:
        self.code_snippet = _render_request_snippet(
            collection=self.collection,
            measurements=list(self.measurements),
            auxiliaries=list(self.auxiliaries),
            time_range=self.time_range,
            magnetic_model=self.magnetic_model,
            is_vobs=self.is_vobs,
            is_ground=self.is_ground,
        )

    @param.depends("collection", "max_duration", watch=True, on_init=True)
    def _update_time_extent_label(self) -> None:
        time_extent = _get_collection_time_extent(self.collection)
        self.time_extent_label = _format_time_extent_label(time_extent, self.max_duration)

    @param.depends("code_snippet", watch=True, on_init=True)
    async def _update_preview_dataset(self) -> None:
        self.preview_dataset_html = "Loading preview..."
        self.preview_plot = None
        self.is_loading = True
        try:
            ds = await asyncio.to_thread(
                _build_preview_dataset,
                self.collection,
                list(self.measurements),
                list(self.auxiliaries),
                self.time_range,
                self.magnetic_model,
                self.is_vobs or self.is_ground,
            )
        except Exception as exc:
            self.preview_dataset_html = f"Preview failed: {exc}"
        else:
            self._last_dataset = ds
            self.preview_dataset_html = ds._repr_html_()
            available_measurements = [
                m for m in ds.data_vars if ds[m].dtype.kind in "biufc"
            ]
            self.param["plot_measurements"].objects = available_measurements
            if available_measurements:
                preferred = next(
                    (m for m in self.measurements if m in available_measurements),
                    available_measurements[0],
                )
                self.plot_measurements = [preferred]
            self.preview_plot = await asyncio.to_thread(
                _build_plot,
                ds,
                self.plot_measurements or list(self.measurements),
            )
        finally:
            self.is_loading = False

    @param.depends("plot_measurements", watch=True)
    def _update_preview_plot_on_plot_measurement_change(self) -> None:
        if self._last_dataset is not None:
            self.preview_plot = _build_plot(
                self._last_dataset,
                list(self.plot_measurements) if self.plot_measurements else [],
            )

    @param.depends("collection", watch=True, on_init=True)
    def _update_auto_time_range(self) -> None:
        start, end, max_duration = _calculate_auto_time_range(self.collection)
        self.time_range = (start, end)
        self.max_duration = max_duration

    # --- Magnetic (space) tab handlers ---

    @param.depends("mission", watch=True, on_init=True)
    def _update_spacecraft(self) -> None:
        spacecrafts = list(MAG_COLLECTIONS[self.mission].keys())
        self.param["spacecraft"].objects = spacecrafts
        self.spacecraft = spacecrafts[0]

    @param.depends("spacecraft", watch=True, on_init=True)
    def _update_variants(self) -> None:
        variants = list(MAG_COLLECTIONS[self.mission][self.spacecraft].keys())
        self.param["variant"].objects = variants
        self.variant = variants[0]

    @param.depends("spacecraft", "variant", watch=True, on_init=True)
    def _update_collection_from_mag(self) -> None:
        mag_collection = MAG_COLLECTIONS[self.mission][self.spacecraft][self.variant]
        self.collection_type = COLLECTIONS_TO_TYPES[mag_collection]
        self.collection = mag_collection

    # --- VOBS/GVO tab handlers ---

    @param.depends("vobs_collection_label", "vobs_secular_variation", watch=True)
    def _update_vobs_collection(self) -> None:
        if not self.is_vobs:
            return
        src = VOBS_SV_COLLECTIONS if self.vobs_secular_variation else VOBS_COLLECTIONS
        new_collection = src[self.vobs_collection_label]
        new_type = COLLECTIONS_TO_TYPES.get(new_collection, self.collection_type)
        if new_type != self.collection_type:
            # Changing collection_type triggers _update_collections_and_measurements
            # which resets measurements, model, auxiliaries, collection objects, and collection value.
            self.collection_type = new_type
        else:
            # Same type (e.g. label changed within the same mission group):
            # update collection objects + selection and reset measurements to defaults.
            collections = COLLECTION_MAP.get(self.collection_type) or list(src.values())
            self.param["collection"].objects = collections
            self.collection = new_collection
            self.param["measurements"].objects = MEASUREMENTS_BY_COLLECTION[self.collection_type]
            self.measurements = list(self._default_measurements_by_type.get(self.collection_type, []))

    @param.depends("ground_collection_label", watch=True)
    def _update_ground_collection(self) -> None:
        if self.is_ground:
            self.collection = AUX_OBS_COLLECTIONS[self.ground_collection_label]
            self.measurements = self._ground_default_measurements()


# ---------------------------------------------------------------------------
# Dashboard builder sub-functions
# ---------------------------------------------------------------------------

def _build_collection_tabs(state: QueryState) -> tuple[pn.Tabs, pn.Tabs]:
    all_collections_content = pn.Param(
        state,
        parameters=["collection_type", "collection"],
        widgets={"collection": {"type": pn.widgets.Select, "size": 6}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    space_content = pn.Param(
        state,
        parameters=["mission", "spacecraft", "variant"],
        show_name=False,
        sizing_mode="stretch_width",
    )
    vobs_content = pn.Param(
        state,
        parameters=["vobs_collection_label", "vobs_secular_variation"],
        widgets={"vobs_secular_variation": {"type": pn.widgets.Checkbox}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    ground_content = pn.Param(
        state,
        parameters=["ground_collection_label"],
        show_name=False,
        sizing_mode="stretch_width",
    )
    magnetic_tabs = pn.Tabs(
        ("Space", space_content),
        ("GVO / VOBS", vobs_content),
        ("Ground", ground_content),
        sizing_mode="stretch_width",
    )
    collection_tabs = pn.Tabs(
        ("All collections", all_collections_content),
        ("Magnetic", magnetic_tabs),
        sizing_mode="stretch_width",
    )
    return collection_tabs, magnetic_tabs


def _build_time_section(state: QueryState) -> pn.Column:
    time_range_label = pn.pane.HTML(
        state.time_extent_label,
        sizing_mode="stretch_width",
        margin=(0, 0, 0, 0),
    )
    state.param.watch(
        lambda event: setattr(time_range_label, "object", event.new),
        "time_extent_label",
    )
    time_range_widget = pn.widgets.DatetimeRangePicker.from_param(
        state.param.time_range,
        name="",
        sizing_mode="stretch_width",
    )
    return pn.Column(time_range_label, time_range_widget, sizing_mode="stretch_width")


def _build_parameters(state: QueryState) -> pn.Column:
    measurements_param = pn.Param(
        state,
        parameters=["measurements"],
        widgets={"measurements": {"type": pn.widgets.CheckBoxGroup}},
        name="Measurements",
        sizing_mode="stretch_width",
    )
    models_param = pn.Param(
        state,
        parameters=["magnetic_model"],
        name="Models",
        sizing_mode="stretch_width",
    )
    auxiliaries_param = pn.Param(
        state,
        parameters=["auxiliaries"],
        widgets={"auxiliaries": {"type": pn.widgets.CheckBoxGroup}},
        name="Auxiliaries",
        sizing_mode="stretch_width",
    )
    selection_tabs = pn.layout.Tabs(
        measurements_param,
        models_param,
        auxiliaries_param,
        sizing_mode="stretch_width",
    )
    return pn.Column(selection_tabs, sizing_mode="stretch_width")




def _setup_watchers(
    magnetic_tabs: pn.Tabs,
    state: QueryState,
    code_editor: pn.widgets.CodeEditor,
    html_pane: pn.pane.HTML,
    plot_pane: pn.Column,
) -> None:
    def _on_magnetic_tab_changed(active_index: int) -> None:
        if active_index == 0:  # Space
            state._update_collection_from_mag()
        elif active_index == _VOBS_MAGNETIC_TAB and not state.is_vobs:
            src = VOBS_SV_COLLECTIONS if state.vobs_secular_variation else VOBS_COLLECTIONS
            col = src.get(state.vobs_collection_label, next(iter(src.values())))
            state.collection_type = COLLECTIONS_TO_TYPES.get(col, _VOBS_COLLECTION_TYPE)
        elif active_index == _GROUND_MAGNETIC_TAB and not state.is_ground:
            state.collection_type = _GROUND_COLLECTION_TYPE

    magnetic_tabs.param.watch(lambda event: _on_magnetic_tab_changed(event.new), "active")
    _on_magnetic_tab_changed(magnetic_tabs.active)

    def _update_plot(event: param.parameterized.Event) -> None:
        plot_pane.clear()
        if event.new is not None:
            plot_pane.append(event.new)
        else:
            plot_pane.append(pn.pane.Markdown("No plot available for selected measurements."))

    state.param.watch(_update_plot, "preview_plot")
    state.param.watch(lambda e: setattr(code_editor, "value", e.new), "code_snippet")
    state.param.watch(lambda e: setattr(html_pane, "object", e.new), "preview_dataset_html")


def _build_nav_banner() -> pn.pane.HTML:
    link_style = (
        "color: #93c5fd; font-size: 11px; text-decoration: underline;"
        " text-underline-offset: 2px; white-space: nowrap;"
    )
    return pn.pane.HTML(
        f"""
        <div style="
            background: #0f2a4a;
            border-radius: 6px;
            padding: 6px 14px;
            margin: 4px 0;
            color: #e0e7ef;
            font-family: sans-serif;
        ">
            <div style="font-size: 11px; line-height: 1.5; margin-bottom: 4px;">
                This dashboard is in active development and is provided here for testing purposes<br>
                Contact: <a href="mailto:ashley.smith@ed.ac.uk" style="{link_style}">ashley.smith@ed.ac.uk</a>
            </div>
            <div style="display: flex; gap: 18px; flex-wrap: wrap;">
                <a href=".." style="{link_style}">..to the other dashboards</a>
                <a href="https://viresclient.readthedocs.io/" target="_blank" style="{link_style}">viresclient Docs</a>
                <a href="https://github.com/Swarm-DISC/dashboards/" target="_blank" style="{link_style}">Dashboards repo</a>
            </div>
        </div>
        """,
        sizing_mode="stretch_width",
    )


def _build_header_banner() -> pn.pane.HTML:
    return pn.pane.HTML(
        """
        <div style="
            background: #f0f9ff;
            border-left: 4px solid #2563eb;
            padding: 16px 20px;
            border-radius: 0 8px 8px 0;
            margin-bottom: 4px;
        ">
            <h2 style="margin: 0 0 6px 0; color: #1e3a5f; font-size: 17px; font-weight: 600;">
                VirES Query Builder
            </h2>
            <p style="margin: 0; font-size: 13px; color: #475569; line-height: 1.6;">
                Browse collections from the
                <a href="https://vires.services" style="color: #2563eb;" target="_blank">VirES for Swarm</a>
                service. Select a collection, configure measurements and time range, then copy the
                generated
                <a href="https://viresclient.readthedocs.io" style="color: #2563eb;" target="_blank">viresclient</a>
                code into your own Python environment. A small data preview loads automatically.
                See <a href="https://viresclient.readthedocs.io/en/latest/capabilities.html" style="color: #2563eb;" target="_blank">VirES capabilities</a> for more possibilities.
            </p>
        </div>
        """,
        sizing_mode="stretch_width",
    )


def _build_dashboard(state: QueryState) -> pn.template.FastListTemplate:
    collection_layout, magnetic_tabs = _build_collection_tabs(state)
    time_section_content = _build_time_section(state)
    parameters = _build_parameters(state)

    collection_section = pn.Column(
        pn.pane.Markdown("**Select collection**", margin=(0, 0, 8, 0)),
        collection_layout,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["collection"],
    )
    time_section = pn.Column(
        time_section_content,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["time"],
    )
    parameters_section = pn.Column(
        pn.pane.Markdown("**Select parameters**", margin=(0, 0, 8, 0)),
        parameters,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["parameters"],
    )

    plot_measurements_selector = pn.Param(
        state,
        parameters=["plot_measurements"],
        widgets={"plot_measurements": {"type": pn.widgets.MultiSelect, "size": 5}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    plot_selector_container = pn.Column(
        pn.pane.Markdown("**Measurement to plot:**", margin=(0, 0, 4, 0), styles={"font-size": "12px"}),
        plot_measurements_selector,
        sizing_mode="stretch_width",
    )

    code_editor = pn.widgets.CodeEditor(
        value=state.code_snippet,
        height=400,
        language="python",
        readonly=True,
        print_margin=False,
        sizing_mode="stretch_width",
    )
    html_pane = pn.pane.HTML(state.preview_dataset_html, sizing_mode="stretch_both", min_height=300)
    plot_pane = pn.Column(
        pn.pane.Markdown("Plot available when data loads."),
        sizing_mode="stretch_both",
        min_height=300,
    )
    main_tabs = pn.layout.Tabs(
        ("Code", code_editor),
        ("Data", html_pane),
        ("Plot", pn.Column(plot_selector_container, plot_pane, sizing_mode="stretch_both")),
        sizing_mode="stretch_both",
    )

    about_data_pane = pn.pane.HTML(
        state.about_data_html,
        sizing_mode="stretch_width",
        margin=(0, 0, 4, 0),
    )
    state.param.watch(lambda e: setattr(about_data_pane, "object", e.new), "about_data_html")

    progress_bar = pn.widgets.Progress(
        value=-1,
        active=False,
        visible=False,
        sizing_mode="stretch_width",
        height=4,
        margin=(0, 0, 0, 0),
        bar_color="primary",
    )

    def _update_progress(event: param.parameterized.Event) -> None:
        progress_bar.active = event.new
        progress_bar.visible = event.new
        if event.new:
            main_tabs.styles = {"opacity": "0.4", "pointer-events": "none"}
        else:
            main_tabs.styles = {"opacity": "1", "pointer-events": "auto"}

    state.param.watch(_update_progress, "is_loading")

    main_card = pn.Column(about_data_pane, progress_bar, main_tabs, sizing_mode="stretch_both")

    _setup_watchers(magnetic_tabs, state, code_editor, html_pane, plot_pane)

    return pn.template.FastListTemplate(
        title="VirES Query Builder",
        header=[_build_nav_banner()],
        sidebar=[collection_section, time_section, parameters_section],
        main=[_build_header_banner(), main_card],
        sidebar_width=380,
        header_background="#1d4ed8",
        accent="#2563eb",
        theme_toggle=False,
        raw_css=[
            "#header { height: 80px !important; min-height: 80px !important; }",
        ],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

state = QueryState()
dashboard = _build_dashboard(state)
dashboard.servable()
