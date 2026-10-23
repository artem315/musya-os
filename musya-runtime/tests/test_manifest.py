import pytest

from musya_runtime import ManifestError, load_manifest_file, validate_manifest

DRIVER = """
name: MUR Browser
version: 1.0.0
runtime: musya
entry: main.mysya
permissions:
  - browser.read
  - browser.click
  - browser.screenshot
"""


def test_valid_driver(tmp_path):
    (tmp_path / "main.mysya").write_text('print "hi"')
    (tmp_path / "browser.driver").write_text(DRIVER)
    m = load_manifest_file(tmp_path / "browser.driver", project_root=tmp_path)
    assert m.kind == "driver"
    assert m.id == "mur-browser"
    assert m.permissions == ["browser.read", "browser.click", "browser.screenshot"]
    assert m.sensitive_permissions == []


def test_missing_entry_reported(tmp_path):
    with pytest.raises(ManifestError, match="does not exist"):
        validate_manifest(DRIVER, project_root=tmp_path)


def test_collects_all_errors():
    with pytest.raises(ManifestError) as exc:
        validate_manifest("name: 'bad/name'\nversion: one\nruntime: python\nentry: ../x.py\n"
                          "permissions: [root.shell, browser.read]\nenv: {API_KEY: 1, 'bad-name': 2}")
    errs = " | ".join(exc.value.errors)
    for needle in ["'name'", "semantic", "unsupported runtime", ".mysya", "unknown permission 'root.shell'",
                   "invalid env name"]:
        assert needle in errs


def test_wildcards_and_warnings():
    m = validate_manifest("name: App\nversion: 2.0.0-beta\npermissions: ['browser.*', files.read]\n"
                          "env: {SECRET_TOKEN: x}\nfoo: 1")
    assert "browser.download" in m.permissions and "files.read" in m.permissions
    assert "browser.download" in m.sensitive_permissions
    assert any("secret" in w for w in m.warnings)
    assert any("unknown key 'foo'" in w for w in m.warnings)


def test_yaml_safety():
    with pytest.raises(ManifestError):
        validate_manifest("!!python/object/apply:os.system ['echo pwned']")
    with pytest.raises(ManifestError, match="mapping"):
        validate_manifest("- a\n- b")
