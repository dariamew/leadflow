"""Pin the installed dependency closure. Run after pip install -e '.[dev]'."""

import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def closure(requirements):
    packages, seen = {}, set()

    def visit(raw):
        requirement = Requirement(raw)
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen:
            return
        seen.add(key)
        distribution = metadata.distribution(name)
        packages[name] = f"{name}=={distribution.version}"
        for dependency in distribution.requires or []:
            parsed = Requirement(dependency)
            if parsed.marker is None or any(
                parsed.marker.evaluate({"extra": extra}) for extra in {"", *requirement.extras}
            ):
                visit(dependency)

    for requirement in requirements:
        visit(requirement)
    return packages


runtime = closure(project["dependencies"])
development = closure(project["optional-dependencies"]["dev"])
(ROOT / "requirements.lock").write_text(
    "# Python 3.12; resolved installed runtime dependencies.\n"
    + "\n".join(runtime[name] for name in sorted(runtime))
    + "\n",
    encoding="utf-8",
)
(ROOT / "requirements-dev.lock").write_text(
    "-r requirements.lock\n"
    + "\n".join(development[name] for name in sorted(development) if name not in runtime)
    + "\n",
    encoding="utf-8",
)
