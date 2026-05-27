A library for simplifying deployment of agents.

There is not a log of settings to set for deploying an agent:
- Allowing HTTP/S through a ort
- Possibly installing a reverse proxy service
- ...

But there does not seem to be a library that makes this process easy.


---

mkdir test; cd test; touch oneFile; git init; git add .; git commit -m "Initialized test project to deploy"



1. sslip.io


- Python
- Pulumi
- sslip.io

-
---



3. Cloudflare Registrar






1. Azure App Service — you push code, Azure runs it. No Docker required, handles the
HTTP/HTTPS and domain for you. Simplest.
2. Azure Container Apps — you give it a Docker image, it runs it. More portable across   clouds, but requires a Dockerfile.

---

The stack:

- **Language**: Python
- **Provisioning**: Pulumi (Python SDK)
- **VM**: Azure VM
- **Configuration**: cloud-init (installs Caddy on first boot)
- **File serving**: Caddy `file_server`
- **HTTPS/domain**: sslip.io (derived from the VM's public IP, no purchase needed)

The flow for a user of the library:

1. Point the library at a directory
2. Library provisions an Azure VM via Pulumi
3. VM boots, cloud-init installs and configures Caddy
4. Library derives the sslip.io URL from the IP
5. User gets back a URL — visit it, see the files

No domain purchase, no manual SSH, no Azure UI. Just a directory in, a URL out.

Ready to start building?



curl -fsSL https://get.pulumi.com | sh
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash




```
az login --use-device-code
az ad sp create-for-rbac --name infra-lib-sp --role Contributor --scopes /subscriptions/$(az account show --query id -o tsv)

export ARM_CLIENT_ID=<appId>
export ARM_CLIENT_SECRET=<password>
export ARM_TENANT_ID=<tenant>
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)


pip3 install pulumi pulumi-azure-native


pulumi login --local

mkdir -p /root/qlairoslabs/libs/infra-lib/infra && cd /root/qlairoslabs/libs/infra-lib/infra && /root/.pulumi/bin/pulumi new azure-python --name infra-lib --stack dev --yes


ssh-keygen -t rsa -b 4096 -f .ssh/id_rsa -N ""

cd infra && pulumi preview



     pulumi up --yess