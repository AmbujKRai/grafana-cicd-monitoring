"""Generates the Grafana dashboards in monitoring/grafana/dashboards (dashboards as code).

Panels are described with small helper functions so that every dashboard shares the
same colours, units and layout conventions. Run after changing a panel:

    python scripts/build_dashboards.py

Grafana picks up the regenerated JSON files within 30 seconds (provisioning).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "monitoring" / "grafana" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}

GREEN, RED, ORANGE, YELLOW, BLUE, PURPLE, GREY = (
    "green",
    "red",
    "orange",
    "yellow",
    "blue",
    "purple",
    "#8e8e8e",
)
RESULT_COLORS = {"success": GREEN, "failure": RED, "cancelled": GREY, "timed_out": ORANGE, "skipped": GREY}
SEVERITY_COLORS = {"critical": "dark-red", "high": RED, "medium": ORANGE, "low": YELLOW, "any": RED}

GH_RESULT_MAPPING = [
    {
        "type": "value",
        "options": {"1": {"text": "PASSING", "color": GREEN}, "0": {"text": "FAILING", "color": RED}},
    }
]
JENKINS_RESULT_MAPPING = [
    {
        "type": "value",
        "options": {
            "0": {"text": "SUCCESS", "color": GREEN},
            "1": {"text": "UNSTABLE", "color": YELLOW},
            "2": {"text": "FAILURE", "color": RED},
            "3": {"text": "NOT BUILT", "color": GREY},
            "4": {"text": "ABORTED", "color": GREY},
        },
    }
]
PASS_FAIL_MAPPING = [
    {"type": "value", "options": {"1": {"text": "PASS", "color": GREEN}, "0": {"text": "FAIL", "color": RED}}}
]
UP_DOWN_MAPPING = [
    {"type": "value", "options": {"1": {"text": "UP", "color": GREEN}, "0": {"text": "DOWN", "color": RED}}}
]


def steps(*pairs, base=GREEN):
    """Threshold steps: steps((value, colour), ...) on top of a base colour."""
    return {
        "mode": "absolute",
        "steps": [{"color": base, "value": None}] + [{"color": c, "value": v} for v, c in pairs],
    }


def q(expr, legend="__auto", ref="A", instant=False, fmt=None, interval=None):
    target = {
        "datasource": DS,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "instant": instant,
        "refId": ref,
    }
    if fmt:
        target["format"] = fmt
    if interval:
        target["interval"] = interval
    return target


def color_overrides(colors: dict) -> list:
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in colors.items()
    ]


class Board:
    """Collects panels and lays them out on Grafana's 24-column grid."""

    def __init__(self, uid, title, description, tags, time_from="now-6h", refresh="30s"):
        self.uid, self.title, self.description = uid, title, description
        self.tags, self.time_from, self.refresh = tags, time_from, refresh
        self.panels, self.variables, self.annotations = [], [], []
        self.x = self.y = self.row_height = 0
        self.next_id = 1

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_height
            self.row_height = 0
        panel["id"] = self.next_id
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.next_id += 1
        self.panels.append(panel)
        self.x += w
        self.row_height = max(self.row_height, h)
        return panel["id"]

    def row(self, title):
        self.x, self.y = 0, self.y + self.row_height
        self.row_height = 0
        self.add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
        self.x, self.y, self.row_height = 0, self.y + 1, 0

    def variable(self, name, label, query=None, custom=None, default=None, hide=0):
        if custom:
            options = [{"text": v, "value": v, "selected": v == default} for v in custom]
            var = {
                "type": "custom",
                "name": name,
                "label": label,
                "query": ",".join(custom),
                "options": options,
                "current": {"text": default, "value": default},
                "hide": hide,
            }
        else:
            var = {
                "type": "query",
                "name": name,
                "label": label,
                "datasource": DS,
                "query": query,
                "definition": query,
                "refresh": 2,
                "sort": 1,
                "includeAll": False,
                "multi": False,
                "hide": hide,
            }
            if default:
                var["current"] = {"text": default, "value": default}
        self.variables.append(var)

    def annotation(self, name, expr, color, title):
        self.annotations.append(
            {
                "name": name,
                "datasource": DS,
                "enable": True,
                "iconColor": color,
                "expr": expr,
                "step": "30s",
                "titleFormat": title,
                "useValueForTime": False,
            }
        )

    def to_json(self):
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "timezone": "browser",
            "editable": False,
            "graphTooltip": 1,
            "refresh": self.refresh,
            "schemaVersion": 41,
            "version": 1,
            "time": {"from": self.time_from, "to": "now"},
            "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m", "15m"]},
            "templating": {"list": self.variables},
            "annotations": {
                "list": [
                    {
                        "builtIn": 1,
                        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                        "enable": True,
                        "hide": True,
                        "iconColor": "rgba(0, 211, 255, 1)",
                        "name": "Annotations & Alerts",
                        "type": "dashboard",
                    },
                    *self.annotations,
                ]
            },
            "links": [
                {
                    "title": "CI/CD dashboards",
                    "type": "dashboards",
                    "tags": ["ci-cd"],
                    "asDropdown": False,
                    "includeVars": False,
                    "keepTime": True,
                    "icon": "external link",
                }
            ],
            "panels": self.panels,
        }


# -- panel helpers ----------------------------------------------------------------
def stat(
    title,
    targets,
    unit="short",
    thresholds=None,
    mappings=None,
    decimals=None,
    description="",
    color_mode="background",
    graph="none",
    text_mode="auto",
    no_value=None,
    links=None,
    calc="lastNotNull",
):
    defaults = {
        "unit": unit,
        "color": {"mode": "thresholds"},
        "thresholds": thresholds or steps(base=BLUE),
        "mappings": mappings or [],
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value is not None:
        defaults["noValue"] = no_value
    if links:
        defaults["links"] = links
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": [calc], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": text_mode,
            "colorMode": color_mode,
            "graphMode": graph,
            "justifyMode": "center",
            "wideLayout": True,
            "showPercentChange": False,
        },
    }


def gauge(
    title, targets, unit="percentunit", minimum=0, maximum=1, thresholds=None, description="", decimals=None
):
    defaults = {
        "unit": unit,
        "min": minimum,
        "max": maximum,
        "color": {"mode": "thresholds"},
        "thresholds": thresholds or steps(base=BLUE),
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "gauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
            "orientation": "auto",
            "sizing": "auto",
        },
    }


def timeseries(
    title,
    targets,
    unit="short",
    draw="line",
    stack=False,
    colors=None,
    description="",
    fill=15,
    decimals=None,
    minimum=0,
    legend_calcs=None,
):
    defaults = {
        "unit": unit,
        "min": minimum,
        "color": {"mode": "palette-classic"},
        "custom": {
            "drawStyle": draw,
            "lineWidth": 2,
            "lineInterpolation": "smooth",
            "fillOpacity": 80 if draw == "bars" else fill,
            "gradientMode": "opacity" if draw == "line" else "none",
            "showPoints": "never",
            "spanNulls": True,
            "barAlignment": 0,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
            "axisBorderShow": False,
        },
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": color_overrides(colors or {})},
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
                "calcs": legend_calcs or [],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def per_run_transform(row_field, column_field, x_name):
    """Instant table -> one row per run (sorted numerically) with one column per category."""
    matrix = f"{row_field}\\{column_field}"
    return [
        {
            "id": "groupingToMatrix",
            "options": {
                "rowField": row_field,
                "columnField": column_field,
                "valueField": "Value",
                "emptyValue": "null",
            },
        },
        {
            "id": "convertFieldType",
            "options": {"conversions": [{"targetField": matrix, "destinationType": "number"}], "fields": {}},
        },
        {"id": "sortBy", "options": {"fields": {}, "sort": [{"field": matrix, "desc": False}]}},
        {
            "id": "convertFieldType",
            "options": {"conversions": [{"targetField": matrix, "destinationType": "string"}], "fields": {}},
        },
        {"id": "organize", "options": {"renameByName": {matrix: x_name}}},
    ]


def barchart(
    title,
    targets,
    transformations,
    x_field,
    unit="s",
    stack=True,
    colors=None,
    description="",
    thresholds=None,
    show_thresholds=False,
    legend=True,
    decimals=None,
):
    defaults = {
        "unit": unit,
        "min": 0,
        "color": {"mode": "palette-classic"},
        "custom": {
            "fillOpacity": 85,
            "gradientMode": "none",
            "lineWidth": 0,
            "axisBorderShow": False,
            "thresholdsStyle": {"mode": "dashed" if show_thresholds else "off"},
        },
    }
    if thresholds:
        defaults["thresholds"] = thresholds
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "barchart",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "transformations": transformations,
        "fieldConfig": {
            "defaults": defaults,
            # run/build numbers are labels, not durations: show them as "#12"
            "overrides": [field_override(x_field, unit="prefix:#"), *color_overrides(colors or {})],
        },
        "options": {
            "orientation": "vertical",
            "xField": x_field,
            "stacking": "normal" if stack else "none",
            "showValue": "never",
            "groupWidth": 0.75,
            "barWidth": 0.85,
            "barRadius": 0.05,
            "xTickLabelRotation": 0,
            "xTickLabelSpacing": 0,
            "fullHighlight": False,
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": legend, "calcs": []},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
    }


def bargauge(
    title,
    targets,
    unit="s",
    thresholds=None,
    description="",
    display="gradient",
    colors=None,
    decimals=None,
    maximum=None,
    color_mode="thresholds",
):
    defaults = {
        "unit": unit,
        "min": 0,
        "color": {"mode": color_mode},
        "thresholds": thresholds or steps(base=BLUE),
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if maximum is not None:
        defaults["max"] = maximum
    return {
        "type": "bargauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": color_overrides(colors or {})},
        "options": {
            "orientation": "horizontal",
            "displayMode": display,
            "valueMode": "color",
            "namePlacement": "left",
            "showUnfilled": True,
            "sizing": "manual",
            "minVizHeight": 16,
            "maxVizHeight": 28,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def table(title, targets, transformations, overrides, description="", sort_by=None):
    return {
        "type": "table",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "transformations": transformations,
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}},
            "overrides": overrides,
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "fields": ""},
            "sortBy": sort_by or [],
        },
    }


def piechart(title, targets, colors=None, description=""):
    return {
        "type": "piechart",
        "title": title,
        "description": description,
        "datasource": DS,
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": "short", "color": {"mode": "palette-classic"}},
            "overrides": color_overrides(colors or {}),
        },
        "options": {
            "pieType": "donut",
            "displayLabels": ["percent"],
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "showLegend": True,
                "values": ["value", "percent"],
            },
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
    }


def text(title, content):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}}


def field_override(name, **props):
    mapping = {
        "displayName": "displayName",
        "unit": "unit",
        "width": "custom.width",
        "hidden": "custom.hidden",
        "mappings": "mappings",
        "cell": "custom.cellOptions",
        "links": "links",
        "decimals": "decimals",
        "thresholds": "thresholds",
        "color": "color",
    }
    return {
        "matcher": {"id": "byName", "options": name},
        "properties": [{"id": mapping[k], "value": v} for k, v in props.items()],
    }


# -- shared queries ----------------------------------------------------------------
WF = 'workflow="$workflow"'
GH_SUCCESS_RATE = (
    f'sum(cicd_workflow_runs_window{{{WF},window="$window",conclusion="success"}})'
    f' / sum(cicd_workflow_runs_window{{{WF},window="$window",conclusion=~"success|failure"}})'
)
JENKINS_SUCCESS = 'sum(default_jenkins_builds_success_build_count_total{jenkins_job=~"$job"}) or vector(0)'
JENKINS_TOTAL = 'sum(default_jenkins_builds_total_build_count_total{jenkins_job=~"$job"})'

GH_RUN_DURATION = q(
    "label_replace(cicd_run_duration_seconds{"
    + WF
    + '}, "conclusion", "failure", "conclusion", "timed_out")',
    instant=True,
    fmt="table",
)
JENKINS_BUILD_DURATION = q(
    'label_replace(default_jenkins_builds_build_duration_milliseconds{jenkins_job=~"$job"} / 1000'
    ' and on(jenkins_job, number) default_jenkins_builds_build_result_ordinal == 0, "result", "success", "", "")'
    ' or label_replace(default_jenkins_builds_build_duration_milliseconds{jenkins_job=~"$job"} / 1000'
    ' and on(jenkins_job, number) default_jenkins_builds_build_result_ordinal > 0, "result", "failure", "", "")',
    instant=True,
    fmt="table",
)

DORA_HELP = (
    "DORA benchmark (Accelerate State of DevOps 2021): elite teams deploy on demand (several times a day), "
    "have a lead time and time to restore under one hour and a change failure rate of 0-15%."
)


def dora_row(board: Board):
    board.row("DORA metrics (GitHub Actions -> staging)")
    board.add(
        stat(
            "Deployment frequency",
            [q("cicd_dora_deployment_frequency_per_day", instant=True)],
            unit="suffix: / day",
            decimals=1,
            thresholds=steps((1 / 30, YELLOW), (1 / 7, BLUE), (1, GREEN), base=RED),
            description="Successful staging deployments per day over the last 7 days. " + DORA_HELP,
            no_value="no deployments",
        ),
        6,
        5,
    )
    board.add(
        stat(
            "Lead time for changes",
            [q("cicd_dora_lead_time_for_changes_seconds", instant=True)],
            unit="s",
            thresholds=steps((3600, BLUE), (86400, YELLOW), (7 * 86400, RED)),
            description="Median time from commit to a successful staging deployment (last 30 days). "
            + DORA_HELP,
            no_value="n/a",
        ),
        6,
        5,
    )
    board.add(
        stat(
            "Change failure rate",
            [q("cicd_dora_change_failure_rate_ratio", instant=True)],
            unit="percentunit",
            decimals=0,
            thresholds=steps((0.15, YELLOW), (0.30, RED)),
            description="Share of main-branch pipeline runs that failed in the last 7 days. " + DORA_HELP,
            no_value="0%",
        ),
        6,
        5,
    )
    board.add(
        stat(
            "Time to restore",
            [q("cicd_dora_time_to_restore_seconds", instant=True)],
            unit="s",
            thresholds=steps((3600, YELLOW), (86400, RED)),
            description="Mean time from a failed main-branch run to the next green run (last 30 days). "
            + DORA_HELP,
            no_value="no incidents",
        ),
        6,
        5,
    )


# -- dashboards -----------------------------------------------------------------------
def overview() -> Board:
    b = Board(
        "cicd-overview",
        "CI/CD Pipeline Overview",
        "Single pane of glass for the GitHub Actions (cloud) and Jenkins (self-hosted) pipelines.",
        ["ci-cd", "overview"],
        time_from="now-3h",
    )
    b.variable(
        "workflow",
        "GitHub workflow",
        "label_values(cicd_workflow_runs_total, workflow)",
        default="CI/CD Pipeline",
    )
    b.variable(
        "job",
        "Jenkins job",
        "label_values(default_jenkins_builds_last_build_result_ordinal, jenkins_job)",
        default="taskflow-api",
    )
    b.variable("window", "Window", custom=["24h", "7d", "30d"], default="7d", hide=2)
    b.annotation(
        "Deployments",
        'changes(cicd_deployments_total{conclusion="success"}[1m]) > 0',
        GREEN,
        "Deployed to staging",
    )
    b.annotation(
        "Failed runs",
        'changes(cicd_workflow_runs_total{conclusion="failure"}[1m]) > 0',
        RED,
        "{{workflow}} failed on {{branch}}",
    )

    b.row("Pipeline health")
    b.add(
        stat(
            "GitHub Actions - main",
            [q(f'cicd_workflow_last_run_status{{{WF},branch="main"}}', instant=True)],
            mappings=GH_RESULT_MAPPING,
            thresholds=steps((1, GREEN), base=RED),
            description="Result of the latest completed run of the selected workflow on main.",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Jenkins - last build",
            [q('default_jenkins_builds_last_build_result_ordinal{jenkins_job=~"$job"}', instant=True)],
            mappings=JENKINS_RESULT_MAPPING,
            thresholds=steps((1, YELLOW), (2, RED), base=GREEN),
            description="Result of the latest Jenkins build (Prometheus metrics plugin).",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "GitHub success rate (7d)",
            [q(GH_SUCCESS_RATE, instant=True)],
            unit="percentunit",
            decimals=0,
            thresholds=steps((0.8, YELLOW), (0.95, GREEN), base=RED),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Jenkins success rate",
            [q(f"({JENKINS_SUCCESS}) / {JENKINS_TOTAL}", instant=True)],
            unit="percentunit",
            decimals=0,
            thresholds=steps((0.8, YELLOW), (0.95, GREEN), base=RED),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Builds in the last 24h",
            [
                q(
                    'sum(cicd_workflow_runs_window{window="24h"}) + '
                    "(count(default_jenkins_builds_build_start_time_milliseconds > (time() - 86400) * 1000) or vector(0))",
                    instant=True,
                )
            ],
            description="GitHub Actions runs plus Jenkins builds started in the last 24 hours.",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Running now",
            [
                q(
                    'sum(cicd_workflow_runs_active{status="in_progress"}) + (sum(default_jenkins_executors_busy) or vector(0))',
                    instant=True,
                )
            ],
            thresholds=steps((1, ORANGE), base=GREY),
            description="Workflow runs in progress plus busy Jenkins executors.",
        ),
        4,
        4,
    )

    dora_row(b)

    b.row("Build performance")
    b.add(
        barchart(
            "GitHub Actions - build duration per run",
            [GH_RUN_DURATION],
            per_run_transform("run_number", "conclusion", "Run"),
            "Run",
            colors=RESULT_COLORS,
            description="Wall-clock duration of each recent run, coloured by outcome.",
        ),
        12,
        9,
    )
    b.add(
        barchart(
            "Jenkins - build duration per build",
            [JENKINS_BUILD_DURATION],
            per_run_transform("number", "result", "Build"),
            "Build",
            colors=RESULT_COLORS,
            description="Duration of each Jenkins build (per-build metrics of the Prometheus plugin).",
        ),
        12,
        9,
    )

    b.add(
        timeseries(
            "Pipeline activity",
            [
                q(
                    f"sum by (conclusion) (increase(cicd_workflow_runs_total{{{WF}}}[$__interval]))",
                    "GitHub {{conclusion}}",
                    "A",
                    interval="2m",
                ),
                q(
                    'sum(increase(default_jenkins_builds_success_build_count_total{jenkins_job=~"$job"}[$__interval]))',
                    "Jenkins success",
                    "B",
                    interval="2m",
                ),
                q(
                    'sum(increase(default_jenkins_builds_failed_build_count_total{jenkins_job=~"$job"}[$__interval]))',
                    "Jenkins failure",
                    "C",
                    interval="2m",
                ),
            ],
            draw="bars",
            stack=True,
            colors={
                "GitHub success": GREEN,
                "GitHub failure": RED,
                "GitHub cancelled": GREY,
                "Jenkins success": "#56A64B",
                "Jenkins failure": "#C4162A",
            },
            description="Completed builds per interval from both CI systems (live, since Prometheus started).",
        ),
        12,
        8,
    )
    b.add(
        table(
            "CI systems side by side",
            [
                q(
                    "sum by (ci_system, pipeline) (ci:build_success:ratio)",
                    instant=True,
                    fmt="table",
                    ref="A",
                ),
                q(
                    "sum by (ci_system, pipeline) (ci:build_duration_seconds:avg)",
                    instant=True,
                    fmt="table",
                    ref="B",
                ),
                q(
                    "sum by (ci_system, pipeline) (ci:build_last_duration_seconds)",
                    instant=True,
                    fmt="table",
                    ref="C",
                ),
                q("sum by (ci_system, pipeline) (ci:builds:total)", instant=True, fmt="table", ref="D"),
            ],
            [
                {"id": "merge", "options": {}},
                {
                    "id": "organize",
                    "options": {
                        "excludeByName": {"Time": True},
                        "indexByName": {
                            "ci_system": 0,
                            "pipeline": 1,
                            "Value #D": 2,
                            "Value #A": 3,
                            "Value #B": 4,
                            "Value #C": 5,
                        },
                        "renameByName": {
                            "ci_system": "CI system",
                            "pipeline": "Pipeline",
                            "Value #A": "Success rate",
                            "Value #B": "Avg duration",
                            "Value #C": "Last duration",
                            "Value #D": "Builds",
                        },
                    },
                },
            ],
            [
                field_override(
                    "Success rate",
                    unit="percentunit",
                    decimals=0,
                    cell={"type": "color-text"},
                    thresholds=steps((0.8, YELLOW), (0.95, GREEN), base=RED),
                ),
                field_override("Avg duration", unit="s"),
                field_override("Last duration", unit="s"),
            ],
            description="Normalised by Prometheus recording rules (ci:* series) so both CI systems use the same units.",
        ),
        12,
        8,
    )

    b.row("Where the time goes")
    b.add(
        bargauge(
            "GitHub Actions - average stage duration",
            [
                q(
                    f"sum by (stage) (cicd_stage_duration_seconds_sum{{{WF}}}) / sum by (stage) (cicd_stage_duration_seconds_count{{{WF}}})",
                    "{{stage}}",
                    instant=True,
                )
            ],
            thresholds=steps((60, YELLOW), (180, RED)),
            description="Average duration of each job (stage).",
        ),
        12,
        7,
    )
    b.add(
        bargauge(
            "Jenkins - stage duration (last build)",
            [
                q(
                    'default_jenkins_builds_last_stage_duration_milliseconds_summary_sum{jenkins_job=~"$job"} / 1000',
                    "{{stage}}",
                    instant=True,
                )
            ],
            thresholds=steps((60, YELLOW), (180, RED)),
            description="Stage timings of the latest Jenkins build.",
        ),
        12,
        7,
    )
    return b


def gha_performance() -> Board:
    b = Board(
        "gha-performance",
        "GitHub Actions - Pipeline Performance",
        "Run, stage and step level performance of the GitHub Actions workflows (cicd-exporter).",
        ["ci-cd", "github-actions"],
    )
    b.variable("repo", "Repository", "label_values(cicd_exporter_info, repository)", hide=2)
    b.variable(
        "workflow", "Workflow", "label_values(cicd_workflow_runs_total, workflow)", default="CI/CD Pipeline"
    )
    b.variable("window", "Window", custom=["24h", "7d", "30d"], default="7d")
    b.annotation(
        "Failed runs",
        f'changes(cicd_workflow_runs_total{{{WF},conclusion="failure"}}[1m]) > 0',
        RED,
        "Run failed on {{branch}}",
    )

    b.row("Summary ($window)")
    b.add(stat("Runs", [q(f'sum(cicd_workflow_runs_window{{{WF},window="$window"}})', instant=True)]), 4, 4)
    b.add(
        stat(
            "Success rate",
            [q(GH_SUCCESS_RATE, instant=True)],
            unit="percentunit",
            decimals=0,
            thresholds=steps((0.8, YELLOW), (0.95, GREEN), base=RED),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Average duration",
            [q(f'cicd_workflow_duration_window_seconds{{{WF},window="$window",stat="avg"}}', instant=True)],
            unit="s",
            thresholds=steps((300, YELLOW), (600, RED)),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "p95 duration",
            [q(f'cicd_workflow_duration_window_seconds{{{WF},window="$window",stat="p95"}}', instant=True)],
            unit="s",
            thresholds=steps((300, YELLOW), (600, RED)),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Avg runner wait per job",
            [
                q(
                    f"sum(cicd_stage_queue_seconds_sum{{{WF}}}) / sum(cicd_stage_queue_seconds_count{{{WF}}})",
                    instant=True,
                )
            ],
            unit="s",
            thresholds=steps((30, YELLOW), (120, RED)),
            description="How long jobs waited for a GitHub-hosted runner after being queued.",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "In progress / queued",
            [
                q(
                    f'sum(cicd_workflow_runs_active{{{WF},status="in_progress"}})',
                    "running",
                    "A",
                    instant=True,
                ),
                q(f'sum(cicd_workflow_runs_active{{{WF},status="queued"}})', "queued", "B", instant=True),
            ],
            thresholds=steps((1, ORANGE), base=GREY),
            text_mode="value_and_name",
        ),
        4,
        4,
    )

    b.row("Run performance")
    b.add(
        barchart(
            "Duration per run",
            [GH_RUN_DURATION],
            per_run_transform("run_number", "conclusion", "Run"),
            "Run",
            colors=RESULT_COLORS,
        ),
        16,
        9,
    )
    b.add(
        piechart(
            "Outcomes ($window)",
            [
                q(
                    f'sum by (conclusion) (cicd_workflow_runs_window{{{WF},window="$window"}})',
                    "{{conclusion}}",
                    instant=True,
                )
            ],
            colors=RESULT_COLORS,
        ),
        8,
        9,
    )
    b.add(
        barchart(
            "Stage breakdown per run",
            [q(f"cicd_run_stage_duration_seconds{{{WF}}}", instant=True, fmt="table")],
            per_run_transform("run_number", "stage", "Run"),
            "Run",
            description="Stacked job (stage) durations for each recent run - shows which stage made a run slow.",
        ),
        16,
        9,
    )
    b.add(
        bargauge(
            "Average stage duration",
            [
                q(
                    f"sum by (stage) (cicd_stage_duration_seconds_sum{{{WF}}}) / sum by (stage) (cicd_stage_duration_seconds_count{{{WF}}})",
                    "{{stage}}",
                    instant=True,
                )
            ],
            thresholds=steps((60, YELLOW), (180, RED)),
        ),
        8,
        9,
    )

    b.row("Bottlenecks and failures")
    b.add(
        bargauge(
            "Slowest steps (latest run)",
            [q(f"topk(8, cicd_step_duration_seconds{{{WF}}})", "{{stage}} / {{step}}", instant=True)],
            thresholds=steps((30, YELLOW), (90, RED)),
        ),
        12,
        8,
    )
    b.add(
        bargauge(
            "Failures by stage",
            [
                q(
                    f'sum by (stage) (cicd_stage_runs_total{{{WF},conclusion="failure"}})',
                    "{{stage}}",
                    instant=True,
                )
            ],
            unit="short",
            thresholds=steps((1, RED), base=GREEN),
            display="basic",
            description="Which job fails most often (all tracked runs).",
        ),
        12,
        8,
    )

    run_link = [
        {
            "title": "Open run in GitHub",
            "url": "https://github.com/${repo}/actions/runs/${__data.fields.run_id}",
            "targetBlank": True,
        }
    ]
    b.add(
        table(
            "Recent runs",
            [
                q(f"cicd_run_duration_seconds{{{WF}}}", instant=True, fmt="table", ref="A"),
                q(f"cicd_run_wait_seconds{{{WF}}}", instant=True, fmt="table", ref="B"),
            ],
            [
                {"id": "merge", "options": {}},
                {
                    "id": "convertFieldType",
                    "options": {
                        "conversions": [{"targetField": "run_number", "destinationType": "number"}],
                        "fields": {},
                    },
                },
                {
                    "id": "organize",
                    "options": {
                        "excludeByName": {
                            "Time": True,
                            "__name__": True,
                            "ci_system": True,
                            "instance": True,
                            "job": True,
                            "workflow": True,
                        },
                        "indexByName": {
                            "run_number": 0,
                            "title": 1,
                            "conclusion": 2,
                            "Value #A": 3,
                            "Value #B": 4,
                            "branch": 5,
                            "event": 6,
                            "actor": 7,
                            "commit": 8,
                            "run_id": 9,
                        },
                        "renameByName": {
                            "run_number": "Run",
                            "title": "Commit message",
                            "conclusion": "Result",
                            "Value #A": "Duration",
                            "Value #B": "Runner wait",
                            "branch": "Branch",
                            "event": "Trigger",
                            "actor": "Actor",
                            "commit": "Commit",
                        },
                    },
                },
            ],
            [
                field_override("Run", width=60, links=run_link),
                field_override("Commit message", width=330),
                field_override("Duration", unit="s", width=100),
                field_override("Runner wait", unit="s", width=100),
                field_override(
                    "Result",
                    width=110,
                    cell={"type": "color-background", "mode": "basic"},
                    mappings=[
                        {
                            "type": "value",
                            "options": {
                                "success": {"text": "success", "color": GREEN},
                                "failure": {"text": "failure", "color": RED},
                                "cancelled": {"text": "cancelled", "color": GREY},
                            },
                        }
                    ],
                ),
                field_override("run_id", hidden=True),
            ],
            description="Latest runs of the workflow. Click a run number to open it on GitHub.",
            sort_by=[{"displayName": "Run", "desc": True}],
        ),
        24,
        10,
    )

    b.row("Live activity")
    b.add(
        timeseries(
            "Active runs",
            [q(f"sum by (status) (cicd_workflow_runs_active{{{WF}}})", "{{status}}")],
            stack=True,
            colors={"in_progress": ORANGE, "queued": PURPLE},
        ),
        12,
        7,
    )
    b.add(
        timeseries(
            "Rolling 24h duration (p50 / p95)",
            [
                q(f'cicd_workflow_duration_window_seconds{{{WF},window="24h",stat="p50"}}', "p50", "A"),
                q(f'cicd_workflow_duration_window_seconds{{{WF},window="24h",stat="p95"}}', "p95", "B"),
            ],
            unit="s",
            colors={"p50": BLUE, "p95": ORANGE},
        ),
        12,
        7,
    )
    return b


def build_quality() -> Board:
    b = Board(
        "build-quality",
        "Build Quality & Security",
        "Test, coverage, lint and security-scan results published by every pipeline run (ci-metrics branch).",
        ["ci-cd", "quality", "security"],
    )
    b.variable("workflow", "Workflow", "label_values(cicd_build_info, workflow)", default="CI/CD Pipeline")

    b.row("Latest build")
    b.add(
        stat(
            "Build",
            [q(f"cicd_build_info{{{WF}}}", "Run #{{run_number}} ({{commit}})", instant=True)],
            text_mode="name",
            color_mode="none",
            description="Run that the quality metrics below come from.",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Tests passed",
            [q(f'cicd_build_tests{{{WF},result="passed"}}', instant=True)],
            thresholds=steps(base=GREEN),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Tests failed",
            [q(f'sum(cicd_build_tests{{{WF},result=~"failed|errors"}})', instant=True)],
            thresholds=steps((1, RED), base=GREEN),
        ),
        4,
        4,
    )
    b.add(
        gauge(
            "Line coverage",
            [q(f"cicd_build_coverage_ratio{{{WF}}}", instant=True)],
            thresholds=steps((0.8, YELLOW), (0.9, GREEN), base=RED),
            decimals=1,
            description="Pipeline gate: at least 80% line coverage.",
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Image size",
            [q(f"cicd_build_image_size_bytes{{{WF}}}", instant=True)],
            unit="decbytes",
            thresholds=steps((200e6, YELLOW), (500e6, RED)),
            decimals=1,
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Smoke-test latency",
            [q(f"cicd_build_smoke_test_latency_ms{{{WF}}}", instant=True)],
            unit="ms",
            thresholds=steps((100, YELLOW), (500, RED)),
            decimals=1,
            description="Average response time of the staging smoke-test requests.",
        ),
        4,
        4,
    )

    b.row("Quality gates and findings")
    b.add(
        stat(
            "Quality gates",
            [q(f"cicd_build_quality_gate{{{WF}}}", "{{gate}}", instant=True)],
            mappings=PASS_FAIL_MAPPING,
            thresholds=steps((1, GREEN), base=RED),
            text_mode="value_and_name",
            description="tests: no failures | coverage: >= 80% | sast: no HIGH Bandit findings | "
            "sca: no vulnerable dependencies | container: no fixable CRITICAL CVEs",
        ),
        9,
        6,
    )
    b.add(
        bargauge(
            "Container image CVEs (Trivy)",
            [q(f'cicd_build_vulnerabilities{{{WF},scanner="trivy"}}', "{{severity}}", instant=True)],
            unit="short",
            colors=SEVERITY_COLORS,
            color_mode="fixed",
            display="basic",
        ),
        8,
        6,
    )
    b.add(
        stat(
            "Code & dependency findings",
            [
                q(
                    f'sum(cicd_build_vulnerabilities{{{WF},scanner="bandit"}})',
                    "Bandit (SAST)",
                    "A",
                    instant=True,
                ),
                q(
                    f'sum(cicd_build_vulnerabilities{{{WF},scanner="pip-audit"}})',
                    "pip-audit (SCA)",
                    "B",
                    instant=True,
                ),
                q(f"cicd_build_lint_issues{{{WF}}}", "Ruff lint", "C", instant=True),
            ],
            thresholds=steps((1, RED), base=GREEN),
            text_mode="value_and_name",
        ),
        7,
        6,
    )

    b.row("Trends per run")
    b.add(
        barchart(
            "Line coverage per run",
            [q(f"cicd_run_coverage_ratio{{{WF}}}", instant=True, fmt="table")],
            [
                *per_run_transform("run_number", "workflow", "Run"),
                {"id": "organize", "options": {"renameByName": {"$workflow": "coverage"}}},
            ],
            "Run",
            unit="percentunit",
            stack=False,
            colors={"CI/CD Pipeline": BLUE},
            thresholds=steps((0.8, GREEN), base=RED),
            show_thresholds=True,
            legend=False,
        ),
        12,
        8,
    )
    b.add(
        barchart(
            "Test results per run",
            [q(f'cicd_run_tests{{{WF},result=~"passed|failed|errors"}}', instant=True, fmt="table")],
            per_run_transform("run_number", "result", "Run"),
            "Run",
            unit="short",
            colors={"passed": GREEN, "failed": RED, "errors": ORANGE},
        ),
        12,
        8,
    )
    b.add(
        barchart(
            "Security findings per run",
            [q(f"cicd_run_vulnerabilities{{{WF}}}", instant=True, fmt="table")],
            per_run_transform("run_number", "scanner", "Run"),
            "Run",
            unit="short",
            colors={"trivy": ORANGE, "bandit": PURPLE, "pip-audit": RED},
            description="Total findings per scanner (Trivy counts include unfixed OS package CVEs).",
        ),
        12,
        8,
    )
    b.add(
        barchart(
            "Image size per run",
            [q(f"cicd_run_image_size_bytes{{{WF}}}", instant=True, fmt="table")],
            per_run_transform("run_number", "workflow", "Run"),
            "Run",
            unit="decbytes",
            stack=False,
            colors={"CI/CD Pipeline": PURPLE},
            legend=False,
        ),
        12,
        8,
    )

    b.row("Jenkins tests (last build)")
    b.add(
        stat(
            "Jenkins tests",
            [
                q(
                    'default_jenkins_builds_last_build_tests_total{jenkins_job="taskflow-api"}',
                    "total",
                    "A",
                    instant=True,
                ),
                q(
                    'default_jenkins_builds_last_build_tests_failing{jenkins_job="taskflow-api"}',
                    "failing",
                    "B",
                    instant=True,
                ),
                q(
                    'default_jenkins_builds_last_last_build_tests_skipped{jenkins_job="taskflow-api"}',
                    "skipped",
                    "C",
                    instant=True,
                ),
            ],
            text_mode="value_and_name",
            thresholds=steps(base=BLUE),
        ),
        12,
        4,
    )
    return b


def jenkins() -> Board:
    b = Board(
        "jenkins-builds",
        "Jenkins - Build Metrics",
        "Build, stage, queue and executor metrics from the Jenkins Prometheus metrics plugin.",
        ["ci-cd", "jenkins"],
    )
    b.variable(
        "job",
        "Job",
        "label_values(default_jenkins_builds_last_build_result_ordinal, jenkins_job)",
        default="taskflow-api",
    )
    j = 'jenkins_job=~"$job"'

    b.row("Status")
    b.add(
        stat(
            "Jenkins",
            [q('up{job="jenkins"}', instant=True)],
            mappings=UP_DOWN_MAPPING,
            thresholds=steps((1, GREEN), base=RED),
        ),
        3,
        4,
    )
    b.add(
        stat(
            "Last build",
            [q(f"default_jenkins_builds_last_build_result_ordinal{{{j}}}", instant=True)],
            mappings=JENKINS_RESULT_MAPPING,
            thresholds=steps((1, YELLOW), (2, RED), base=GREEN),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Last build duration",
            [q(f"default_jenkins_builds_last_build_duration_milliseconds{{{j}}} / 1000", instant=True)],
            unit="s",
            thresholds=steps((180, YELLOW), (600, RED)),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Queue wait (last build)",
            [q(f"default_jenkins_builds_last_build_waiting_milliseconds{{{j}}} / 1000", instant=True)],
            unit="s",
            thresholds=steps((30, YELLOW), (120, RED)),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Builds (success / failed)",
            [
                q(
                    f"sum(default_jenkins_builds_success_build_count_total{{{j}}}) or vector(0)",
                    "success",
                    "A",
                    instant=True,
                ),
                q(
                    f"sum(default_jenkins_builds_failed_build_count_total{{{j}}}) or vector(0)",
                    "failed",
                    "B",
                    instant=True,
                ),
            ],
            text_mode="value_and_name",
            thresholds=steps(base=BLUE),
        ),
        5,
        4,
    )
    b.add(
        gauge(
            "Job health score",
            [q(f"default_jenkins_builds_health_score{{{j}}}", instant=True)],
            unit="percent",
            maximum=100,
            thresholds=steps((40, YELLOW), (80, GREEN), base=RED),
            description="Jenkins weather score: share of recent builds that succeeded.",
        ),
        4,
        4,
    )

    b.row("Builds")
    b.add(
        barchart(
            "Build duration per build",
            [JENKINS_BUILD_DURATION],
            per_run_transform("number", "result", "Build"),
            "Build",
            colors=RESULT_COLORS,
        ),
        14,
        9,
    )
    b.add(
        bargauge(
            "Stage duration (last build)",
            [
                q(
                    f"default_jenkins_builds_last_stage_duration_milliseconds_summary_sum{{{j}}} / 1000",
                    "{{stage}}",
                    instant=True,
                )
            ],
            thresholds=steps((60, YELLOW), (180, RED)),
        ),
        10,
        9,
    )
    b.add(
        barchart(
            "Queue wait per build",
            [
                q(
                    f"default_jenkins_builds_build_waiting_milliseconds{{{j}}} / 1000",
                    instant=True,
                    fmt="table",
                )
            ],
            per_run_transform("number", "jenkins_job", "Build"),
            "Build",
            stack=False,
            colors={"taskflow-api": PURPLE},
            legend=False,
            description="Time each build spent in the queue before an executor picked it up.",
        ),
        14,
        8,
    )
    b.add(
        stat(
            "Last build tests",
            [
                q(f"default_jenkins_builds_last_build_tests_total{{{j}}}", "total", "A", instant=True),
                q(f"default_jenkins_builds_last_build_tests_failing{{{j}}}", "failing", "B", instant=True),
                q(
                    f"default_jenkins_builds_last_last_build_tests_skipped{{{j}}}",
                    "skipped",
                    "C",
                    instant=True,
                ),
            ],
            text_mode="value_and_name",
            thresholds=steps(base=BLUE),
        ),
        10,
        5,
    )

    b.row("Executors (live)")
    b.add(
        timeseries(
            "Executors",
            [
                q("sum(default_jenkins_executors_busy)", "busy", "A"),
                q("sum(default_jenkins_executors_idle)", "idle", "B"),
                q("sum(default_jenkins_executors_queue_length)", "queue length", "C"),
            ],
            colors={"busy": ORANGE, "idle": GREEN, "queue length": RED},
        ),
        14,
        7,
    )
    b.add(
        timeseries(
            "Running build duration",
            [
                q(
                    f"default_jenkins_builds_running_build_duration_milliseconds{{{j}}} / 1000",
                    "{{jenkins_job}}",
                )
            ],
            unit="s",
            description="Elapsed time of the build that is currently running.",
        ),
        10,
        7,
    )
    return b


def stack_health() -> Board:
    b = Board(
        "stack-health",
        "Monitoring Stack Health",
        "Meta-monitoring: scrape targets, exporter polling and GitHub API quota, alert notifications.",
        ["ci-cd", "meta-monitoring"],
        time_from="now-3h",
    )
    b.row("Status")
    b.add(
        stat(
            "Targets up",
            [q("sum(up)", "up", "A", instant=True), q("count(up)", "total", "B", instant=True)],
            text_mode="value_and_name",
            thresholds=steps(base=GREEN),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Last GitHub poll",
            [q("time() - cicd_exporter_last_poll_timestamp_seconds", instant=True)],
            unit="s",
            thresholds=steps((300, YELLOW), (900, RED)),
            description="Seconds since the exporter last polled GitHub.",
        ),
        4,
        4,
    )
    b.add(
        gauge(
            "GitHub API quota left",
            [q("cicd_github_api_rate_limit_remaining / cicd_github_api_rate_limit_limit", instant=True)],
            thresholds=steps((0.1, YELLOW), (0.3, GREEN), base=RED),
            decimals=0,
        ),
        4,
        4,
    )
    b.add(stat("Tracked runs", [q("cicd_exporter_tracked_runs", instant=True)]), 4, 4)
    b.add(
        stat(
            "Poll errors",
            [q("cicd_exporter_poll_errors_total", instant=True)],
            thresholds=steps((1, ORANGE), base=GREEN),
        ),
        4,
        4,
    )
    b.add(
        stat(
            "Alert notifications",
            [q("sum(cicd_alert_notifications_total) or vector(0)", instant=True)],
            thresholds=steps((1, ORANGE), base=GREEN),
            description="Notifications delivered by Grafana to the webhook.",
        ),
        4,
        4,
    )

    b.row("Scraping")
    b.add(
        table(
            "Scrape targets",
            [q("up", instant=True, fmt="table")],
            [
                {
                    "id": "organize",
                    "options": {
                        "excludeByName": {"Time": True, "__name__": True},
                        "indexByName": {"job": 0, "instance": 1, "ci_system": 2, "Value": 3},
                        "renameByName": {
                            "job": "Job",
                            "instance": "Instance",
                            "ci_system": "CI system",
                            "Value": "State",
                        },
                    },
                }
            ],
            [
                field_override(
                    "State",
                    mappings=UP_DOWN_MAPPING,
                    cell={"type": "color-background", "mode": "basic"},
                    width=90,
                )
            ],
        ),
        10,
        8,
    )
    b.add(
        timeseries("Scrape duration", [q("scrape_duration_seconds", "{{job}}")], unit="s", decimals=3), 14, 8
    )

    b.row("Exporter and alerting")
    b.add(
        timeseries(
            "GitHub API quota remaining",
            [q("cicd_github_api_rate_limit_remaining", "remaining")],
            colors={"remaining": BLUE},
        ),
        8,
        7,
    )
    b.add(
        timeseries(
            "GitHub API requests",
            [q("sum by (code) (increase(cicd_github_api_requests_total[5m]))", "HTTP {{code}}")],
            draw="bars",
            stack=True,
            colors={"HTTP 200": GREEN, "HTTP 304": BLUE, "HTTP 404": ORANGE, "HTTP 403": RED},
        ),
        8,
        7,
    )
    b.add(
        timeseries(
            "Alert notifications",
            [
                q(
                    "sum by (alertname, status) (increase(cicd_alert_notifications_total[5m]))",
                    "{{alertname}} ({{status}})",
                )
            ],
            draw="bars",
            stack=True,
        ),
        8,
        7,
    )
    return b


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for build in (overview, gha_performance, build_quality, jenkins, stack_health):
        board = build()
        path = OUT / f"{board.uid}.json"
        path.write_text(json.dumps(board.to_json(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent)} ({len(board.panels)} panels)")


if __name__ == "__main__":
    main()
