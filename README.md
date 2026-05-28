

```
pip install -e . -q && infra-lib --help
```

Usage


```
# It creates a vm, and puts the contents of the current directory into the vm
infra-lib deploy --name oneFileProject .
```


1. Provides a way to authenticate so Pulumi can use the cloud provider sdk
2. Lists available options for the resources
3. Provisions the specified infrastructure
4. Transfers the files with `rsync`
    - Allows to run a command (EXPAND INTO ITS OWN MODULE)
5. Configures a domain (if domain is provided)
    - BYOD: adds the provided domain to the caddyfile, generating an automatic HTTPS certificate with Let's encrypt
    **It expects for you to point the domain A record to the IP of the generated VM**
        - if `proxied` is set to true (like what cloudflare does by default), it avoids this step
    - Cloudflare Registrar (TBI)
    - http: no domain is configured, only HTTP works
6. Health
    - wait for url: waits for the resource and pings it ??



- Generated SSH keys are stored in `~/.infra-lib/keys`.
- Credentials are written into `~/.infra-lib/credentials`

```
infra-lib auth
infra-lib deploy
infra-lib down
infra-lib list

infra-lib sizes
```

# Cloud Providers supported

- Azure

# TBI

- Implementing server + mcp, so agents can provision
- Look into api keys or a way of authentication that would allow for
- Implementing a way to define the infra
    - More options
    - UI view ?
- Nice loading indicator, logs of the current step or what the program is doing
- docker containers