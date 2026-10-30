# Public native rendering API in pinned docling-parse 7.22.3.
# Upstream ships py.typed but no stub for this extension module.
class RenderConfig:
    scale: float
    canvas_width: int
    canvas_height: int
    render_text: bool
    def __init__(self) -> None: ...
