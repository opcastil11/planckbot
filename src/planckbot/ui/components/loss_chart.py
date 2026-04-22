"""Live loss chart component using Plotly."""

from nicegui import ui
from planckbot.ui.theme import COLORS


def loss_chart(experiment_id: str = ""):
    """Plotly loss chart that auto-updates via timer."""
    fig = {
        "data": [{
            "x": [],
            "y": [],
            "type": "scatter",
            "mode": "lines+markers",
            "name": "Training Loss",
            "line": {"color": COLORS["primary"], "width": 2},
            "marker": {"size": 4},
        }],
        "layout": {
            "title": {"text": "Training Loss", "font": {"color": COLORS["text"]}},
            "paper_bgcolor": COLORS["surface"],
            "plot_bgcolor": COLORS["bg"],
            "xaxis": {
                "title": "Step",
                "color": COLORS["text_muted"],
                "gridcolor": COLORS["surface2"],
            },
            "yaxis": {
                "title": "Loss",
                "color": COLORS["text_muted"],
                "gridcolor": COLORS["surface2"],
            },
            "font": {"color": COLORS["text"]},
            "margin": {"l": 50, "r": 20, "t": 40, "b": 40},
        },
    }
    chart = ui.plotly(fig).classes("w-full").style("height: 350px;")
    return chart


def update_loss_chart(chart, conn, experiment_id: str):
    """Fetch latest metrics from DB and update the chart."""
    try:
        cur = conn.execute(
            "SELECT step, metric_value FROM metrics_history "
            "WHERE experiment_id = ? AND metric_name = 'train_loss' ORDER BY step",
            (experiment_id,),
        )
        rows = cur.fetchall()
        if rows:
            steps = [r[0] or 0 for r in rows]
            values = [r[1] for r in rows]
            chart.figure["data"][0]["x"] = steps
            chart.figure["data"][0]["y"] = values
            chart.update()
    except Exception:
        pass
