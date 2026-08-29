import subprocess

from sandbox.sync.capture import capture_manifest


def test_projection_uses_git_relative_sorted_paths(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "app.py").write_text("print('safe')\n")
    manifest = capture_manifest(tmp_path)
    assert [entry.path for entry in manifest.entries] == ["nested/app.py"]
    assert str(tmp_path) not in str(manifest.canonical_entries())
