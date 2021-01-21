import subprocess
import sys
import os
from pathlib import Path
import re
import tempfile
from functools import partial

from packaging import version as packaging_version


def secure_shell(github_token, *args):
    print(" ".join([re.sub(r"%s" % github_token, "<GITHUB_TOKEN>", arg) for arg in args]))
    subprocess.run(args, check=True)

def whlindex(github_token, index_name, signature, repo_url, repo_tag, package_path, target_branch, target_dir):

    # Discover the package metadata: name, version, required minimum Python version
    cwd = os.getcwd()
    os.chdir(package_path)
    cmd = [sys.executable, str(Path(package_path) / "setup.py"), "--name", "--version",
           "--classifiers"]
    print(" ".join(cmd))
    metadata = subprocess.check_output(cmd).decode().split("\n")
    package_name, package_version = metadata[:2]
    # Normalize name
    # See https://www.python.org/dev/peps/pep-0503/#normalized-names
    normalized_package_name = re.sub(r"[-_.]+", "-", package_name).lower()
    index_file = Path(target_dir) / normalized_package_name / "index.html"
    python_classifier = "Programming Language :: Python :: "
    try:
        python_version = sorted(packaging_version.parse(line[len(python_classifier):])
                                for line in metadata[2:]
                                if line.startswith(python_classifier))[0]
    except IndexError:
        raise LookupError("setup.py must contain a \"%s\" classifier."
                          % python_classifier.strip()) from None
    if repo_tag.lstrip("v") != package_version:
        print("tag <> setup.py version mismatch: %s vs %s" % (
            repo_tag.lstrip("v"), package_version), file=sys.stderr)
        return 1
    os.chdir(cwd)

    # Publish the new version
    with tempfile.TemporaryDirectory() as index_dir:
        shell = partial(secure_shell, github_token)
        shell("git", "clone", "--branch=" + target_branch, "--depth=1",
              "https://%s@github.com/%s.git" % (github_token, index_name), index_dir)
        index_file = Path(index_dir) / index_file
        parser = IndexHTMLParser()
        links_data = parser.get_index_data(str(index_file))

        links_data.append({
            "href": "git+%(repo_url)s@%(repo_tag)s#egg=%(package_name)s-%(package_version)s" %
                    dict(repo_url=repo_url,
                         repo_tag=repo_tag,
                         package_version=package_version,
                         package_name=package_name),
            "data-requires-python": "&gt;=%s" % python_version,
            "data": "-".join([package_name, package_version])
        })

        lt = '<a href="%s" data-requires-python="%s">%s</a><br/>'
        links = [lt % (d["href"], d["data-requires-python"], d["data"]) for d in links_data]
        doc = """<!DOCTYPE html>
<html>
<head>
<title>Links for %(package)s</title>
</head>
<body>
<h1>Links for %(package)s</h1>
%(links)s
</body>
</html>
""" % dict(package=package_name, links="\n".join(links))

        if do_not_push:
            print(doc)
            return 0

        # push the changes
        os.chdir(index_dir)
        name, email = parseaddr(signature)
        shell("git", "config", "user.name", name)
        shell("git", "config", "user.email", email)

        if not index_file.exists():
            index_file.parent.mkdir(parents=True)
        with open(index_file, "w") as f:
            f.write(doc)

        shell("git", "add", "-A")
        shell("git", "commit", "-sm", "Update index for %s-%s" %
              (normalized_package_name, package_version))
        shell("git", "push", "origin", "%s:%s" % (target_branch, target_branch))
