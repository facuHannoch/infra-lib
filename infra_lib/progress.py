"""Cross-cutting progress + interaction reporting.

The pipeline and low-level steps report through the *active* Reporter.
By default it is silent (suitable for programmatic / MCP use). The CLI swaps
in an interactive Reporter (rich spinners + questionary prompts).
"""
import contextlib
from rich.console import Console

console = Console(highlight=False)


class Reporter:
    """Receives progress milestones and interaction callbacks. Default: silent."""

    # --- progress ---
    def step(self, msg: str):
        pass

    def done(self, msg: str):
        pass

    def warn(self, msg: str):
        pass

    def raw(self, text: str):
        """A passthrough output line (pulumi/rsync/setup output)."""
        pass

    def status(self, msg: str):
        """Context manager shown while a long step runs."""
        return contextlib.nullcontext()

    # --- milestones / interaction ---
    def show_ip(self, ip: str):
        pass

    def need_dns(self, domain, ip: str) -> None:
        """A domain needs DNS pointed at `ip`. Silent: assume already handled."""
        pass

    def confirm_test(self) -> bool:
        """Whether to run the post-deploy health check. Silent: yes."""
        return True

    def finished(self, deployment) -> None:
        pass


_active: Reporter = Reporter()


def set_reporter(reporter: Reporter) -> None:
    global _active
    _active = reporter


def reporter() -> Reporter:
    return _active


# Module-level convenience used by low-level steps.
def step(msg: str):
    _active.step(msg)


def done(msg: str):
    _active.done(msg)


def warn(msg: str):
    _active.warn(msg)


def raw(text: str):
    _active.raw(text)


def status(msg: str):
    return _active.status(msg)
