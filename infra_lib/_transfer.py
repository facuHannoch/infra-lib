import os
import subprocess
import time
import paramiko

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
    while time.time() < deadline:
        try:
            _connect(host, ssh_key_path).close()
            return
        except Exception:
            time.sleep(5)
    raise TimeoutError(f"SSH not available on {host} after {timeout}s")


def _wait_for_cloud_init(host: str, ssh_key_path: str):
    client = _connect(host, ssh_key_path)
    _, stdout, _ = client.exec_command("cloud-init status --wait")
    stdout.channel.recv_exit_status()
    client.close()


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


def run_command(host: str, command: str, ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    _wait_for_cloud_init(host, ssh_key_path)
    client = _connect(host, ssh_key_path)
    _, stdout, stderr = client.exec_command(command, get_pty=True)
    for line in stdout:
        print(line, end="", flush=True)
    exit_code = stdout.channel.recv_exit_status()
    client.close()
    if exit_code != 0:
        raise RuntimeError(f"Install command failed (exit {exit_code})")


def transfer(host: str, source_dir: str = None, caddyfile: str = None, ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    _wait_for_cloud_init(host, ssh_key_path)

    if source_dir:
        client = _connect(host, ssh_key_path)
        dir_name = os.path.basename(source_dir.rstrip("/"))
        _, _, stderr = client.exec_command(f"sudo mkdir -p /srv/files/{dir_name} && sudo chown -R azureuser:azureuser /srv/files")
        stderr.channel.recv_exit_status()
        client.close()

        cmd = [
            "rsync", "-az", "--delete",
            "--filter=:- .gitignore",
        ]
        for pattern in _ALWAYS_EXCLUDE:
            cmd += ["--exclude", pattern]
        dir_name = os.path.basename(source_dir.rstrip("/"))
        cmd += [
            "-e", f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no",
            source_dir.rstrip("/") + "/",
            f"azureuser@{host}:/srv/files/{dir_name}/",
        ]
        subprocess.run(cmd, check=True)

    if caddyfile:
        client = _connect(host, ssh_key_path)
        _write_caddyfile(client, caddyfile)
        client.close()
