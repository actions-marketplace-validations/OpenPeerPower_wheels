"""Opp.io Builder main application."""
from pathlib import Path
import shutil
from subprocess import CalledProcessError, TimeoutExpired
import sys
from tempfile import TemporaryDirectory
from typing import Optional

import click
import click_pathlib

from builder.apk import install_apks
from builder.infra import (
    check_available_binary,
    create_wheels_folder,
    create_wheels_index,
)
from builder.pip import (
    build_wheels_local,
    build_wheels_package,
    build_wheels_requirement,
    extract_packages,
    install_pips,
    write_requirement,
)
from builder.upload.indexer import whlindex
from builder.utils import check_url
from builder.wheel import copy_wheels_from_cache, fix_wheels_name, run_auditwheel


@click.command("builder")
@click.option("--apk", default="build-base", help="APKs they are needed to build this.")
@click.option("--pip", default="Cython", help="PiPy modules needed to build this.")
@click.option(
    "--skip-binary", default=":none:", help="List of packages to skip wheels from pypi."
)
@click.option(
    "--requirement",
    type=click_pathlib.Path(exists=True),
    help="Python requirement file.",
)
@click.option(
    "--requirement-diff",
    type=click_pathlib.Path(exists=True),
    help="Python requirement file to calc the different for selective builds.",
)
@click.option(
    "--constraint",
    type=click_pathlib.Path(exists=True),
    help="Python constraint file.",
)
@click.option(
    "--prebuild-dir",
    type=click_pathlib.Path(exists=True),
    help="Folder with include allready builded wheels for upload.",
)
@click.option(
    "--single",
    is_flag=True,
    default=False,
    help="Install every package as single requirement.",
)
@click.option(
    "--auditwheel",
    is_flag=True,
    default=False,
    help="Use auditwheel to include dynamic linked library.",
)
@click.option(
    "--local", is_flag=True, default=False, help="Build wheel from local folder setup."
)
@click.option(
    "--test", is_flag=True, default=False, help="Test building wheels, no upload."
)
@click.option(
    "--repo-url", required=True, type=str, help="The repository URL where the Python package officially lives. It should start with \"https://\".\""
)
@click.option(
    "--github-token", required=True, type=str, help="GitHub token to use for pushing to the index repository."
)
@click.option(
    "--index-name", required=True, type=str, help="Index repository name on GitHub, e.g. \"openpeerpower/python-package-server/.\""
)
@click.option(
    "--signature", required=True, type=str, help="Git signature for the index repository, in the standard format Full Name <email@com>"
)
@click.option(
    "--repo-tag", required=True, type=str, help="The tag to publish, which must match the version in setup.py; this is a safety check."
)
@click.option(
    "--timeout", default=345, type=int, help="Max runtime for pip before abort."
)
@click.option(
    "--target-branch", default="main", type=str, help="The Git branch in the index repo to which to publish the package."
)
@click.option(
    "--target-dir", default="docs", type=str, help="Path in the index repository that is the PyPi root. We are assuming GitHub Pages by default."
)
@click.option(
    "--do-not-push", default="", type=str, help="Do not push to the index repo. Set this to whatever to activate this option."
)

def builder(
    apk: str,
    pip: str,
    skip_binary: str,
    requirement: Optional[Path],
    requirement_diff: Optional[Path],
    constraint: Optional[Path],
    prebuild_dir: Optional[Path],
    single: bool,
    auditwheel: bool,
    local: bool,
    test: bool,
    repo_url: str,
    github_token: str,
    index_name: str,
    signature: str,
    repo_tag: str,
    timeout: int,
    target_branch: str,
    target_dir: str,
    do_not_push: str
):
    """Build wheels precompiled for Open Peer Power container."""
    install_apks(apk)
    check_url(index_name)

    exit_code = 0
    with TemporaryDirectory() as temp_dir:
        output = Path(temp_dir)
        output = "/tmp/wheelhouse"

        wheels_dir = create_wheels_folder(output)
        wheels_index = create_wheels_index(index_name)

        # Setup build helper
        install_pips(wheels_index, pip)
        timeout = timeout * 60

        if local:
            # Build wheels in a local folder/src
            build_wheels_local(wheels_index, wheels_dir)
        elif prebuild_dir:
            # Prepare built wheels for upload
            for whl_file in prebuild_dir.glob("*.whl"):
                shutil.copy(whl_file, Path(wheels_dir, whl_file.name))
        elif single:
            # Build every wheel like a single installation
            packages = extract_packages(requirement, requirement_diff)
            skip_binary = check_available_binary(wheels_index, skip_binary, packages)
            for package in packages:
                print(f"Process package: {package}", flush=True)
                try:
                    build_wheels_package(
                        package,
                        wheels_index,
                        wheels_dir,
                        skip_binary,
                        timeout,
                        constraint,
                    )
                except CalledProcessError:
                    exit_code = 109
                except TimeoutExpired:
                    exit_code = 80
                    copy_wheels_from_cache(Path("/root/.cache/pip/wheels"), wheels_dir)
        else:
            # Build all needed wheels at once
            packages = extract_packages(requirement, requirement_diff)
            temp_requirement = Path("/tmp/wheels_requirement.txt")
            write_requirement(temp_requirement, packages)

            skip_binary = check_available_binary(wheels_index, skip_binary, packages)
            try:
                build_wheels_requirement(
                    temp_requirement,
                    wheels_index,
                    wheels_dir,
                    skip_binary,
                    timeout,
                    constraint,
                )
            except CalledProcessError:
                exit_code = 109
            except TimeoutExpired:
                exit_code = 80
                copy_wheels_from_cache(Path("/root/.cache/pip/wheels"), wheels_dir)

        if auditwheel:
            run_auditwheel(wheels_dir)

        fix_wheels_name(wheels_dir)
        if not test:
            whlindex(github_token, index_name, signature, repo_url, repo_tag, output, target_branch, target_dir)

    sys.exit(exit_code)


if __name__ == "__main__":
    builder()  # pylint: disable=no-value-for-parameter
