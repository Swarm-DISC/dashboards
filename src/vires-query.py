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
# State classes
# ---------------------------------------------------------------------------

class MagState(param.Parameterized):
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

    mission = param.Selector(default="Swarm", objects=list(MAG_COLLECTIONS.keys()))
    spacecraft = param.Selector()
    variant = param.Selector()

    _last_dataset = None
    _default_measurements_by_type = {"MAG": ["B_NEC"]}

    @param.depends("collection_type", watch=True, on_init=True)
    def _update_collections_and_measurements(self) -> None:
        self.measurements = list(self._default_measurements_by_type.get(self.collection_type, []))
        collections = COLLECTION_MAP[self.collection_type]
        self.param["collection"].objects = collections
        self.collection = collections[0]
        self.param["measurements"].objects = MEASUREMENTS_BY_COLLECTION[self.collection_type]

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
                list(self.auxiliaries),
                self.time_range,
                self.magnetic_model,
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

    @param.depends("collection", watch=True, on_init=True)
    def _update_auto_time_range(self) -> None:
        start, end = _calculate_auto_time_range(self.collection)
        self.time_range = (start, end)


class VobsState(param.Parameterized):
    collection = param.Selector(
        default="Swarm (1-monthly)", objects=list(VOBS_COLLECTIONS.keys())
    )
    measurements = param.ListSelector(default=[], objects=[])
    time_range = param.DateRange(
        default=(dt.datetime(2024, 3, 1), dt.datetime(2024, 3, 1, 0, 1))
    )
    time_extent_label = param.String("")
    code_snippet = param.String("")
    preview_dataset_html = param.String("")
    preview_plot = param.Parameter(default=None)
    plot_measurements = param.ListSelector(default=[], objects=[])

    _last_dataset = None
    _default_measurements = ["SiteCode", "B_OB", "B_CF"]

    @property
    def collection_id(self) -> str:
        return VOBS_COLLECTIONS[self.collection]

    @param.depends("collection", watch=True, on_init=True)
    def _update_measurements(self) -> None:
        vires = SwarmRequest()
        try:
            measurements = vires.available_measurements(self.collection_id)
            self.param["measurements"].objects = measurements
            self.measurements = [
                m for m in self._default_measurements if m in measurements
            ]
        except Exception:
            self.param["measurements"].objects = []
            self.measurements = []

    @param.depends("collection", watch=True, on_init=True)
    def _update_auto_time_range(self) -> None:
        start, end = _calculate_auto_time_range(self.collection_id)
        self.time_range = (start, end)

    @param.depends("collection", watch=True, on_init=True)
    def _update_time_extent_label(self) -> None:
        time_extent = _get_collection_time_extent(self.collection_id)
        self.time_extent_label = _format_time_extent_label(time_extent)

    @param.depends("collection", "measurements", "time_range", watch=True, on_init=True)
    def _update_code_snippet(self) -> None:
        self.code_snippet = VOBS_REQUEST_TEMPLATE.format(
            collection=self.collection_id,
            measurements=list(self.measurements),
            time_range=self.time_range,
        )

    @param.depends("code_snippet", watch=True, on_init=True)
    async def _update_preview_dataset(self) -> None:
        self.preview_dataset_html = "Loading preview..."
        self.preview_plot = None
        try:
            ds = await asyncio.to_thread(
                _build_preview_dataset,
                self.collection_id,
                list(self.measurements),
                [],
                self.time_range,
                "",
                True,
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
    def _update_preview_plot_on_measurement_change(self) -> None:
        if self._last_dataset is not None:
            self.preview_plot = _build_plot(
                self._last_dataset,
                list(self.plot_measurements) if self.plot_measurements else [],
            )


# ---------------------------------------------------------------------------
# Dashboard builder sub-functions
# ---------------------------------------------------------------------------

def _build_collection_tabs(mag_state: MagState, vobs_state: VobsState) -> pn.Tabs:
    return pn.layout.Tabs(
        pn.Param(
            mag_state,
            parameters=["collection_type", "collection"],
            widgets={"collection": {"type": pn.widgets.Select, "size": 6}},
            name="Generic",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            mag_state,
            parameters=["mission", "spacecraft", "variant"],
            name="Magnetic (space)",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            vobs_state,
            parameters=["collection"],
            name="VOBS/GVO",
            sizing_mode="stretch_width",
        ),
        sizing_mode="stretch_width",
    )


def _build_mag_parameters(mag_state: MagState) -> pn.Column:
    time_range_hint = pn.pane.Markdown(
        mag_state.time_extent_label,
        sizing_mode="stretch_width",
        margin=(4, 0, 0, 0),
        styles=_HINT_STYLES,
    )
    mag_state.param.watch(
        lambda event: setattr(time_range_hint, "object", event.new),
        "time_extent_label",
    )
    time_range = pn.Param(
        mag_state,
        parameters=["time_range"],
        widgets={"time_range": {"type": pn.widgets.DatetimeRangePicker}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    selection_tabs = pn.layout.Tabs(
        pn.Param(
            mag_state,
            parameters=["measurements"],
            widgets={"measurements": {"type": pn.widgets.CheckBoxGroup}},
            name="Measurements",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            mag_state,
            parameters=["magnetic_model", "auxiliaries"],
            widgets={"auxiliaries": {"type": pn.widgets.CheckBoxGroup}},
            name="Auxiliaries",
            sizing_mode="stretch_width",
        ),
        sizing_mode="stretch_width",
    )
    return pn.Column(time_range, time_range_hint, selection_tabs, sizing_mode="stretch_width")


def _build_vobs_parameters(vobs_state: VobsState) -> pn.Column:
    time_range_hint = pn.pane.Markdown(
        vobs_state.time_extent_label,
        sizing_mode="stretch_width",
        margin=(4, 0, 0, 0),
        styles=_HINT_STYLES,
    )
    vobs_state.param.watch(
        lambda event: setattr(time_range_hint, "object", event.new),
        "time_extent_label",
    )
    time_range = pn.Param(
        vobs_state,
        parameters=["time_range"],
        widgets={"time_range": {"type": pn.widgets.DatetimeRangePicker}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    measurements_tab = pn.Param(
        vobs_state,
        parameters=["measurements"],
        widgets={"measurements": {"type": pn.widgets.CheckBoxGroup}},
        name="Measurements",
        sizing_mode="stretch_width",
    )
    return pn.Column(
        time_range,
        time_range_hint,
        measurements_tab,
        sizing_mode="stretch_width",
        visible=False,
    )


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


def _setup_tab_watchers(
    collection_tabs: pn.Tabs,
    mag_state: MagState,
    vobs_state: VobsState,
    mag_params: pn.Column,
    vobs_params: pn.Column,
    code_editor: pn.widgets.CodeEditor,
    html_pane: pn.pane.HTML,
    plot_pane: pn.Column,
    plot_measurements_selector: pn.Param,
    vobs_plot_measurements_selector: pn.Param,
) -> None:
    def _no_plot_message() -> pn.pane.Markdown:
        return pn.pane.Markdown("No plot available for selected measurements.")

    def _sync_all(active_index: int) -> None:
        is_vobs = active_index == VOBS_TAB
        mag_params.visible = not is_vobs
        vobs_params.visible = is_vobs
        plot_measurements_selector.visible = not is_vobs
        vobs_plot_measurements_selector.visible = is_vobs
        state = vobs_state if is_vobs else mag_state
        code_editor.value = state.code_snippet
        html_pane.object = state.preview_dataset_html
        plot_pane.clear()
        if state.preview_plot is not None:
            plot_pane.append(state.preview_plot)
        else:
            plot_pane.append(_no_plot_message())

    collection_tabs.param.watch(lambda event: _sync_all(event.new), "active")
    _sync_all(collection_tabs.active)

    def _update_plot(event: param.parameterized.Event) -> None:
        if event.new is not None:
            plot_pane.clear()
            plot_pane.append(event.new)
        else:
            plot_pane.clear()
            plot_pane.append(_no_plot_message())

    def _on_mag_code_update(event: param.parameterized.Event) -> None:
        if collection_tabs.active != VOBS_TAB:
            code_editor.value = event.new

    def _on_vobs_code_update(event: param.parameterized.Event) -> None:
        if collection_tabs.active == VOBS_TAB:
            code_editor.value = event.new

    def _on_mag_preview_update(event: param.parameterized.Event) -> None:
        if collection_tabs.active != VOBS_TAB:
            html_pane.object = event.new

    def _on_vobs_preview_update(event: param.parameterized.Event) -> None:
        if collection_tabs.active == VOBS_TAB:
            html_pane.object = event.new

    mag_state.param.watch(_update_plot, "preview_plot")
    vobs_state.param.watch(_update_plot, "preview_plot")
    mag_state.param.watch(_on_mag_code_update, "code_snippet")
    vobs_state.param.watch(_on_vobs_code_update, "code_snippet")
    mag_state.param.watch(_on_mag_preview_update, "preview_dataset_html")
    vobs_state.param.watch(_on_vobs_preview_update, "preview_dataset_html")


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


def _build_dashboard(mag_state: MagState, vobs_state: VobsState) -> pn.template.FastListTemplate:
    collection_tabs = _build_collection_tabs(mag_state, vobs_state)
    mag_params = _build_mag_parameters(mag_state)
    vobs_params = _build_vobs_parameters(vobs_state)

    collection_section = pn.Column(
        pn.pane.Markdown("**Select collection**", margin=(0, 0, 8, 0)),
        collection_tabs,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["collection"],
    )
    parameters_section = pn.Column(
        pn.pane.Markdown("**Select parameters**", margin=(0, 0, 8, 0)),
        mag_params,
        vobs_params,
        sizing_mode="stretch_width",
        styles=_SECTION_STYLES["parameters"],
    )

    plot_measurements_selector = pn.Param(
        mag_state,
        parameters=["plot_measurements"],
        widgets={"plot_measurements": {"type": pn.widgets.MultiSelect, "size": 5}},
        show_name=False,
        sizing_mode="stretch_width",
    )
    vobs_plot_measurements_selector = pn.Param(
        vobs_state,
        parameters=["plot_measurements"],
        widgets={"plot_measurements": {"type": pn.widgets.MultiSelect, "size": 5}},
        show_name=False,
        sizing_mode="stretch_width",
        visible=False,
    )
    plot_selector_container = pn.Column(
        pn.pane.Markdown("**Measurement to plot:**", margin=(0, 0, 4, 0), styles={"font-size": "12px"}),
        plot_measurements_selector,
        vobs_plot_measurements_selector,
        sizing_mode="stretch_width",
    )

    code_section, code_editor = _build_code_editor(mag_state.code_snippet)
    preview_section, html_pane, plot_pane = _build_preview_section(
        mag_state.preview_dataset_html,
        plot_selector_container,
    )

    _setup_tab_watchers(
        collection_tabs,
        mag_state,
        vobs_state,
        mag_params,
        vobs_params,
        code_editor,
        html_pane,
        plot_pane,
        plot_measurements_selector,
        vobs_plot_measurements_selector,
    )

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

mag_state = MagState()
vobs_state = VobsState()
dashboard = _build_dashboard(mag_state, vobs_state)
dashboard.servable()
