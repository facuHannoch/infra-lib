"""Build a directory into a container image and push it to a registry.

This is the "package" step of a container workload. It's deliberately thin: it
shells out to the user's local Docker and relies on their existing
`docker login` (e.g. to ghcr.io) — infra-lib does not manage registry tokens.
Pushing always needs auth, even for public images; pulling a public image needs
none (private pull creds are a separate, deferred concern — see todo.md).
"""
import shutil
import subprocess

from .. import progress


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def image_ref(registry: str, name: str, tag: str = "latest") -> str:
    """ghcr.io/me + app -> ghcr.io/me/app:latest."""
    return f"{registry.rstrip('/')}/{name}:{tag}"


def build_and_push(build_dir: str, registry: str, name: str, tag: str = "latest") -> str:
    """Build `build_dir` (which must contain a Dockerfile) and push to `registry`.

    Returns the resulting image ref. Raises with actionable guidance if Docker
    is missing or the push isn't authenticated.
    """
    if not registry:
        raise ValueError("'build' needs a 'registry' to push to (e.g. registry: ghcr.io/<user>).")
    if not _have_docker():
        raise RuntimeError(
            "Docker is required to build an image but wasn't found on PATH. "
            "Install Docker, or use a prebuilt `image:` ref instead of `build:`."
        )
    ref = image_ref(registry, name, tag)

    progress.step(f"Building image {ref}")
    _run(["docker", "build", "-t", ref, build_dir], "docker build failed")

    progress.step(f"Pushing {ref}")
    try:
        _run(["docker", "push", ref], "docker push failed")
    except RuntimeError as e:
        raise RuntimeError(
            f"{e}\nIf this is an auth error, run `docker login {ref.split('/')[0]}` "
            f"with a token that has package-write permission (for ghcr.io: a PAT "
            f"with 'write:packages')."
        ) from e
    progress.done(f"Image ready: {ref}")
    return ref


def _run(cmd: list[str], err_prefix: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{err_prefix}: {detail}")
