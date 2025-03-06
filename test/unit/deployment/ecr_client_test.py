import base64
import subprocess
from unittest.mock import MagicMock, call, patch
from shutil import which
from botocore.exceptions import ClientError

import pytest

from cloudlift.deployment.ecr_client import EcrClient, get_container_tool
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture
def ecr_client_mock():
    return MagicMock()


@pytest.fixture
def boto3_session_mock(monkeypatch, ecr_client_mock):
    session_mock = MagicMock()
    session_mock.client.return_value = ecr_client_mock
    boto3_mock = MagicMock()
    boto3_mock.session.Session.return_value = session_mock
    monkeypatch.setattr("cloudlift.deployment.ecr_client.boto3", boto3_mock)
    return boto3_mock


@pytest.fixture
def subprocess_mock(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.ecr_client.subprocess", mock)
    return mock


@pytest.fixture
def which_mock(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.ecr_client.which", mock)
    return mock


@pytest.fixture
def ecr_instance(monkeypatch, ecr_client_mock):
    monkeypatch.setattr(
        "cloudlift.deployment.ecr_client.get_account_id", lambda: "123456789012"
    )
    monkeypatch.setattr(
        "cloudlift.deployment.ecr_client.get_container_tool", lambda: "docker"
    )
    
    # Create an instance and set the version attribute
    instance = EcrClient(
        name="test-service",
        region="ap-south-1",
        build_args={"ARG1": "value1"},
        working_dir="/tmp/test",
    )
    instance.version = "v1.0"  # Set default version for tests
    
    # Replace the ecr_client with our mock to avoid actual AWS calls
    instance.ecr_client = ecr_client_mock
    
    return instance


@pytest.fixture
def version():
    return "v1.0.0"


def test_get_container_tool_podman(which_mock):
    """Test that get_container_tool returns podman if available"""
    which_mock.side_effect = lambda tool: (
        "/usr/bin/podman" if tool == "podman" else None
    )

    result = get_container_tool()

    assert result == "/usr/bin/podman"
    assert which_mock.call_count == 1
    which_mock.assert_called_with("podman")


def test_get_container_tool_docker(which_mock):
    """Test that get_container_tool returns docker if podman is not available"""
    which_mock.side_effect = lambda tool: (
        None if tool == "podman" else "/usr/bin/docker"
    )

    result = get_container_tool()

    assert result == "/usr/bin/docker"
    assert which_mock.call_count == 2
    which_mock.assert_has_calls([call("podman"), call("docker")])


def test_get_container_tool_none_available(which_mock):
    """Test that get_container_tool raises exception if no container tools are available"""
    which_mock.return_value = None

    with pytest.raises(UnrecoverableException, match="Podman not installed"):
        get_container_tool()


def test_init(boto3_session_mock, ecr_instance):
    """Test initialization of EcrClient"""
    assert ecr_instance.name == "test-service"
    assert ecr_instance.region == "ap-south-1"
    assert ecr_instance.build_args == {"ARG1": "value1"}
    assert ecr_instance.working_dir == "/tmp/test"
    assert ecr_instance.container_tool == "docker"

    boto3_session_mock.session.Session.assert_called_once_with(region_name="ap-south-1")


def test_repo_name(ecr_instance):
    """Test repo_name property"""
    assert ecr_instance.repo_name == "test-service-repo"


def test_account_id(ecr_instance):
    """Test account_id property"""
    assert ecr_instance.account_id == "123456789012"


def test_ecr_image_uri(ecr_instance):
    """Test ecr_image_uri property"""
    assert (
        ecr_instance.ecr_image_uri
        == "123456789012.dkr.ecr.ap-south-1.amazonaws.com/test-service-repo"
    )


def test_container_tool_name(ecr_instance):
    """Test container_tool_name property"""
    assert ecr_instance.container_tool_name == "docker"


def test_container_tool_name_with_path(monkeypatch, ecr_instance):
    """Test container_tool_name property with path"""
    monkeypatch.setattr(ecr_instance, "container_tool", "/usr/bin/docker")
    assert ecr_instance.container_tool_name == "docker"


def test_container_tool_name_none(monkeypatch, ecr_instance):
    """Test container_tool_name property when container_tool is None"""
    monkeypatch.setattr(ecr_instance, "container_tool", None)
    assert ecr_instance.container_tool_name is None


def test_ensure_repository_new(ecr_client_mock, ecr_instance):
    """Test _ensure_repository when repository doesn't exist"""
    ecr_client_mock.create_repository.return_value = {
        "repository": {"repositoryName": "test-service-repo"}
    }

    ecr_instance._ensure_repository()

    ecr_client_mock.create_repository.assert_called_once_with(
        repositoryName="test-service-repo",
        imageScanningConfiguration={"scanOnPush": True},
    )


def test_ensure_repository_exists(ecr_client_mock, ecr_instance):
    """Test _ensure_repository when repository already exists"""
    # Create a proper ClientError with RepositoryAlreadyExistsException
    error_response = {
        'Error': {
            'Code': 'RepositoryAlreadyExistsException',
            'Message': 'Repository already exists'
        }
    }
    # This creates a ClientError with the expected message containing 'RepositoryAlreadyExistsException'
    error = ClientError(error_response, 'CreateRepository')
    ecr_client_mock.create_repository.side_effect = error

    ecr_instance._ensure_repository()

    ecr_client_mock.create_repository.assert_called_once()


def test_ensure_repository_other_exception(ecr_client_mock, ecr_instance):
    """Test _ensure_repository when other exception occurs"""
    error = Exception("Some other error")
    ecr_client_mock.create_repository.side_effect = error

    with pytest.raises(Exception, match="Some other error"):
        ecr_instance._ensure_repository()


def test_build_command_no_args(monkeypatch, ecr_instance):
    """Test _build_command when no build args are provided"""
    monkeypatch.setattr(ecr_instance, "build_args", None)

    result = ecr_instance._build_command("test-image:latest")

    assert result == "docker build -t test-image:latest /tmp/test"


def test_build_command_with_args(ecr_instance):
    """Test _build_command with build args"""
    result = ecr_instance._build_command("test-image:latest")

    assert "docker build -t test-image:latest" in result
    assert "--build-arg ARG1=value1" in result
    assert "/tmp/test" in result


def test_login_to_ecr(ecr_client_mock, subprocess_mock, ecr_instance):
    """Test _login_to_ecr method"""
    # Setup mock response
    auth_token = base64.b64encode(b"AWS:password123").decode("utf-8")
    ecr_client_mock.get_authorization_token.return_value = {
        "authorizationData": [
            {
                "authorizationToken": auth_token,
                "proxyEndpoint": "https://123456789012.dkr.ecr.ap-south-1.amazonaws.com",
            }
        ]
    }

    ecr_instance._login_to_ecr()

    # Verify correct calls
    ecr_client_mock.get_authorization_token.assert_called_once()
    subprocess_mock.check_call.assert_called_once_with(
        [
            "docker",
            "login",
            "-u",
            "AWS",
            "-p",
            "password123",
            "123456789012.dkr.ecr.ap-south-1.amazonaws.com",
        ]
    )


def test_find_commit_sha(subprocess_mock, ecr_instance):
    """Test _find_commit_sha with no version parameter"""
    # First check for tag returns empty (no tags for HEAD)
    subprocess_mock.check_output.side_effect = [
        b"",  # git tag -l HEAD
        b"abcdef1234567890"  # git rev-list
    ]
    
    result = ecr_instance._find_commit_sha()
    assert result == "abcdef1234567890"
    
    # Check that it first tried to find a tag, then fell back to commit hash
    subprocess_mock.check_output.assert_has_calls([
        call(["git", "tag", "-l", "HEAD"]),
        call(["git", "rev-list", "-n", "1", "HEAD"])
    ])

def test_find_commit_sha_with_version(subprocess_mock, ecr_instance, version):
    """Test _find_commit_sha with specific version that's not a tag"""
    # First check for tag returns empty (not a tag)
    subprocess_mock.check_output.side_effect = [
        b"",  # git tag -l v1.0.0
        b"abcdef1234567890"  # git rev-list
    ]
    
    result = ecr_instance._find_commit_sha("v1.0.0")
    assert result == "abcdef1234567890"
    
    # Check that it first tried to find a tag, then fell back to commit hash
    subprocess_mock.check_output.assert_has_calls([
        call(["git", "tag", "-l", "v1.0.0"]),
        call(["git", "rev-list", "-n", "1", "v1.0.0"])
    ])

def test_find_commit_sha_with_tag(subprocess_mock, ecr_instance):
    """Test _find_commit_sha with a version that is a git tag"""
    # When the version is an actual git tag
    subprocess_mock.check_output.side_effect = [
        b"v1.2.3",  # git tag -l v1.2.3 returns the tag itself
    ]
    
    result = ecr_instance._find_commit_sha("v1.2.3")
    assert result == "v1.2.3"  # Should return the tag, not its commit hash
    
    # It should only call git tag -l and not git rev-list
    subprocess_mock.check_output.assert_called_once_with(
        ["git", "tag", "-l", "v1.2.3"]
    )

def test_find_commit_sha_tag_check_fails(subprocess_mock, ecr_instance):
    """Test _find_commit_sha when git tag check fails but commit lookup succeeds"""
    # First command (tag check) fails, second succeeds
    subprocess_mock.check_output.side_effect = [
        subprocess.CalledProcessError(1, "git tag"),  # git tag fails
        b"abcdef1234567890"  # git rev-list succeeds
    ]
    
    result = ecr_instance._find_commit_sha("v1.0.0")
    assert result == "abcdef1234567890"
    
    subprocess_mock.check_output.assert_has_calls([
        call(["git", "tag", "-l", "v1.0.0"]),
        call(["git", "rev-list", "-n", "1", "v1.0.0"])
    ])

def test_find_commit_sha_error(subprocess_mock, ecr_instance):
    """Test _find_commit_sha when both git commands fail"""
    # Both commands fail
    subprocess_mock.check_output.side_effect = [
        subprocess.CalledProcessError(1, "git tag"),  # git tag fails
        subprocess.CalledProcessError(1, "git rev-list"),  # git rev-list also fails
    ]
    
    with pytest.raises(UnrecoverableException, match="Commit SHA not found"):
        ecr_instance._find_commit_sha()


def test_push_image(subprocess_mock, ecr_instance):
    """Test _push_image method"""
    ecr_instance._login_to_ecr = MagicMock()

    ecr_instance._push_image("local-image:latest", "ecr-image:latest")

    # Verify calls in order
    assert ecr_instance._login_to_ecr.called
    subprocess_mock.check_call.assert_has_calls(
        [
            call(["docker", "tag", "local-image:latest", "ecr-image:latest"]),
            call(["docker", "push", "ecr-image:latest"]),
            call(["docker", "rmi", "ecr-image:latest"]),
        ]
    )


def test_push_image_local_not_found(subprocess_mock, ecr_instance):
    """Test _push_image when local image not found"""
    subprocess_mock.check_call.side_effect = [
        subprocess.CalledProcessError(1, "docker tag")
    ]

    with pytest.raises(UnrecoverableException, match="Local image was not found"):
        ecr_instance._push_image("local-image:latest", "ecr-image:latest")


def test_set_version_with_explicit_version(ecr_instance, monkeypatch):
    """Test set_version with explicit version parameter"""
    find_image_mock = MagicMock(return_value={"imageId": "test"})
    find_commit_mock = MagicMock(return_value="commit123")

    monkeypatch.setattr(ecr_instance, "_find_image_in_ecr", find_image_mock)
    monkeypatch.setattr(ecr_instance, "_find_commit_sha", find_commit_mock)

    ecr_instance.set_version("v1.0.0")

    assert ecr_instance.version == "v1.0.0"
    find_commit_mock.assert_called_once_with("v1.0.0")
    find_image_mock.assert_called_once_with("commit123")


def test_set_version_image_not_found(ecr_instance, monkeypatch):
    """Test set_version when image not found in ECR"""
    find_image_mock = MagicMock(return_value=None)
    find_commit_mock = MagicMock(return_value="commit123")

    monkeypatch.setattr(ecr_instance, "_find_image_in_ecr", find_image_mock)
    monkeypatch.setattr(ecr_instance, "_find_commit_sha", find_commit_mock)

    with pytest.raises(
        UnrecoverableException, match="Image for given version could not be found"
    ):
        ecr_instance.set_version("v1.0.0")


def test_set_version_with_dirty_repo(subprocess_mock, ecr_instance):
    """Test set_version with no version parameter and dirty git state"""
    subprocess_mock.check_output.return_value = b"M file.txt"

    ecr_instance.set_version(None)

    assert ecr_instance.version == "dirty"
    subprocess_mock.check_output.assert_called_once_with(["git", "status", "--short"])


def test_set_version_with_clean_repo(subprocess_mock, ecr_instance, monkeypatch):
    """Test set_version with no version parameter and clean git state"""
    # First call is for git status, second for commit sha
    subprocess_mock.check_output.side_effect = [b"", b"commit123"]

    find_commit_sha_mock = MagicMock(return_value="commit123")
    monkeypatch.setattr(ecr_instance, "_find_commit_sha", find_commit_sha_mock)

    ecr_instance.set_version(None)

    assert ecr_instance.version == "commit123"
    find_commit_sha_mock.assert_called_once()


def test_find_image_in_ecr(ecr_client_mock, ecr_instance):
    """Test _find_image_in_ecr when image exists"""
    expected_image = {"imageId": {"imageTag": "v1.0"}, "imageManifest": "manifest-data"}
    ecr_client_mock.batch_get_image.return_value = {"images": [expected_image]}

    result = ecr_instance._find_image_in_ecr("v1.0")

    assert result == expected_image
    ecr_client_mock.batch_get_image.assert_called_once_with(
        repositoryName="test-service-repo", imageIds=[{"imageTag": "v1.0"}]
    )


def test_find_image_in_ecr_not_found(ecr_client_mock, ecr_instance):
    """Test _find_image_in_ecr when image doesn't exist"""
    ecr_client_mock.batch_get_image.return_value = {"images": []}

    result = ecr_instance._find_image_in_ecr("v1.0")

    assert result is None
    ecr_client_mock.batch_get_image.assert_called_once()


def test_find_image_in_ecr_exception(ecr_client_mock, ecr_instance):
    """Test _find_image_in_ecr when exception occurs"""
    ecr_client_mock.batch_get_image.side_effect = Exception("Error finding image")

    result = ecr_instance._find_image_in_ecr("v1.0")

    assert result is None
    ecr_client_mock.batch_get_image.assert_called_once()


def test_add_image_tag_success(ecr_client_mock, ecr_instance):
    """Test _add_image_tag when successful"""
    ecr_client_mock.batch_get_image.return_value = {
        "images": [{"imageManifest": "manifest-data"}]
    }

    ecr_instance._add_image_tag("existing-tag", "new-tag")

    ecr_client_mock.batch_get_image.assert_called_once_with(
        repositoryName="test-service-repo", imageIds=[{"imageTag": "existing-tag"}]
    )
    ecr_client_mock.put_image.assert_called_once_with(
        repositoryName="test-service-repo",
        imageTag="new-tag",
        imageManifest="manifest-data",
    )


def test_add_image_tag_failure(ecr_client_mock, ecr_instance):
    """Test _add_image_tag when it fails"""
    ecr_client_mock.batch_get_image.side_effect = Exception("Error getting image")

    ecr_instance._add_image_tag("existing-tag", "new-tag")

    ecr_client_mock.batch_get_image.assert_called_once()
    ecr_client_mock.put_image.assert_not_called()


def test_build_and_upload_image(ecr_instance, monkeypatch):
    """Test build_and_upload_image method"""
    ensure_repo_mock = MagicMock()
    ensure_image_mock = MagicMock()

    monkeypatch.setattr(ecr_instance, "_ensure_repository", ensure_repo_mock)
    monkeypatch.setattr(ecr_instance, "_ensure_image_in_ecr", ensure_image_mock)

    ecr_instance.build_and_upload_image()

    ensure_repo_mock.assert_called_once()
    ensure_image_mock.assert_called_once()


def test_ensure_image_in_ecr_found(ecr_instance, monkeypatch, ecr_client_mock):
    """Test _ensure_image_in_ecr when image is already in ECR"""
    find_image_mock = MagicMock(return_value={"imageManifest": "manifest-data"})

    monkeypatch.setattr(ecr_instance, "_find_image_in_ecr", find_image_mock)
    monkeypatch.setattr(ecr_instance, "ecr_client", ecr_client_mock)
    # No need to set version as we've already set it in the fixture

    ecr_instance._ensure_image_in_ecr()

    find_image_mock.assert_called_once_with("v1.0")
    ecr_client_mock.put_image.assert_called_once_with(
        repositoryName="test-service-repo",
        imageTag="v1.0",
        imageManifest="manifest-data",
    )


def test_ensure_image_in_ecr_not_found(ecr_instance, monkeypatch):
    """Test _ensure_image_in_ecr when image is not in ECR and needs building"""
    find_image_mock = MagicMock(side_effect=[None, {"imageManifest": "manifest-data"}])
    build_image_mock = MagicMock()
    push_image_mock = MagicMock()

    monkeypatch.setattr(ecr_instance, "_find_image_in_ecr", find_image_mock)
    monkeypatch.setattr(ecr_instance, "_build_image", build_image_mock)
    monkeypatch.setattr(ecr_instance, "_push_image", push_image_mock)
    # Version is already set in fixture

    ecr_instance._ensure_image_in_ecr()

    assert find_image_mock.call_count == 2
    build_image_mock.assert_called_once_with("test-service:v1.0")
    push_image_mock.assert_called_once_with(
        "test-service:v1.0",
        "123456789012.dkr.ecr.ap-south-1.amazonaws.com/test-service-repo:v1.0",
    )


def test_ensure_image_in_ecr_dirty(ecr_instance, monkeypatch):
    """Test _ensure_image_in_ecr with dirty version"""
    find_image_mock = MagicMock(return_value=None)
    build_image_mock = MagicMock()
    push_image_mock = MagicMock()

    monkeypatch.setattr(ecr_instance, "_find_image_in_ecr", find_image_mock)
    monkeypatch.setattr(ecr_instance, "_build_image", build_image_mock)
    monkeypatch.setattr(ecr_instance, "_push_image", push_image_mock)
    monkeypatch.setattr(ecr_instance, "version", "dirty")

    ecr_instance._ensure_image_in_ecr()

    build_image_mock.assert_called_once_with("test-service:dirty")
    push_image_mock.assert_called_once_with(
        "test-service:dirty",
        "123456789012.dkr.ecr.ap-south-1.amazonaws.com/test-service-repo:dirty",
    )


def test_build_image(ecr_instance, subprocess_mock, monkeypatch):
    """Test _build_image method"""
    build_command_mock = MagicMock(
        return_value="docker build -t test-image:latest /tmp/test"
    )
    monkeypatch.setattr(ecr_instance, "_build_command", build_command_mock)

    ecr_instance._build_image("test-image:latest")

    build_command_mock.assert_called_once_with("test-image:latest")
    subprocess_mock.check_call.assert_called_once_with(
        "docker build -t test-image:latest /tmp/test", shell=True
    )


def test_upload_image(ecr_instance, monkeypatch):
    """Test upload_image method"""
    ensure_repo_mock = MagicMock()
    push_image_mock = MagicMock()
    add_tag_mock = MagicMock()

    monkeypatch.setattr(ecr_instance, "_ensure_repository", ensure_repo_mock)
    monkeypatch.setattr(ecr_instance, "_push_image", push_image_mock)
    monkeypatch.setattr(ecr_instance, "_add_image_tag", add_tag_mock)

    ecr_instance.upload_image("v1.0", ["latest", "stable"])

    ensure_repo_mock.assert_called_once()
    push_image_mock.assert_called_once_with(
        "test-service:v1.0",
        "123456789012.dkr.ecr.ap-south-1.amazonaws.com/test-service-repo:v1.0",
    )
    add_tag_mock.assert_has_calls([call("v1.0", "latest"), call("v1.0", "stable")])
