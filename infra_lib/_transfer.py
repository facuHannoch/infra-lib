import os
import time
import paramiko

_DEFAULT_SSH_KEY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.ssh/id_rsa")


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


def _write_caddyfile(client: paramiko.SSHClient, caddyfile: str):
    sftp = client.open_sftp()
    with sftp.file("/home/azureuser/Caddyfile.tmp", "w") as f:
        f.write(caddyfile)
    sftp.close()
    _, _, stderr = client.exec_command(
        "sudo mv /home/azureuser/Caddyfile.tmp /etc/caddy/Caddyfile && sudo systemctl restart caddy"
    )
    stderr.channel.recv_exit_status()


def transfer(host: str, source_dir: str, caddyfile: str = None, ssh_key_path: str = None):
    ssh_key_path = os.path.abspath(ssh_key_path or _DEFAULT_SSH_KEY)
    _wait_for_ssh(host, ssh_key_path)
    client = _connect(host, ssh_key_path)

    _, _, stderr = client.exec_command("sudo mkdir -p /srv/files && sudo chown azureuser:azureuser /srv/files")
    stderr.channel.recv_exit_status()

    sftp = client.open_sftp()
    for filename in os.listdir(source_dir):
        local_path = os.path.join(source_dir, filename)
        if os.path.isfile(local_path):
            sftp.put(local_path, f"/srv/files/{filename}")
    sftp.close()

    if caddyfile:
        _write_caddyfile(client, caddyfile)

    client.close()
