# Copyright 2020-2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For further info, check https://github.com/canonical/charmcraft

import datetime
import json
import pathlib
import sys
import zipfile
from argparse import Namespace
from textwrap import dedent
from unittest import mock
from unittest.mock import MagicMock, call, patch

import pytest
import yaml
from craft_cli import CraftError

from charmcraft.bases import get_host_as_base
from charmcraft.cmdbase import JSON_FORMAT
from charmcraft.commands import pack
from charmcraft.commands.pack import PackCommand, build_zip
from charmcraft.config import Project, load, BasesConfiguration


def get_namespace(
    *,
    bases_index=None,
    debug=False,
    destructive_mode=False,
    force=None,
    shell=False,
    shell_after=False,
    format=None,
    measure=None,
):
    if bases_index is None:
        bases_index = []

    return Namespace(
        bases_index=bases_index,
        debug=debug,
        destructive_mode=destructive_mode,
        force=force,
        shell=shell,
        shell_after=shell_after,
        format=format,
        measure=measure,
    )


# default namespace
noargs = get_namespace()


@pytest.fixture
def bundle_yaml(tmp_path):
    """Create an empty bundle.yaml, with the option to set values to it."""
    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text("{}")
    content = {}

    def func(*, name):
        content["name"] = name
        encoded = yaml.dump(content)
        bundle_path.write_text(encoded)
        return encoded

    return func


@pytest.fixture
def mock_parts():
    with patch("charmcraft.commands.pack.parts") as mock_parts:
        yield mock_parts


@pytest.fixture
def mock_launch_shell():
    with patch("charmcraft.commands.build.launch_shell") as mock_shell:
        yield mock_shell


# -- tests for the project type decissor


def test_resolve_charm_type(config):
    """The config indicates the project is a charm."""
    config.set(type="charm")
    cmd = PackCommand(config)

    with patch.object(cmd, "_pack_charm") as mock:
        cmd.run(noargs)
    mock.assert_called_with(noargs)


def test_resolve_bundle_type(config):
    """The config indicates the project is a bundle."""
    config.set(type="bundle")
    cmd = PackCommand(config)

    with patch.object(cmd, "_pack_bundle") as mock:
        cmd.run(noargs)
    mock.assert_called_with(noargs)


def test_resolve_no_config_packs_charm(config, tmp_path):
    """There is no config, so it's decided to pack a charm."""
    config.set(
        project=Project(
            config_provided=False,
            dirpath=tmp_path,
            started_at=datetime.datetime.utcnow(),
        )
    )
    cmd = PackCommand(config)

    with patch.object(cmd, "_pack_charm") as mock:
        cmd.run(noargs)
    mock.assert_called_with(noargs)


def test_resolve_dump_measure_if_indicated(config, tmp_path):
    """Dumps measurement if the user requested it."""
    config.set(type="charm")
    cmd = PackCommand(config)

    measure_filepath = tmp_path / "testmeasures.json"
    args = get_namespace(measure=measure_filepath)
    with patch.object(cmd, "_pack_charm"):
        cmd.run(args)

    # check the measurement file is created, and check that the root measurement
    # is the whole pack run
    assert measure_filepath.exists()
    dumped = json.loads(measure_filepath.read_text())
    (root_measurement,) = [
        measurement
        for measurement in dumped.values()
        if "parent" in measurement and measurement["parent"] is None
    ]
    assert root_measurement["msg"] == "Whole pack run"


# -- tests for main bundle building process


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
@pytest.mark.parametrize("formatted", [None, JSON_FORMAT])
def test_bundle_simple_succesful_build(tmp_path, emitter, bundle_yaml, bundle_config, formatted):
    """A simple happy story."""
    # mandatory files (other than the automatically provided manifest)
    content = bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")
    (tmp_path / "README.md").write_text("test readme")

    # build!
    args = get_namespace(format=formatted)
    PackCommand(bundle_config).run(args)

    # check
    zipname = tmp_path / "testbundle.zip"
    zf = zipfile.ZipFile(zipname)
    assert "charmcraft.yaml" not in [x.filename for x in zf.infolist()]
    assert zf.read("bundle.yaml") == content.encode("ascii")
    assert zf.read("README.md") == b"test readme"

    if formatted:
        emitter.assert_json_output({"bundles": [str(zipname)]})
    else:
        expected = "Created '{}'.".format(zipname)
        emitter.assert_message(expected)

    # check the manifest is present and with particular values that depend on given info
    manifest = yaml.safe_load(zf.read("manifest.yaml"))
    assert manifest["charmcraft-started-at"] == bundle_config.project.started_at.isoformat() + "Z"

    # verify that the manifest was not leftover in user's project
    assert not (tmp_path / "manifest.yaml").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_missing_bundle_file(tmp_path, bundle_config):
    """Can not build a bundle without bundle.yaml."""
    # build without a bundle.yaml!
    with pytest.raises(CraftError) as cm:
        PackCommand(bundle_config).run(noargs)
    assert str(cm.value) == (
        "Missing or invalid main bundle file: '{}'.".format(tmp_path / "bundle.yaml")
    )


def test_bundle_missing_other_mandatory_file(tmp_path, bundle_config, bundle_yaml):
    """Can not build a bundle without any of the mandatory files."""
    bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")

    # build without a README!
    with pytest.raises(CraftError) as cm:
        PackCommand(bundle_config).run(noargs)
    assert str(cm.value) == "Missing mandatory file: {!r}.".format(str(tmp_path / "README.md"))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_missing_name_in_bundle(tmp_path, bundle_yaml, bundle_config):
    """Can not build a bundle without name."""
    bundle_config.set(type="bundle")

    # build!
    with pytest.raises(CraftError) as cm:
        PackCommand(bundle_config).run(noargs)
    assert str(cm.value) == (
        "Invalid bundle config; "
        "missing a 'name' field indicating the bundle's name in file '{}'.".format(
            tmp_path / "bundle.yaml"
        )
    )


def test_bundle_debug_no_error(
    tmp_path, bundle_yaml, bundle_config, mock_parts, mock_launch_shell
):
    bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")
    (tmp_path / "README.md").write_text("test readme")

    PackCommand(bundle_config).run(get_namespace(debug=True))

    assert mock_launch_shell.mock_calls == []


def test_bundle_debug_with_error(
    tmp_path, bundle_yaml, bundle_config, mock_parts, mock_launch_shell
):
    mock_parts.PartsLifecycle.return_value.run.side_effect = CraftError("fail")
    bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")
    (tmp_path / "README.md").write_text("test readme")

    with pytest.raises(CraftError):
        PackCommand(bundle_config).run(get_namespace(debug=True))

    assert mock_launch_shell.mock_calls == [mock.call()]


def test_bundle_shell(tmp_path, bundle_yaml, bundle_config, mock_parts, mock_launch_shell):
    bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")
    (tmp_path / "README.md").write_text("test readme")

    PackCommand(bundle_config).run(get_namespace(shell=True))

    assert mock_launch_shell.mock_calls == [mock.call()]


def test_bundle_shell_after(tmp_path, bundle_yaml, bundle_config, mock_parts, mock_launch_shell):
    bundle_yaml(name="testbundle")
    bundle_config.set(type="bundle")
    (tmp_path / "README.md").write_text("test readme")

    PackCommand(bundle_config).run(get_namespace(shell_after=True))

    assert mock_launch_shell.mock_calls == [mock.call()]


# -- tests for implicit bundle part


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_parts_not_defined(tmp_path, monkeypatch, bundle_yaml):
    """Parts are not defined.

    When the "parts" section does not exist, create an implicit "bundle" part and
    populate it with the default bundle building parameters.
    """
    bundle_yaml(name="testbundle")
    (tmp_path / "README.md").write_text("test readme")

    charmcraft_file = tmp_path / "charmcraft.yaml"
    charmcraft_file.write_text("type: bundle")

    config = load(tmp_path)

    monkeypatch.setenv("CHARMCRAFT_MANAGED_MODE", "1")
    with patch("charmcraft.parts.PartsLifecycle", autospec=True) as mock_lifecycle:
        mock_lifecycle.side_effect = SystemExit()
        with pytest.raises(SystemExit):
            PackCommand(config).run(get_namespace(shell_after=True))
    mock_lifecycle.assert_has_calls(
        [
            call(
                {
                    "bundle": {
                        "plugin": "bundle",
                        "source": str(tmp_path),
                        "prime": [
                            "bundle.yaml",
                            "README.md",
                        ],
                    }
                },
                work_dir=pathlib.Path("/root"),
                project_dir=tmp_path,
                project_name="testbundle",
                ignore_local_sources=["testbundle.zip"],
            )
        ]
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_parts_with_bundle_part(tmp_path, monkeypatch, bundle_yaml):
    """Parts are declared with a charm part with implicit plugin.

    When the "parts" section exists in chamcraft.yaml and a part named "bundle"
    is defined with implicit plugin (or explicit "bundle" plugin), populate it
    with the defaults for bundle building.
    """
    bundle_yaml(name="testbundle")
    (tmp_path / "README.md").write_text("test readme")

    charmcraft_file = tmp_path / "charmcraft.yaml"
    charmcraft_file.write_text(
        dedent(
            """
            type: bundle
            parts:
              bundle:
                prime:
                  - my_extra_file.txt
        """
        )
    )

    config = load(tmp_path)

    monkeypatch.setenv("CHARMCRAFT_MANAGED_MODE", "1")
    with patch("charmcraft.parts.PartsLifecycle", autospec=True) as mock_lifecycle:
        mock_lifecycle.side_effect = SystemExit()
        with pytest.raises(SystemExit):
            PackCommand(config).run(get_namespace(shell_after=True))
    mock_lifecycle.assert_has_calls(
        [
            call(
                {
                    "bundle": {
                        "plugin": "bundle",
                        "source": str(tmp_path),
                        "prime": [
                            "my_extra_file.txt",
                            "bundle.yaml",
                            "README.md",
                        ],
                    }
                },
                work_dir=pathlib.Path("/root"),
                project_dir=tmp_path,
                project_name="testbundle",
                ignore_local_sources=["testbundle.zip"],
            )
        ]
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_parts_without_bundle_part(tmp_path, monkeypatch, bundle_yaml):
    """Parts are declared without a bundle part.

    When the "parts" section exists in chamcraft.yaml and a part named "bundle"
    is not defined, process parts normally and don't invoke the bundle plugin.
    """
    bundle_yaml(name="testbundle")
    (tmp_path / "README.md").write_text("test readme")

    charmcraft_file = tmp_path / "charmcraft.yaml"
    charmcraft_file.write_text(
        dedent(
            """
            type: bundle
            parts:
              foo:
                plugin: nil
        """
        )
    )

    config = load(tmp_path)

    monkeypatch.setenv("CHARMCRAFT_MANAGED_MODE", "1")
    with patch("charmcraft.parts.PartsLifecycle", autospec=True) as mock_lifecycle:
        mock_lifecycle.side_effect = SystemExit()
        with pytest.raises(SystemExit):
            PackCommand(config).run(get_namespace(shell_after=True))
    mock_lifecycle.assert_has_calls(
        [
            call(
                {
                    "foo": {
                        "plugin": "nil",
                    }
                },
                work_dir=pathlib.Path("/root"),
                project_dir=tmp_path,
                project_name="testbundle",
                ignore_local_sources=["testbundle.zip"],
            )
        ]
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_bundle_parts_with_bundle_part_with_plugin(tmp_path, monkeypatch, bundle_yaml):
    """Parts are declared with a bundle part that uses a different plugin.

    When the "parts" section exists in chamcraft.yaml and a part named "bundle"
    is defined with a plugin that's not "bundle", handle it as a regular part
    without populating fields for bundle building.
    """
    bundle_yaml(name="testbundle")
    (tmp_path / "README.md").write_text("test readme")

    charmcraft_file = tmp_path / "charmcraft.yaml"
    charmcraft_file.write_text(
        dedent(
            """
            type: bundle
            parts:
              bundle:
                plugin: nil
        """
        )
    )

    config = load(tmp_path)

    monkeypatch.setenv("CHARMCRAFT_MANAGED_MODE", "1")
    with patch("charmcraft.parts.PartsLifecycle", autospec=True) as mock_lifecycle:
        mock_lifecycle.side_effect = SystemExit()
        with pytest.raises(SystemExit):
            PackCommand(config).run(get_namespace(shell_after=True))
    mock_lifecycle.assert_has_calls(
        [
            call(
                {
                    "bundle": {
                        "plugin": "nil",
                    }
                },
                work_dir=pathlib.Path("/root"),
                project_dir=tmp_path,
                project_name="testbundle",
                ignore_local_sources=["testbundle.zip"],
            )
        ]
    )


# -- tests for get paths helper


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_mandatory_ok(tmp_path, bundle_yaml, bundle_config):
    """Simple successful case getting all mandatory files."""
    bundle_yaml(name="testbundle")
    test_mandatory = ["foo.txt", "bar.bin"]
    test_file1 = tmp_path / "foo.txt"
    test_file1.touch()
    test_file2 = tmp_path / "bar.bin"
    test_file2.touch()

    with patch.object(pack, "MANDATORY_FILES", test_mandatory):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    assert "foo.txt" in zipped_files
    assert "bar.bin" in zipped_files


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_ok(tmp_path, bundle_yaml, bundle_config):
    """Extra files were indicated ok."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["f2.txt", "f1.txt"])
    testfile1 = tmp_path / "f1.txt"
    testfile1.touch()
    testfile2 = tmp_path / "f2.txt"
    testfile2.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    assert "f1.txt" in zipped_files
    assert "f2.txt" in zipped_files


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_missing(tmp_path, bundle_yaml, bundle_config):
    """Extra files were indicated but not found."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["f2.txt", "f1.txt"])
    testfile1 = tmp_path / "f1.txt"
    testfile1.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        with pytest.raises(CraftError) as err:
            PackCommand(bundle_config).run(noargs)
    assert str(err.value) == (
        "Parts processing error: Failed to copy '{}/build/stage/f2.txt': "
        "no such file or directory.".format(tmp_path)
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_long_path(tmp_path, bundle_yaml, bundle_config):
    """An extra file can be deep in directories."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["foo/bar/baz/extra.txt"])
    testfile = tmp_path / "foo" / "bar" / "baz" / "extra.txt"
    testfile.parent.mkdir(parents=True)
    testfile.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    assert "foo/bar/baz/extra.txt" in zipped_files


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_wildcards_ok(tmp_path, bundle_yaml, bundle_config):
    """Use wildcards to specify several files ok."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["*.txt"])
    testfile1 = tmp_path / "f1.txt"
    testfile1.touch()
    testfile2 = tmp_path / "f2.bin"
    testfile2.touch()
    testfile3 = tmp_path / "f3.txt"
    testfile3.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    assert "f1.txt" in zipped_files
    assert "f2.bin" not in zipped_files
    assert "f3.txt" in zipped_files


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_wildcards_not_found(tmp_path, bundle_yaml, bundle_config):
    """Use wildcards to specify several files but nothing found."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["*.txt"])

    # non-existent files are not included if using a wildcard
    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    assert zipped_files == ["manifest.yaml"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_globstar(tmp_path, bundle_yaml, bundle_config):
    """Double star means whatever directories are in the path."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["lib/**/*"])
    srcpaths = (
        ("lib/foo/f1.txt", True),
        ("lib/foo/deep/fx.txt", True),
        ("lib/bar/f2.txt", True),
        ("lib/f3.txt", True),
        ("extra/lib/f.txt", False),
        ("libs/fs.txt", False),
    )

    for srcpath, expected in srcpaths:
        testfile = tmp_path / pathlib.Path(srcpath)
        testfile.parent.mkdir(parents=True, exist_ok=True)
        testfile.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    for srcpath, expected in srcpaths:
        assert (srcpath in zipped_files) == expected


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_prime_extra_globstar_specific_files(tmp_path, bundle_yaml, bundle_config):
    """Combination of both mechanisms."""
    bundle_yaml(name="testbundle")
    bundle_config.set(prime=["lib/**/*.txt"])
    srcpaths = (
        ("lib/foo/f1.txt", True),
        ("lib/foo/f1.nop", False),
        ("lib/foo/deep/fx.txt", True),
        ("lib/foo/deep/fx.nop", False),
        ("lib/bar/f2.txt", True),
        ("lib/bar/f2.nop", False),
        ("lib/f3.txt", True),
        ("lib/f3.nop", False),
        ("extra/lib/f.txt", False),
        ("libs/fs.nop", False),
    )

    for srcpath, expected in srcpaths:
        testfile = tmp_path / pathlib.Path(srcpath)
        testfile.parent.mkdir(parents=True, exist_ok=True)
        testfile.touch()

    with patch.object(pack, "MANDATORY_FILES", []):
        PackCommand(bundle_config).run(noargs)

    zf = zipfile.ZipFile(tmp_path / "testbundle.zip")
    zipped_files = [x.filename for x in zf.infolist()]
    for srcpath, expected in srcpaths:
        assert (srcpath in zipped_files) == expected


# -- tests for zip builder


def test_zipbuild_simple(tmp_path):
    """Build a bunch of files in the zip."""
    build_dir = tmp_path / "somedir"
    build_dir.mkdir()

    testfile1 = build_dir / "foo.txt"
    testfile1.write_bytes(b"123\x00456")
    subdir = build_dir / "bar"
    subdir.mkdir()
    testfile2 = subdir / "baz.txt"
    testfile2.write_bytes(b"mo\xc3\xb1o")

    zip_filepath = tmp_path / "testresult.zip"
    build_zip(zip_filepath, build_dir)

    zf = zipfile.ZipFile(zip_filepath)
    assert sorted(x.filename for x in zf.infolist()) == ["bar/baz.txt", "foo.txt"]
    assert zf.read("foo.txt") == b"123\x00456"
    assert zf.read("bar/baz.txt") == b"mo\xc3\xb1o"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_zipbuild_symlink_simple(tmp_path):
    """Symlinks are supported."""
    build_dir = tmp_path / "somedir"
    build_dir.mkdir()

    testfile1 = build_dir / "real.txt"
    testfile1.write_bytes(b"123\x00456")
    testfile2 = build_dir / "link.txt"
    testfile2.symlink_to(testfile1)

    zip_filepath = tmp_path / "testresult.zip"
    build_zip(zip_filepath, build_dir)

    zf = zipfile.ZipFile(zip_filepath)
    assert sorted(x.filename for x in zf.infolist()) == ["link.txt", "real.txt"]
    assert zf.read("real.txt") == b"123\x00456"
    assert zf.read("link.txt") == b"123\x00456"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows not [yet] supported")
def test_zipbuild_symlink_outside(tmp_path):
    """No matter where the symlink points to."""
    # outside the build dir
    testfile1 = tmp_path / "real.txt"
    testfile1.write_bytes(b"123\x00456")

    # inside the build dir
    build_dir = tmp_path / "somedir"
    build_dir.mkdir()
    testfile2 = build_dir / "link.txt"
    testfile2.symlink_to(testfile1)

    zip_filepath = tmp_path / "testresult.zip"
    build_zip(zip_filepath, build_dir)

    zf = zipfile.ZipFile(zip_filepath)
    assert sorted(x.filename for x in zf.infolist()) == ["link.txt"]
    assert zf.read("link.txt") == b"123\x00456"


# tests for the main charm building process


def test_charm_builder_infrastructure_called(config, tmp_path):
    """Check that build.Builder is properly called."""
    measure_filepath = tmp_path / "measurements.json"
    args = Namespace(
        bases_index=[],
        debug=True,
        destructive_mode=True,
        force=True,
        shell=True,
        shell_after=True,
        format=None,
        measure=measure_filepath,
    )
    config.set(type="charm")
    with patch("charmcraft.commands.build.Builder") as builder_class_mock:
        builder_class_mock.return_value = builder_instance_mock = MagicMock()
        PackCommand(config).run(args)
    builder_class_mock.assert_called_with(
        config=config,
        debug=True,
        force=True,
        shell_after=True,
        shell=True,
        measure=measure_filepath,
    )
    builder_instance_mock.run.assert_called_with([], destructive_mode=True)


@pytest.mark.parametrize("formatted", [None, JSON_FORMAT])
def test_charm_pack_output_simple(config, emitter, formatted):
    """Output when packing one charm."""
    args = get_namespace(format=formatted)
    config.set(type="charm")

    builder_instance_mock = MagicMock()
    builder_instance_mock.run.return_value = ["mystuff.charm"]
    with patch("charmcraft.commands.build.Builder") as builder_class_mock:
        builder_class_mock.return_value = builder_instance_mock
        PackCommand(config).run(args)

    if formatted:
        emitter.assert_json_output({"charms": ["mystuff.charm"]})
    else:
        emitter.assert_messages(
            [
                "Charms packed:",
                "    mystuff.charm",
            ]
        )


@pytest.mark.parametrize("formatted", [None, JSON_FORMAT])
def test_charm_pack_output_multiple(config, emitter, formatted):
    """Output when packing multiple charm."""
    args = get_namespace(format=formatted)
    config.set(type="charm")

    builder_instance_mock = MagicMock()
    builder_instance_mock.run.return_value = ["mystuff1.charm", "mystuff2.charm"]
    with patch("charmcraft.commands.build.Builder") as builder_class_mock:
        builder_class_mock.return_value = builder_instance_mock
        PackCommand(config).run(args)

    if formatted:
        emitter.assert_json_output({"charms": ["mystuff1.charm", "mystuff2.charm"]})
    else:
        emitter.assert_messages(
            [
                "Charms packed:",
                "    mystuff1.charm",
                "    mystuff2.charm",
            ]
        )


@pytest.mark.parametrize("formatted", [None, JSON_FORMAT])
def test_charm_pack_output_managed_mode(config, emitter, formatted, monkeypatch):
    """Output when packing charms under managed mode."""
    args = get_namespace(format=formatted)
    config.set(type="charm")

    builder_instance_mock = MagicMock()
    builder_instance_mock.run.return_value = ["mystuff.charm"]
    with patch("charmcraft.env.is_charmcraft_running_in_managed_mode", return_value=True):
        with patch("charmcraft.commands.build.Builder") as builder_class_mock:
            builder_class_mock.return_value = builder_instance_mock
            PackCommand(config).run(args)

    for emitter_call in emitter.interactions:
        assert emitter_call.args[0] != "message"


@pytest.mark.parametrize(
    "bases_indices, bad_index",
    [
        (None, None),  # not used, it's fine
        ([], None),  # empty, it's fine
        ([0], None),  # first one
        ([1], None),  # second one
        ([1, 0], None),  # a sequence
        ([-1], -1),  # not negative!
        ([0, -1], -1),  # also negative, after a valid one
        ([1, 0, -1], -1),  # other sequence
        ([3, 1], 3),  # too big
    ],
)
def test_validator_bases_index_invalid(bases_indices, bad_index, config):
    """Validate the bases indices given in the command line."""
    config.set(
        bases=[
            BasesConfiguration(
                **{"build-on": [get_host_as_base()], "run-on": [get_host_as_base()]}
            ),
            BasesConfiguration(
                **{"build-on": [get_host_as_base()], "run-on": [get_host_as_base()]}
            ),
        ]
    )
    cmd = PackCommand(config)

    if bad_index is None:
        # success case
        cmd._validate_bases_indices(bases_indices)
    else:
        with pytest.raises(CraftError) as exc_cm:
            cmd._validate_bases_indices(bases_indices)
        expected_msg = (
            f"Bases index '{bad_index}' is invalid (must be >= 0 and fit in configured bases)."
        )
        assert str(exc_cm.value) == expected_msg
