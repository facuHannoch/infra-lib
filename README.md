

```
pip install -e . -q && infra-lib --help
```

Usage


```
# It creates a vm, and puts the contents of the current directory into the vm
infra-lib deploy --name oneFileProject .

infra-lib deploy --name projectWithDomain --domain domain.com


#infra-lib down $(infra-lib list -n)


infra-lib down test1              # destroys + removes history (default)
infra-lib down test1 --keep-history  # destroys but keeps Pulumi stack state
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

- It always searches for an `infra.yml` in the current directory. CLI flags override the values written in the yml


- Generated SSH keys are stored in `~/.infra-lib/keys`.
- Credentials are written into `~/.infra-lib/credentials`
- files are stored in `/srv/files`

```
infra-lib auth
infra-lib deploy
infra-lib down
infra-lib list

infra-lib sizes



# No config — just prompts, works anywhere
infra-lib deploy

# Opens $EDITOR with a template, deploy after save
infra-lib deploy --config

# Use a specific file
infra-lib deploy --config ~/templates/bun-app.yml
infra-lib deploy --config ./infra.yml

The editor opens with a pre-filled template using the --name you passed (or myapp). Save and quit, it deploys immediately. If you close without
saving anything meaningful it'll just fall through to prompts.

```


● ps aux | grep bun

  Or if you want to see it by port:
  ss -tlnp | grep 3409

  To see logs:
  tail -f /srv/logs/hub.log

  To kill it:
  kill $(ps aux | grep 'bun run' | grep -v grep | awk '{print $2}')




# Cloud Providers supported

- Azure

# TBI

- Implementing server + mcp, so agents can provision
- Look into api keys or a way of authentication that would allow for
- Implementing a way to define the infra
    - More options
    - UI view ?
- Nice loading indicator, logs of the current step or what the program is doing
    - we should maybe log pulumi output (or give an option to do so) ?
- docker containers

- If I cancel, then I have to do pulumi cancel





# AI-explanation