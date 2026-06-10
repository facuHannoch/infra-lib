import logging
import os
import shlex
import subprocess
import time
import paramiko

from .. import progress
from ..models import ShipItem

log = logging.getLogger(__name__)

_DEFAULT_SSH_KEY = os.path.expanduser("~/.infra-lib/keys/default_id_rsa")

# The default admin/SSH user. Provider-specific, so callers (pipeline/providers)
# pass the unit's user through; this default keeps direct/legacy callers working.
_DEFAULT_USER = "azureuser"

_ALWAYS_EXCLUDE = [".git", "__pycache__", ".venv", "venv", "node_modules", ".env"]


def _connect(host: str, ssh_key_path: str, user: str = _DEFAULT_USER,
             port: int = 22) -> paramiko.SSHClient:
    key = paramiko.RSAKey.from_private_key_file(ssh_key_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, pkey=key)
    return client


def _wait_for_ssh(host: str, ssh_key_path: str, user: str = _DEFAULT_USER,
                  port: int = 22, timeout: int = 300):
    deadline = time.time() + timeout
    with progress.status("Waiting for SSH..."):
        while time.time() < deadline:
            try:
                _connect(host, ssh_key_path, user, port).close()
                progress.done("Host is up")
                return
            except Exception:
                time.sleep(5)
    raise TimeoutError(f"SSH not available on {host}:{port} after {timeout}s")


def open_ssh(host: str, ssh_key_path: str = None, wait: bool = True,
             user: str = _DEFAULT_USER, port: int = 22) -> paramiko.SSHClient:
    """Public: return a connected SSH client (waiting for SSH to come up first).

    Callers must close the returned client. Use this instead of the private
    `_connect` so other layers don't depend on transfer internals.
    """
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    if wait:
        _wait_for_ssh(host, ssh_key_path, user, port)
    return _connect(host, ssh_key_path, user, port)


def ssh_exec(host: str, command: str, ssh_key_path: str = None,
             user: str = _DEFAULT_USER, port: int = 22) -> tuple[str, str, int]:
    """Public: run one command over SSH, return (stdout, stderr, exit_code)."""
    log.debug("ssh %s: %s", host, command)
    client = open_ssh(host, ssh_key_path, user=user, port=port)
    try:
        _, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode()
        err = stderr.read().decode()
        code = stdout.channel.recv_exit_status()
        return out, err, code
    finally:
        client.close()


def wait_for_cloud_init(host: str, ssh_key_path: str = None, user: str = _DEFAULT_USER,
                        port: int = 22):
    """Block until cloud-init finishes (Azure VMs install Caddy that way).

    Provider-specific: only VMs that boot with a cloud-init script need this, so
    the Azure provider calls it from create(); the generic pipeline does not.
    """
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    with progress.status("Waiting for cloud-init (installing Caddy)..."):
        client = _connect(host, ssh_key_path, user, port)
        _, stdout, _ = client.exec_command("cloud-init status --wait")
        stdout.channel.recv_exit_status()
        client.close()
    progress.done("Cloud-init complete")


def configure_caddy(host: str, caddyfile: str, ssh_key_path: str = None,
                    user: str = _DEFAULT_USER, port: int = 22):
    """Install a Caddyfile and reload Caddy (TLS + reverse_proxy). VM-only."""
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    client = _connect(host, ssh_key_path, user, port)
    try:
        sftp = client.open_sftp()
        with sftp.file(f"/home/{user}/Caddyfile.tmp", "w") as f:
            f.write(caddyfile)
        sftp.close()
        _, stdout, stderr = client.exec_command(
            f"sudo mv /home/{user}/Caddyfile.tmp /etc/caddy/Caddyfile && sudo systemctl restart caddy"
        )
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(f"Failed to write Caddyfile: {stderr.read().decode()}")
    finally:
        client.close()


def run_setup(host: str, commands: list[str], ssh_key_path: str = None,
              user: str = _DEFAULT_USER, port: int = 22):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path, user, port)
    progress.step(f"Running setup ({len(commands)} step{'s' if len(commands) != 1 else ''})")
    for i, command in enumerate(commands, 1):
        progress.raw(f"  ({i}/{len(commands)}) {command}")
        client = _connect(host, ssh_key_path, user, port)
        stdin, stdout, _ = client.exec_command(command)
        # Merge stderr into the stream so a failing step's error text is shown,
        # not just its exit code.
        stdout.channel.set_combine_stderr(True)
        # Send EOF on remote stdin so a backgrounded process (e.g. `... &`) can't
        # hold the channel open and hang us. Pair with `>log 2>&1` in the command.
        stdin.close()
        for line in iter(stdout.readline, ""):
            clean = line.rstrip("\n").rstrip("\r")
            if clean.strip():
                progress.raw(f"    {clean}")
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        if exit_code != 0:
            raise RuntimeError(f"Setup step {i} failed (exit {exit_code}): {command}")
    progress.done("Setup complete")


def start_service(host: str, name: str, command: str, ssh_key_path: str = None,
                  user: str = _DEFAULT_USER, port: int = 22, env: dict = None):
    """Install `command` as a supervised systemd service named `name`.

    Unlike a setup step (run once, must exit), a service is long-lived: it is
    detached from our SSH channel (so it can't hang the deploy), restarts on
    crash, and starts on boot. Logs go to the journal (`journalctl -u <name>`).
    `env` is written as systemd Environment= lines (a unit's `env`).
    """
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    # Escape single quotes so the command survives being wrapped in `bash -lc '...'`.
    escaped = command.replace("'", "'\\''")
    env_lines = "".join(
        f"Environment={shlex.quote(f'{k}={v}')}\n" for k, v in (env or {}).items()
    )
    unit = f"""[Unit]
Description=infra-lib service: {name}
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory=/srv/files
Environment=PATH=/home/{user}/.bun/bin:/home/{user}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
{env_lines}ExecStart=/bin/bash -lc '{escaped}'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
    progress.step(f"Starting service '{name}'")
    client = _connect(host, ssh_key_path, user, port)
    sftp = client.open_sftp()
    with sftp.file(f"/home/{user}/{name}.service", "w") as f:
        f.write(unit)
    sftp.close()
    install = (
        f"sudo mv /home/{user}/{name}.service /etc/systemd/system/{name}.service && "
        f"sudo systemctl daemon-reload && "
        f"sudo systemctl enable {name} >/dev/null 2>&1 && "
        f"sudo systemctl restart {name}"
    )
    _, stdout, stderr = client.exec_command(install)
    exit_code = stdout.channel.recv_exit_status()
    err = stderr.read().decode()
    client.close()
    if exit_code != 0:
        raise RuntimeError(f"Failed to start service '{name}': {err.strip()}")
    progress.done(f"Service '{name}' started (logs: infra-lib logs {name})")


def _resolve_dest(item: ShipItem, home: str) -> str:
    """The remote directory a ship item's contents land in (absolute, ~ expanded)."""
    if not item.dest:
        return f"/srv/files/{os.path.basename(item.src.rstrip('/'))}"
    dest = item.dest
    if dest == "~" or dest.startswith("~/"):
        dest = home + dest[1:]
    elif not dest.startswith("/"):
        dest = f"{home}/{dest}"
    return dest.rstrip("/")


def _rsync_item(host: str, item: ShipItem, ssh_key_path: str, user: str,
                port: int, home: str, sudo: bool):
    dest = _resolve_dest(item, home)
    # Ensure the destination exists and is writable by `user` before rsync runs
    # as that user. mkdir -p creates missing parents (rsync won't).
    prefix = "sudo " if sudo and user != "root" else ""
    client = _connect(host, ssh_key_path, user, port)
    _, stdout, _ = client.exec_command(
        f"{prefix}mkdir -p {shlex.quote(dest)} && {prefix}chown -R {user}:{user} {shlex.quote(dest)}"
    )
    stdout.channel.recv_exit_status()
    client.close()

    cmd = ["rsync", "-az", "--delete", "--filter=:- .gitignore"]
    for pattern in _ALWAYS_EXCLUDE:
        cmd += ["--exclude", pattern]
    cmd += [
        "-e", f"ssh -i {ssh_key_path} -p {port} -o StrictHostKeyChecking=no",
        # Trailing slashes on both: the *contents* of src land *at* dest.
        item.src.rstrip("/") + "/",
        f"{user}@{host}:{dest}/",
    ]
    subprocess.run(cmd, check=True)


def transfer(host: str, ship: list = None, ssh_key_path: str = None,
             user: str = _DEFAULT_USER, port: int = 22, home: str = None, sudo: bool = True):
    """rsync each ship item to its destination. Caller ensures SSH is available."""
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    home = home or ("/root" if user == "root" else f"/home/{user}")
    items = list(ship or [])
    if not items:
        return
    names = ", ".join(os.path.basename(i.src.rstrip("/")) for i in items)
    progress.step(f"Shipping {names}")
    for item in items:
        _rsync_item(host, item, ssh_key_path, user, port, home, sudo)
    progress.done(f"Shipped {len(items)} director{'y' if len(items) == 1 else 'ies'}")
