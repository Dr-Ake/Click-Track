"""Release notices must not accidentally collect the packaging.licenses module."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


_SPEC = importlib.util.spec_from_file_location(
    "collect_release_licenses", Path(__file__).resolve().parents[1] / "source" / "Collect-Release-Licenses.py")
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)


def test_notice_documents_and_code_with_license_names_are_distinguished():
    for name in (
        "example.dist-info/licenses/LICENSE.txt",
        "numpy/_core/src/multiarray/dragon4_LICENSE.txt",
        "package/licenses/codec-attribution.md",
        "licensing/license_notes.md",
        "_soundfile_data/COPYING",
        "setuptools/config/NOTICE",
        "Pygments.dist-info/licenses/AUTHORS",
        "package/licenses/acknowledgements",
    ):
        assert collector.is_notice(name), name
    for name in (
        "packaging/licenses/__init__.py",
        "packaging/licenses/_spdx.py",
        "pip/_vendor/packaging/licenses/__pycache__/_spdx.cpython-312.pyc",
        "setuptools/_vendor/packaging/licenses/_spdx.pyo",
        "package/__pycache__/LICENSE.txt",
        "package/licenses/codec.c",
        "package/licenses/NOTICE.exe",
        "package/licenses/license.dll",
        "package/licenses/COPYING.dylib",
        "package/licenses/LICENSE.so.3",
        "package/licenses/spdx-data.json",
    ):
        assert not collector.is_notice(name), name


def test_installed_record_collection_keeps_notices_without_module_or_cache(tmp_path):
    installed = tmp_path / "site-packages"
    output = (tmp_path / "notices").resolve()
    output.mkdir()
    root_notice = output / "PortAudio-LICENSE.txt"
    root_notice.write_text("Handwritten notice to preserve.")
    managed = output / "example-native"
    managed.mkdir()
    (managed / "obsolete-platform.txt").write_text("Stale notice from another build.")
    documents = {
        "example_native-1.0.dist-info/licenses/LICENSE.txt": "Distribution license.",
        "example_native/licenses/codec-attribution.md": "Native codec attribution.",
        "example_native/_soundfile_data/COPYING": "Native library license.",
        "example_native-1.0.dist-info/licenses/AUTHORS": "Copyright contributors.",
    }
    non_documents = {
        "example_native/licenses/__init__.py": "Python SPDX implementation.",
        "example_native/licenses/__pycache__/_spdx.cpython-312.pyc": "Bytecode.",
        "example_native/licenses/_spdx.pyo": "Optimized bytecode.",
        "example_native/licenses/NOTICE.dll": "Executable bytes.",
    }
    for name, content in {**documents, **non_documents}.items():
        destination = installed / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    distribution = SimpleNamespace(
        metadata={"Name": "example-native"}, version="1.0",
        files=list(documents) + list(non_documents), locate_file=lambda name: installed / name,
    )
    result = collector.collect_distribution(distribution, output)
    assert {record["installed_record_path"] for record in result["files"]} == set(documents)
    assert sorted(str(path.relative_to(managed)).replace("\\", "/")
                  for path in managed.rglob("*") if path.is_file()) == sorted(documents)
    assert root_notice.read_text() == "Handwritten notice to preserve."
    assert not (managed / "obsolete-platform.txt").exists()
    assert not any(path.name == "__pycache__" for path in output.rglob("*"))
