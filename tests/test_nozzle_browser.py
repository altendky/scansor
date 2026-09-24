from experiments.nozzle_browser import Handler


def test_reuse_volume_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/reuse-volume.js"] == (
        "reuse-volume.js",
        "text/javascript",
    )
