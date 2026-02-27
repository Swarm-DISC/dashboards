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

pn.extension("codeeditor")
if pn.state.curdoc is not None:
    pn.state.curdoc.title = "VirES Query Builder"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VOBS_TAB = 2  # index of the VOBS/GVO tab in the collection-type tab bar

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
    "preview": {
        "border": "1px solid #fbcfe8",
        "background": "#fdf2f8",
        "border-radius": "8px",
        "padding": "12px",
    },
}
_HINT_STYLES = {"color": "#374151", "font-size": "12px"}
_EXCLUDED_AUXILIARIES = {"Timestamp", "Latitude", "Longitude", "Radius", "Spacecraft"}

# ---------------------------------------------------------------------------
# Request templates
# ---------------------------------------------------------------------------

REQUEST_TEMPLATE = """import datetime as dt
from viresclient import SwarmRequest

request = SwarmRequest()
request.set_collection('{collection}', verbose=False)
request.set_products(
    measurements={measurements},
    models=['{magnetic_model}'],
    auxiliaries={auxiliaries},
    # sampling_step="PT1S"
)
data = request.get_between(
    start_time={time_range[0]!r},
    end_time={time_range[1]!r},
    asynchronous=False,
    show_progress=False,
)
ds = data.as_xarray()"""

VOBS_REQUEST_TEMPLATE = """import datetime as dt
from viresclient import SwarmRequest

request = SwarmRequest()
request.set_collection('{collection}', verbose=False)
request.set_products(
    measurements={measurements},
    # sampling_step="PT1S"
)
data = request.get_between(
    start_time={time_range[0]!r},
    end_time={time_range[1]!r},
    asynchronous=False,
    show_progress=False,
)
ds = data.as_xarray(reshape=True)"""

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

# Derive the VirES collection-type key for VOBS collections at startup.
# COLLECTIONS_TO_TYPES maps collection IDs → type keys; fall back to "VOBS".
_VOBS_COLLECTION_TYPE: str = COLLECTIONS_TO_TYPES.get(
    next(iter(VOBS_COLLECTIONS.values())), "VOBS"
)

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
    _TIME_EXTENT_CACHE[collection] = value
    return value


def _format_time_extent_label(time_extent: Optional[Tuple[str, str]]) -> str:
    if not time_extent:
        return "Available time: unknown"
    start, end = time_extent
    return f"Available time: {start} to {end}"


def _calculate_auto_time_range(collection: str) -> tuple[dt.datetime, dt.datetime]:
    """Calculate automatic time range: starts at collection availability start, spans 10 samples."""
    time_extent = _get_collection_time_extent(collection)
    if not time_extent:
        start = dt.datetime(2024, 3, 1)
        return start, start + dt.timedelta(minutes=1)

    start_str, _ = time_extent
    try:
        start = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        start = dt.datetime(2024, 3, 1)

    collection_type = COLLECTIONS_TO_TYPES.get(collection, "MAG")
    sampling_step_str = COLLECTION_SAMPLING_STEPS.get(collection_type, "PT1S")
    try:
        sampling_step = _parse_iso8601_duration(sampling_step_str)
    except Exception:
        sampling_step = dt.timedelta(seconds=1)

    return start, start + sampling_step * 10


def _render_request_snippet(
    collection: str,
    measurements: List[str],
    auxiliaries: List[str],
    time_range: tuple[dt.datetime, dt.datetime],
    magnetic_model: str,
) -> str:
    return REQUEST_TEMPLATE.format(
        collection=collection,
        measurements=measurements,
        auxiliaries=auxiliaries,
        time_range=time_range,
        magnetic_model=magnetic_model,
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
    code_snippet = param.String("")
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

    _last_dataset = None
    _default_measurements_by_type = {
        "MAG": ["B_NEC"],
        _VOBS_COLLECTION_TYPE: ["SiteCode", "B_OB", "B_CF"],
    }

    @property
    def is_vobs(self) -> bool:
        return self.collection_type == _VOBS_COLLECTION_TYPE

    # --- Generic tab handlers ---

    @param.depends("collection_type", watch=True, on_init=True)
    def _update_collections_and_measurements(self) -> None:
        # Update objects before values so ListSelector validation doesn't drop them.
        self.param["measurements"].objects = MEASUREMENTS_BY_COLLECTION[self.collection_type]
        self.measurements = list(self._default_measurements_by_type.get(self.collection_type, []))
        # VOBS collections span multiple VirES type keys, so use the full VOBS_COLLECTIONS
        # list as objects rather than just the single-type slice from COLLECTION_MAP.
        if self.is_vobs:
            collections = list(VOBS_COLLECTIONS.values())
            preferred = VOBS_COLLECTIONS.get(self.vobs_collection_label)
            self.param["collection"].objects = collections
            self.collection = preferred if preferred in collections else collections[0]
        else:
            collections = COLLECTION_MAP[self.collection_type]
            self.param["collection"].objects = collections
            self.collection = collections[0]

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
        if self.is_vobs:
            self.code_snippet = VOBS_REQUEST_TEMPLATE.format(
                collection=self.collection,
                measurements=list(self.measurements),
                time_range=self.time_range,
            )
        else:
            self.code_snippet = _render_request_snippet(
                collection=self.collection,
                measurements=self.measurements,
                auxiliaries=self.auxiliaries,
                time_range=self.time_range,
                magnetic_model=self.magnetic_model,
            )

    @param.depends("collection", watch=True, on_init=True)
    def _update_time_extent_label(self) -> None:
        time_extent = _get_collection_time_extent(self.collection)
        self.time_extent_label = _format_time_extent_label(time_extent)

    @param.depends("code_snippet", watch=True, on_init=True)
    async def _update_preview_dataset(self) -> None:
        self.preview_dataset_html = "Loading preview..."
        self.preview_plot = None
        try:
            ds = await asyncio.to_thread(
                _build_preview_dataset,
                self.collection,
                list(self.measurements),
                [] if self.is_vobs else list(self.auxiliaries),
                self.time_range,
                "" if self.is_vobs else self.magnetic_model,
                self.is_vobs,
            )
        except Exception as exc:
            self.preview_dataset_html = f"Preview failed: {exc}"
        else:
            self._last_dataset = ds
            self.preview_dataset_html = ds._repr_html_()
            available_measurements = [m for m in self.measurements if m in ds.data_vars]
            self.param["plot_measurements"].objects = available_measurements
            if available_measurements:
                self.plot_measurements = [available_measurements[0]]
            self.preview_plot = await asyncio.to_thread(
                _build_plot,
                ds,
                self.plot_measurements or list(self.measurements),
            )

    @param.depends("plot_measurements", watch=True)
    def _update_preview_plot_on_plot_measurement_change(self) -> None:
        if self._last_dataset is not None:
            self.preview_plot = _build_plot(
                self._last_dataset,
                list(self.plot_measurements) if self.plot_measurements else [],
            )

    @param.depends("collection", watch=True, on_init=True)
    def _update_auto_time_range(self) -> None:
        start, end = _calculate_auto_time_range(self.collection)
        self.time_range = (start, end)

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

    @param.depends("vobs_collection_label", watch=True)
    def _update_vobs_collection(self) -> None:
        if self.is_vobs:
            self.collection = VOBS_COLLECTIONS[self.vobs_collection_label]


# ---------------------------------------------------------------------------
# Dashboard builder sub-functions
# ---------------------------------------------------------------------------

def _build_collection_tabs(state: QueryState) -> pn.Tabs:
    return pn.layout.Tabs(
        pn.Param(
            state,
            parameters=["collection_type", "collection"],
            widgets={"collection": {"type": pn.widgets.Select, "size": 6}},
            name="Generic",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            state,
            parameters=["mission", "spacecraft", "variant"],
            name="Magnetic (space)",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            state,
            parameters=["vobs_collection_label"],
            name="VOBS/GVO",
            sizing_mode="stretch_width",
        ),
        sizing_mode="stretch_width",
    )


def _build_parameters(state: QueryState) -> pn.Column:
    time_range_hint = pn.pane.Markdown(
        state.time_extent_label,
        sizing_mode="stretch_width",
        margin=(4, 0, 0, 0),
        styles=_HINT_STYLES,
    )
    state.param.watch(
        lambda event: setattr(time_range_hint, "object", event.new),
        "time_extent_label",
    )
    time_range = pn.Param(
        state,
        parameters=["time_range"],
        widgets={"time_range": {"type": pn.widgets.DatetimeRangePicker}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    measurements_param = pn.Param(
        state,
        parameters=["measurements"],
        widgets={"measurements": {"type": pn.widgets.CheckBoxGroup}},
        name="Measurements",
        sizing_mode="stretch_width",
    )
    auxiliaries_param = pn.Param(
        state,
        parameters=["magnetic_model", "auxiliaries"],
        widgets={"auxiliaries": {"type": pn.widgets.CheckBoxGroup}},
        name="Auxiliaries",
        sizing_mode="stretch_width",
        visible=not state.is_vobs,
    )

    def _update_auxiliaries_visibility(event: param.parameterized.Event) -> None:
        is_vobs = event.new == _VOBS_COLLECTION_TYPE
        auxiliaries_param.visible = not is_vobs
        if is_vobs and selection_tabs.active == 1:
            selection_tabs.active = 0

    state.param.watch(_update_auxiliaries_visibility, "collection_type")

    selection_tabs = pn.layout.Tabs(
        measurements_param,
        auxiliaries_param,
        sizing_mode="stretch_width",
    )
    return pn.Column(time_range, time_range_hint, selection_tabs, sizing_mode="stretch_width")


def _build_code_editor(initial_value: str) -> tuple[pn.Column, pn.widgets.CodeEditor]:
    editor = pn.widgets.CodeEditor(
        value=initial_value,
        height=360,
        language="python",
        readonly=True,
        print_margin=False,
        sizing_mode="stretch_width",
    )
    section = pn.Column(
        pn.pane.Markdown("**Code example**", margin=(0, 0, 8, 0)),
        editor,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["code"],
    )
    return section, editor


def _build_preview_section(
    initial_html: str,
    plot_selector_container: pn.Column,
) -> tuple[pn.Column, pn.pane.HTML, pn.Column]:
    html_pane = pn.pane.HTML(initial_html, sizing_mode="stretch_both", min_height=300)
    plot_pane = pn.Column(
        pn.pane.Markdown("Plot available when data loads."),
        sizing_mode="stretch_both",
        min_height=300,
    )
    preview_tabs = pn.layout.Tabs(
        ("Data", html_pane),
        ("Plot", pn.Column(
            plot_selector_container,
            plot_pane,
            sizing_mode="stretch_both",
        )),
        sizing_mode="stretch_both",
    )
    section = pn.Column(
        pn.pane.Markdown("**Preview**", margin=(0, 0, 8, 0)),
        preview_tabs,
        sizing_mode="stretch_both",
        styles=_SECTION_STYLES["preview"],
    )
    return section, html_pane, plot_pane


def _setup_watchers(
    collection_tabs: pn.Tabs,
    state: QueryState,
    code_editor: pn.widgets.CodeEditor,
    html_pane: pn.pane.HTML,
    plot_pane: pn.Column,
) -> None:
    def _on_vobs_tab_activated(active_index: int) -> None:
        """When the VOBS/GVO tab is selected, switch state to the VOBS collection type."""
        if active_index == VOBS_TAB and not state.is_vobs:
            state.collection_type = _VOBS_COLLECTION_TYPE

    collection_tabs.param.watch(lambda event: _on_vobs_tab_activated(event.new), "active")
    _on_vobs_tab_activated(collection_tabs.active)

    def _update_plot(event: param.parameterized.Event) -> None:
        plot_pane.clear()
        if event.new is not None:
            plot_pane.append(event.new)
        else:
            plot_pane.append(pn.pane.Markdown("No plot available for selected measurements."))

    state.param.watch(_update_plot, "preview_plot")
    state.param.watch(lambda e: setattr(code_editor, "value", e.new), "code_snippet")
    state.param.watch(lambda e: setattr(html_pane, "object", e.new), "preview_dataset_html")


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
            </p>
        </div>
        """,
        sizing_mode="stretch_width",
    )


def _build_dashboard(state: QueryState) -> pn.template.FastListTemplate:
    collection_tabs = _build_collection_tabs(state)
    parameters = _build_parameters(state)

    collection_section = pn.Column(
        pn.pane.Markdown("**Select collection**", margin=(0, 0, 8, 0)),
        collection_tabs,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["collection"],
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

    code_section, code_editor = _build_code_editor(state.code_snippet)
    preview_section, html_pane, plot_pane = _build_preview_section(
        state.preview_dataset_html,
        plot_selector_container,
    )

    _setup_watchers(collection_tabs, state, code_editor, html_pane, plot_pane)

    return pn.template.FastListTemplate(
        title="VirES Query Builder",
        sidebar=[collection_section, parameters_section],
        main=[_build_header_banner(), code_section, preview_section],
        sidebar_width=380,
        header_background="#1d4ed8",
        accent="#2563eb",
        theme_toggle=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

state = QueryState()
dashboard = _build_dashboard(state)
dashboard.servable()
