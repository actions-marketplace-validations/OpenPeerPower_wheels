"""Opp.io Builder main application."""
import os
from pathlib import Path
import shutil
import re
import subprocess
from subprocess import CalledProcessError, TimeoutExpired
import sys
from tempfile import TemporaryDirectory
from typing import Optional
from functools import partial
from email.utils import parseaddr


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
    help="Folder with already built wheels for upload.",
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
    "--github-token",
    required=True,
    type=str,
    help="GitHub token to use for pushing to the index repository.",
)
@click.option(
    "--index-name",
    required=True,
    type=str,
    help='Index repository name on GitHub, e.g. "openpeerpower/python-package-server/."',
)
@click.option(
    "--signature",
    required=True,
    type=str,
    help="Git signature for the index repository, in the standard format Full Name <email@com>",
)
@click.option(
    "--timeout", default=345, type=int, help="Max runtime for pip before abort."
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
    github_token: str,
    index_name: str,
    signature: str,
    timeout: int,
):
    """Build wheels precompiled for Open Peer Power container."""
    install_apks(apk)

    exit_code = 0
    with TemporaryDirectory() as index_dir:
        output = Path(index_dir)
        shell = partial(secure_shell, github_token)
        shell(
            "git",
            "clone",
            "--branch=main",
            "--depth=1",
            "https://%s@github.com/%s.git" % (github_token, index_name),
            index_dir,
        )
        wheels_dir = create_wheels_folder(output)
        wheels_index = create_wheels_index("https://github.com/" + index_name + ".git")

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
            # Recreate the indices
            make_tree(index_dir + "/docs")
            # Publish the new version
            os.chdir(index_dir)
            name, email = parseaddr(signature)
            shell("git", "config", "user.name", name)
            shell("git", "config", "user.email", email)

            shell("git", "add", "-A")
            shell("git", "commit", "-sm", "Update index")
            shell("git", "push", "origin", "main:main")

    sys.exit(exit_code)


def secure_shell(github_token, *args):
    """ execute git commands in a sub process """
    print(
        " ".join([re.sub(r"%s" % github_token, "<GITHUB_TOKEN>", arg) for arg in args])
    )
    subprocess.run(args, cwd="/usr/src", check=True)


def make_tree(path):
    """ Recreate index,html files """
    html1 = """<!DOCTYPE html>
<html>
    """
    for root, dirs, files in os.walk(path):
        path = root.split(os.sep)
        doc = (
            html1
            + """<head><title>Index of /{0}/</title></head>
    <body bgcolor="white">
        <h1>Index of /{0}/</h1><pre><a href="../">../</a>
        """.format(
                os.path.basename(root)
            )
        )
        for dirname in dirs:
            doc = (
                doc
                + """
            <a href={0}/>{0}/</a>""".format(
                    dirname
                )
            )
        for file in files:
            if file != "index.html":
                doc = (
                    doc
                    + """
            <a href={0}>{0}</a>""".format(
                        file
                    )
                )
        doc = (
            doc
            + """
    </pre><hr></body>
</html>
        """
        )
        index_file = Path(root + "/index.html")
        with open(index_file, "w") as fil_name:
            fil_name.write(doc)


if __name__ == "__main__":
    builder()  # pylint: disable=no-value-for-parameter
