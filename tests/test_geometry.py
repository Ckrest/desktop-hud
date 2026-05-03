from desktop_hud.geometry import resolve_frame, resolve_stack_scene


def test_resolve_center_frame_with_viewport_units():
    frame = resolve_frame(
        {
            "anchor": "center",
            "origin": "center",
            "width": "50vw",
            "height": "25vh",
        },
        2000,
        1000,
    )

    assert frame.to_data() == {"x": 500, "y": 375, "width": 1000, "height": 250}


def test_resolve_bottom_center_clamps_to_margins():
    frame = resolve_frame(
        {
            "anchor": "bottom-center",
            "origin": "bottom-center",
            "offset": {"y": "-20px"},
            "width": "90vw",
            "max_width": "1200px",
            "height": "200px",
            "margin": {"x": "40px", "y": "40px"},
        },
        1600,
        900,
    )

    assert frame.width == 1200
    assert frame.height == 200
    assert frame.x == 200
    assert frame.y == 640


def test_stack_scene_resolves_child_frames_relative_to_parent():
    elements, parent = resolve_stack_scene(
        {
            "frame": {
                "anchor": "bottom-center",
                "origin": "bottom-center",
                "width": "800px",
                "margin": "40px",
            },
            "layout": {"type": "stack", "direction": "vertical", "gap": "10px", "align": "start"},
            "items": [
                {"id": "keyboard", "type": "keyboard", "width": "100%", "height": "300px"},
                {"id": "media", "type": "keyboard", "width": "400px", "height": "80px"},
            ],
        },
        1200,
        800,
    )

    assert parent.to_data() == {"x": 200, "y": 370, "width": 800, "height": 390}
    assert elements[0]["resolved_frame"] == {"x": 200, "y": 370, "width": 800, "height": 300}
    assert elements[1]["resolved_frame"] == {"x": 200, "y": 680, "width": 400, "height": 80}
