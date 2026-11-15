import importlib.metadata

import pytest

import verso


def test_installed_distribution_matches_source_version():
    try:
        installed = importlib.metadata.version("verso-cli")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("verso-cli is not installed in this environment")

    assert installed == verso.__version__
