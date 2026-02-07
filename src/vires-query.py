import asyncio
import datetime as dt
from typing import Dict, List, Optional, Tuple

import panel as pn
import param
from viresclient import SwarmRequest

pn.extension("codeeditor")
if pn.state.curdoc is not None:
    pn.state.curdoc.title = "VirES Query Builder"

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


def _load_metadata() -> tuple[Dict[str, List[str]], Dict[str, List[str]], List[str], List[str], Dict[str, str]]:
    vires = SwarmRequest()
    collection_map = vires.available_collections(details=False)
    measurements_by_collection = {
        collection_type: vires.available_measurements(collection_type)
        for collection_type in collection_map.keys()
    }
    auxiliaries = [
        aux
        for aux in vires.available_auxiliaries()
        if aux not in {"Timestamp", "Latitude", "Longitude", "Radius", "Spacecraft"}
    ]
    mag_models = vires.available_models(details=False)
    collections_to_types = vires._available["collections_to_keys"]
    return collection_map, measurements_by_collection, auxiliaries, mag_models, collections_to_types


COLLECTION_MAP, MEASUREMENTS_BY_COLLECTION, AUXILIARIES, MAG_MODELS, COLLECTIONS_TO_TYPES = _load_metadata()


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
    return data.as_xarray()


_TIME_EXTENT_CACHE: Dict[str, Optional[Tuple[str, str]]] = {}


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


class ViresParameters(param.Parameterized):
    collection_type = param.Selector(default="MAG", objects=list(COLLECTION_MAP.keys()))
    collection = param.Selector(objects=COLLECTION_MAP["MAG"])
    measurements = param.ListSelector(default=[], objects=MEASUREMENTS_BY_COLLECTION["MAG"])
    magnetic_model = param.Selector(default="", objects=[""] + MAG_MODELS)
    auxiliaries = param.ListSelector(default=[], objects=AUXILIARIES)
    time_range = param.DateRange(default=(dt.datetime(2024, 3, 1), dt.datetime(2024, 3, 1, 0, 1)))
    time_extent_label = param.String("")
    code_snippet = param.String("")
    preview_dataset_html = param.String("")

    mission = param.Selector(default="Swarm", objects=list(MAG_COLLECTIONS.keys()))
    spacecraft = param.Selector()
    variant = param.Selector()
    mag_collection = param.String()

    @param.depends("collection_type", watch=True)
    def _update_collections_and_measurements(self) -> None:
        self.measurements = []
        collections = COLLECTION_MAP[self.collection_type]
        self.param["collection"].objects = collections
        self.collection = collections[0]
        measurements = MEASUREMENTS_BY_COLLECTION[self.collection_type]
        self.param["measurements"].objects = measurements

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
            self.preview_dataset_html = ds._repr_html_()

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
    def _update_mag_collection(self) -> None:
        self.mag_collection = MAG_COLLECTIONS[self.mission][self.spacecraft][self.variant]

    @param.depends("mission", "spacecraft", "variant", "mag_collection", watch=True, on_init=True)
    def _update_collections(self) -> None:
        self.collection_type = COLLECTIONS_TO_TYPES[self.mag_collection]
        self.collection = self.mag_collection


def _build_dashboard(state: ViresParameters) -> pn.FlexBox:
    html_pane = pn.pane.HTML(
        state.preview_dataset_html,
        sizing_mode="stretch_both",
        min_height=300,
    )

    def _update_html(event: param.parameterized.Event) -> None:
        html_pane.object = event.new

    state.param.watch(_update_html, "preview_dataset_html")

    generic_tabs = pn.layout.Tabs(
        pn.Param(
            state,
            parameters=["collection_type", "collection"],
            widgets={
                "collection": {"type": pn.widgets.Select, "size": 6},
            },
            name="Generic",
            sizing_mode="stretch_width",
        ),
        pn.Param(
            state,
            parameters=["mission", "spacecraft", "variant", "mag_collection"],
            name="Magnetic (space)",
            sizing_mode="stretch_width",
        ),
        sizing_mode="stretch_width",
    )

    measurements_tab = pn.Param(
        state,
        parameters=["measurements"],
        widgets={
            "measurements": {"type": pn.widgets.CheckBoxGroup},
        },
        name="Measurements",
        sizing_mode="stretch_width",
    )

    auxiliaries_tab = pn.Param(
        state,
        parameters=["magnetic_model", "auxiliaries"],
        widgets={
            "auxiliaries": {"type": pn.widgets.CheckBoxGroup},
        },
        name="Auxiliaries",
        sizing_mode="stretch_width",
    )

    selection_tabs = pn.layout.Tabs(
        measurements_tab,
        auxiliaries_tab,
        sizing_mode="stretch_width",
    )

    time_range_hint = pn.pane.Markdown(
        state.time_extent_label,
        sizing_mode="stretch_width",
        margin=(4, 0, 0, 0),
        styles={"color": "#374151", "font-size": "12px"},
    )

    def _update_time_extent(event: param.parameterized.Event) -> None:
        time_range_hint.object = event.new

    state.param.watch(_update_time_extent, "time_extent_label")

    time_range = pn.Param(
        state,
        parameters=["time_range"],
        widgets={
            "time_range": {"type": pn.widgets.DatetimeRangePicker},
        },
        show_name=False,
        sizing_mode="stretch_width",
    )

    code_snippet = pn.Param(
        state,
        parameters=["code_snippet"],
        widgets={
            "code_snippet": {
                "type": pn.widgets.CodeEditor,
                "height": 360,
                "language": "python",
                "readonly": True,
                "print_margin": False,
                "sizing_mode": "stretch_width",
            },
        },
        name="Code",
        sizing_mode="stretch_width",
    )

    collection_section = pn.Column(
        pn.pane.Markdown("**Select collection**", margin=(0, 0, 8, 0)),
        generic_tabs,
        sizing_mode="stretch_width",
        styles={
            "border": "1px solid #c7d2fe",
            "background": "#eef2ff",
            "border-radius": "8px",
            "padding": "12px",
        },
    )

    parameters_section = pn.Column(
        pn.pane.Markdown("**Select paramters**", margin=(0, 0, 8, 0)),
        time_range,
        time_range_hint,
        selection_tabs,
        sizing_mode="stretch_width",
        styles={
            "border": "1px solid #a7f3d0",
            "background": "#ecfdf3",
            "border-radius": "8px",
            "padding": "12px",
        },
    )

    controls_column = pn.Column(
        collection_section,
        parameters_section,
        sizing_mode="fixed",
        width=480,
        margin=(0, 12, 0, 0),
    )

    preview_column = pn.Column(
        code_snippet,
        html_pane,
        sizing_mode="stretch_both",
        min_width=360,
    )

    return pn.Row(
        controls_column,
        preview_column,
        sizing_mode="stretch_both",
    )


parameter_state = ViresParameters()
dashboard = _build_dashboard(parameter_state)
dashboard.servable()
