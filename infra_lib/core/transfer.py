import logging
import os
import subprocess
import time
import paramiko

from .. import progress

log = logging.getLogger(__name__)

_DEFAULT_SSH_KEY = os.path.expanduser("~/.infra-lib/keys/default_id_rsa")

_ALWAYS_EXCLUDE = [".git", "__pycache__", ".venv", "venv", "node_modules", ".env"]


def _connect(host: str, ssh_key_path: str) -> paramiko.SSHClient:
    key = paramiko.RSAKey.from_private_key_file(ssh_key_path)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username="azureuser", pkey=key)
    return client


def _wait_for_ssh(host: str, ssh_key_path: str, timeout: int = 300):
    deadline = time.time() + timeout
    with progress.status("Waiting for VM to accept SSH..."):
        while time.time() < deadline:
            try:
                _connect(host, ssh_key_path).close()
                progress.done("VM is up")
                return
            except Exception:
                time.sleep(5)
    raise TimeoutError(f"SSH not available on {host} after {timeout}s")


def open_ssh(host: str, ssh_key_path: str = None, wait: bool = True) -> paramiko.SSHClient:
    """Public: return a connected SSH client (waiting for SSH to come up first).

    Callers must close the returned client. Use this instead of the private
    `_connect` so other layers don't depend on transfer internals.
    """
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    if wait:
        _wait_for_ssh(host, ssh_key_path)
    return _connect(host, ssh_key_path)


def ssh_exec(host: str, command: str, ssh_key_path: str = None) -> tuple[str, str, int]:
    """Public: run one command over SSH, return (stdout, stderr, exit_code)."""
    log.debug("ssh %s: %s", host, command)
    client = open_ssh(host, ssh_key_path)
    try:
        _, stdout, stderr = client.exec_command(command)
        out = stdout.read().decode()
        err = stderr.read().decode()
        code = stdout.channel.recv_exit_status()
        return out, err, code
    finally:
        client.close()


def _wait_for_cloud_init(host: str, ssh_key_path: str):
    with progress.status("Waiting for cloud-init (installing Caddy)..."):
        client = _connect(host, ssh_key_path)
        _, stdout, _ = client.exec_command("cloud-init status --wait")
        stdout.channel.recv_exit_status()
        client.close()
    progress.done("Cloud-init complete")


def _write_caddyfile(client: paramiko.SSHClient, caddyfile: str):
    sftp = client.open_sftp()
    with sftp.file("/home/azureuser/Caddyfile.tmp", "w") as f:
        f.write(caddyfile)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        "sudo mv /home/azureuser/Caddyfile.tmp /etc/caddy/Caddyfile && sudo systemctl restart caddy"
    )
    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        raise RuntimeError(f"Failed to write Caddyfile: {stderr.read().decode()}")


def run_setup(host: str, commands: list[str], ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    _wait_for_cloud_init(host, ssh_key_path)
    progress.step(f"Running setup ({len(commands)} step{'s' if len(commands) != 1 else ''})")
    for i, command in enumerate(commands, 1):
        progress.raw(f"  ({i}/{len(commands)}) {command}")
        client = _connect(host, ssh_key_path)
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


def start_service(host: str, name: str, command: str, ssh_key_path: str = None):
    """Install `command` as a supervised systemd service named `name`.

    Unlike a setup step (run once, must exit), a service is long-lived: it is
    detached from our SSH channel (so it can't hang the deploy), restarts on
    crash, and starts on boot. Logs go to the journal (`journalctl -u <name>`).
    """
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    # Escape single quotes so the command survives being wrapped in `bash -lc '...'`.
    escaped = command.replace("'", "'\\''")
    unit = f"""[Unit]
Description=infra-lib service: {name}
After=network.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/srv/files
Environment=PATH=/home/azureuser/.bun/bin:/home/azureuser/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/bin/bash -lc '{escaped}'
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
    progress.step(f"Starting service '{name}'")
    client = _connect(host, ssh_key_path)
    sftp = client.open_sftp()
    with sftp.file(f"/home/azureuser/{name}.service", "w") as f:
        f.write(unit)
    sftp.close()
    install = (
        f"sudo mv /home/azureuser/{name}.service /etc/systemd/system/{name}.service && "
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


def _rsync_dir(host: str, source_dir: str, ssh_key_path: str):
    client = _connect(host, ssh_key_path)
    dir_name = os.path.basename(source_dir.rstrip("/"))
    _, _, stderr = client.exec_command(
        f"sudo mkdir -p /srv/files/{dir_name} && sudo chown -R azureuser:azureuser /srv/files"
    )
    stderr.channel.recv_exit_status()
    client.close()

    cmd = ["rsync", "-az", "--delete", "--filter=:- .gitignore"]
    for pattern in _ALWAYS_EXCLUDE:
        cmd += ["--exclude", pattern]
    cmd += [
        "-e", f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no",
        source_dir.rstrip("/") + "/",
        f"azureuser@{host}:/srv/files/{dir_name}/",
    ]
    subprocess.run(cmd, check=True)


def transfer(host: str, ship: list = None, caddyfile: str = None, ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    _wait_for_cloud_init(host, ssh_key_path)

    dirs = []
    if ship:
        dirs += [d for d in ship if d not in dirs]

    if dirs:
        names = ", ".join(os.path.basename(d.rstrip("/")) for d in dirs)
        progress.step(f"Shipping {names}")
        for d in dirs:
            _rsync_dir(host, d, ssh_key_path)
        progress.done(f"Shipped {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}")

    if caddyfile:
        progress.step("Configuring web server")
        client = _connect(host, ssh_key_path)
        _write_caddyfile(client, caddyfile)
        client.close()
        progress.done("Caddy configured")
