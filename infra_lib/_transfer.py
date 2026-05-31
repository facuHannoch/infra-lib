import os
import subprocess
import time
import paramiko
from ._progress import console, step, done

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
    with console.status("[bold]Waiting for VM to accept SSH...", spinner="dots"):
        while time.time() < deadline:
            try:
                _connect(host, ssh_key_path).close()
                done("VM is up")
                return
            except Exception:
                time.sleep(5)
    raise TimeoutError(f"SSH not available on {host} after {timeout}s")


def _wait_for_cloud_init(host: str, ssh_key_path: str):
    with console.status("[bold]Waiting for cloud-init (installing Caddy)...", spinner="dots"):
        client = _connect(host, ssh_key_path)
        _, stdout, _ = client.exec_command("cloud-init status --wait")
        stdout.channel.recv_exit_status()
        client.close()
    done("Cloud-init complete")


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
    step(f"Running setup ({len(commands)} step{'s' if len(commands) != 1 else ''})")
    for i, command in enumerate(commands, 1):
        console.print(f"  [dim]({i}/{len(commands)})[/dim] [bold]{command}[/bold]")
        client = _connect(host, ssh_key_path)
        _, stdout, stderr = client.exec_command(command)
        for line in iter(stdout.readline, ""):
            clean = line.rstrip("\n").rstrip("\r")
            if clean.strip():
                console.print(f"    [dim]{clean}[/dim]")
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        if exit_code != 0:
            raise RuntimeError(f"Setup step {i} failed (exit {exit_code}): {command}")
    done("Setup complete")


# Keep for backwards compatibility with --install flag
def run_command(host: str, command: str, ssh_key_path: str = None):
    run_setup(host, [command], ssh_key_path=ssh_key_path)


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


def transfer(host: str, source_dir: str = None, ship: list = None, caddyfile: str = None, ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    _wait_for_cloud_init(host, ssh_key_path)

    dirs = []
    if source_dir:
        dirs.append(source_dir)
    if ship:
        dirs += [d for d in ship if d not in dirs]

    if dirs:
        labels = ", ".join(f"[bold]{os.path.basename(d.rstrip('/'))}[/bold]" for d in dirs)
        step(f"Shipping {labels}")
        for d in dirs:
            _rsync_dir(host, d, ssh_key_path)
        done(f"Shipped {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}")

    if caddyfile:
        step("Configuring web server")
        client = _connect(host, ssh_key_path)
        _write_caddyfile(client, caddyfile)
        client.close()
        done("Caddy configured")
