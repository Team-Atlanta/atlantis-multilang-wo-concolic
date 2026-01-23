import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from mlla.utils.cp import init_cp_repo, sCP


@pytest.fixture
def test_cp_path():
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    cp_dir = Path(temp_dir) / "test_cp"
    cp_dir.mkdir()

    # Create project.yaml
    project_yaml = {
        "language": "jvm",
        "main_repo": "git@github.com:Team-Atlanta/cp-java-jenkins-source.git",
    }
    with open(cp_dir / "project.yaml", "w") as f:
        yaml.dump(project_yaml, f)

    # Create .aixcc directory and config.yaml
    aixcc_path = cp_dir / ".aixcc"
    aixcc_path.mkdir()
    aixcc_config = {
        "full_mode": {"base_commit": "e0f81a3bdbd777f8b801e41bb8ccd26b33ca5022"},
        "harness_files": [
            {
                "name": "JenkinsTwo",
                "path": (
                    "$PROJECT/fuzz/jenkins-harness-two"
                    "/src/main/java/com/aixcc/jenkins/harnesses/two/JenkinsTwo.java"
                ),
            },
            {
                "name": "JenkinsThree",
                "path": (
                    "$PROJECT/fuzz/jenkins-harness-three"
                    "/src/main/java/com/aixcc/jenkins/harnesses/three/JenkinsThree.java"
                ),
            },
            {
                "name": "JenkinsFive",
                "path": (
                    "$PROJECT/fuzz/jenkins-harness-five"
                    "/src/main/java/com/aixcc/jenkins/harnesses/five/JenkinsFive.java"
                ),
            },
        ],
    }
    with open(aixcc_path / "config.yaml", "w") as f:
        yaml.dump(aixcc_config, f)

    yield cp_dir

    # Cleanup
    shutil.rmtree(temp_dir)


def test_cp_initialization_without_crs_target(test_cp_path):
    """Test CP initialization without CRS_TARGET environment variable"""
    # Initialize repository
    cp_src_path = init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path)

    # Verify CP attributes - should use cp_path.name
    assert cp.name == test_cp_path.name
    assert Path(cp.proj_path) == test_cp_path
    assert Path(cp.cp_src_path) == cp_src_path

    # Verify harness configuration was loaded
    assert len(cp.harnesses) > 0
    harness_names = [h.name for h in cp.harnesses.values()]
    assert "JenkinsTwo" in harness_names
    assert "JenkinsThree" in harness_names
    assert "JenkinsFive" in harness_names


def test_cp_initialization_with_crs_target(test_cp_path, monkeypatch):
    """Test CP initialization with CRS_TARGET environment variable"""
    # Set CRS_TARGET environment variable
    target_name = "custom-target-name"
    monkeypatch.setenv(
        "CRS_TARGET", target_name
    )  # Temporarily set env var for this test only

    # Initialize repository
    cp_src_path = init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path)

    # Verify CP attributes - should use CRS_TARGET value
    assert cp.name == target_name
    assert Path(cp.proj_path) == test_cp_path
    assert Path(cp.cp_src_path) == cp_src_path

    # Verify harness configuration was loaded
    assert len(cp.harnesses) > 0
    harness_names = [h.name for h in cp.harnesses.values()]
    assert "JenkinsTwo" in harness_names
    assert "JenkinsThree" in harness_names
    assert "JenkinsFive" in harness_names


def test_cp_initialization_with_target_harness(test_cp_path):
    """Test CP initialization with target harness specified"""
    # Initialize repository with target harness
    cp_src_path = init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path, "JenkinsTwo")

    # Verify CP attributes
    assert cp.name == test_cp_path.name
    assert Path(cp.proj_path) == test_cp_path
    assert Path(cp.cp_src_path) == cp_src_path

    # Verify only target harness is loaded
    assert len(cp.harnesses) == 1
    harness = next(iter(cp.harnesses.values()))
    assert harness.name == "JenkinsTwo"


def test_cp_sanitizers(test_cp_path):
    """Test CP sanitizers property"""
    # Initialize repository
    init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path)

    # Update project.yaml to include sanitizers
    project_yaml = {
        "language": "jvm",
        "main_repo": "git@github.com:Team-Atlanta/cp-java-jenkins-source.git",
        "sanitizers": ["address", "undefined"],
    }
    with open(test_cp_path / "project.yaml", "w") as f:
        yaml.dump(project_yaml, f)

    # Verify sanitizers
    assert cp.sanitizers == ["address", "undefined"]


def test_cp_list_files_recursive(test_cp_path):
    """Test CP list_files_recursive method"""
    # Initialize repository
    init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path)

    # Get list of files
    files = cp.list_files_recursive()

    # Verify expected files are present
    file_paths = [str(f.relative_to(test_cp_path)) for f in files]
    assert "project.yaml" in file_paths
    assert ".aixcc/config.yaml" in file_paths


def test_from_cp_path(test_cp_path):
    """Test CP path properties"""
    # Initialize repository
    init_cp_repo(test_cp_path)
    _cp, cp = sCP.from_cp_path(test_cp_path)

    # Verify path properties
    assert cp.aixcc_path == test_cp_path / ".aixcc"
    assert cp.yaml_path == test_cp_path / "project.yaml"
