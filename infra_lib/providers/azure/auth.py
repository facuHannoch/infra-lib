import os
import json
import time
import uuid
import configparser
import urllib.request
import urllib.error

_CREDENTIALS_FILE = os.path.expanduser("~/.infra-lib/credentials")
_CONTRIBUTOR_ROLE = "b24988ac-6180-42a0-ab88-20f7382dd24c"


def _load(provider: str) -> dict:
    config = configparser.ConfigParser()
    config.read(_CREDENTIALS_FILE)
    return dict(config[provider]) if provider in config else {}


def _save(provider: str, creds: dict):
    config = configparser.ConfigParser()
    config.read(_CREDENTIALS_FILE)
    config[provider] = creds
    os.makedirs(os.path.dirname(_CREDENTIALS_FILE), exist_ok=True)
    with open(_CREDENTIALS_FILE, "w") as f:
        config.write(f)
    os.chmod(_CREDENTIALS_FILE, 0o600)


def _http_json(method: str, url: str, token: str, body: dict = None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        # The useful detail (e.g. Authorization_RequestDenied) is in the body.
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Azure request failed ({e.code} {method} {url}):\n{detail}") from e


def _graph_request(method: str, path: str, token: str, body: dict = None):
    return _http_json(method, f"https://graph.microsoft.com/v1.0{path}", token, body)


def _arm_request(method: str, url: str, token: str, body: dict = None):
    return _http_json(method, url, token, body)


def _decode_jwt_payload(token: str) -> dict:
    import base64
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def auth_azure():
    from azure.identity import DeviceCodeCredential

    credential = DeviceCodeCredential(additionally_allowed_tenants=["*"])

    # get initial token (triggers device code flow once)
    arm_token = credential.get_token("https://management.azure.com/.default").token

    # discover all tenants this user has access to
    tenants_data = _arm_request("GET", "https://management.azure.com/tenants?api-version=2022-12-01", arm_token)
    tenant_ids = [t["tenantId"] for t in tenants_data.get("value", [])]

    # collect subscriptions across all tenants
    raw_subs = []
    tenant_id = _decode_jwt_payload(arm_token).get("tid")
    tenant_tokens = {tenant_id: arm_token}

    for tid in tenant_ids:
        try:
            tok = credential.get_token("https://management.azure.com/.default", tenant_id=tid).token
            tenant_tokens[tid] = tok
            subs_data = _arm_request("GET", "https://management.azure.com/subscriptions?api-version=2022-12-01", tok)
            for s in subs_data.get("value", []):
                s["_tenant_id"] = tid
                raw_subs.append(s)
        except Exception:
            pass

    if not raw_subs:
        raise RuntimeError("No Azure subscriptions found.")

    if len(raw_subs) == 1:
        raw_sub = raw_subs[0]
    else:
        print("Available subscriptions:")
        for i, s in enumerate(raw_subs):
            print(f"  {i+1}. {s['displayName']} ({s['subscriptionId']})")
        try:
            choice = int(input("Select: "))
        except ValueError:
            raise RuntimeError("Invalid selection: expected a number.")
        if not 1 <= choice <= len(raw_subs):
            raise RuntimeError(f"Selection out of range (1-{len(raw_subs)}).")
        raw_sub = raw_subs[choice - 1]

    subscription_id = raw_sub["subscriptionId"]
    tenant_id = raw_sub["_tenant_id"]
    graph_token = credential.get_token("https://graph.microsoft.com/.default", tenant_id=tenant_id).token
    arm_token = tenant_tokens.get(tenant_id, credential.get_token("https://management.azure.com/.default", tenant_id=tenant_id).token)

    # create app registration
    app_name = f"infra-lib-{uuid.uuid4().hex[:8]}"
    app = _graph_request("POST", "/applications", graph_token, {"displayName": app_name})
    app_id = app["appId"]
    app_object_id = app["id"]

    # create service principal
    sp = _graph_request("POST", "/servicePrincipals", graph_token, {"appId": app_id})
    sp_object_id = sp["id"]

    # create client secret
    secret = _graph_request(
        "POST",
        f"/applications/{app_object_id}/addPassword",
        graph_token,
        {"passwordCredential": {"displayName": "infra-lib"}},
    )
    client_secret = secret["secretText"]

    # assign Contributor role. The SP may not have propagated through AAD yet,
    # so Azure can return PrincipalNotFound — retry until it settles.
    role_assignment_id = str(uuid.uuid4())
    role_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Authorization/roleAssignments/{role_assignment_id}?api-version=2022-04-01"
    )
    role_body = {
        "properties": {
            "roleDefinitionId": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{_CONTRIBUTOR_ROLE}",
            "principalId": sp_object_id,
            "principalType": "ServicePrincipal",
        }
    }
    for attempt in range(6):
        try:
            _arm_request("PUT", role_url, arm_token, role_body)
            break
        except RuntimeError as e:
            if "PrincipalNotFound" in str(e) and attempt < 5:
                time.sleep(5)
                continue
            raise

    _save("azure", {
        "client_id": app_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
    })

    print("Authenticated successfully.")
    print(f"  Subscription: {raw_sub['displayName']} ({subscription_id})")
    print(f"  Credentials saved to {_CREDENTIALS_FILE}")


def save_azure_credentials(client_id: str, client_secret: str, tenant_id: str,
                           subscription_id: str, validate: bool = True) -> str:
    """Non-interactive auth: persist an *existing* service principal.

    For headless/agent use where someone already has an SP (and doesn't want the
    device-code flow). Validates the creds against Azure before saving unless
    validate=False. Returns the path the credentials were written to.
    """
    fields = {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
    }
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise ValueError(f"missing required credential(s): {', '.join(missing)}")

    if validate:
        from azure.identity import ClientSecretCredential
        try:
            cred = ClientSecretCredential(
                tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
            )
            cred.get_token("https://management.azure.com/.default")
        except Exception as e:
            raise RuntimeError(f"Azure rejected the credentials: {e}") from e

    _save("azure", fields)
    return _CREDENTIALS_FILE


def load_azure_credentials():
    # env vars take priority (CI/CD use case)
    if os.environ.get("ARM_CLIENT_ID"):
        return

    creds = _load("azure")
    if not creds:
        raise RuntimeError("No Azure credentials found. Run 'infra-lib auth azure' first.")

    os.environ["ARM_CLIENT_ID"] = creds["client_id"]
    os.environ["ARM_CLIENT_SECRET"] = creds["client_secret"]
    os.environ["ARM_TENANT_ID"] = creds["tenant_id"]
    os.environ["ARM_SUBSCRIPTION_ID"] = creds["subscription_id"]
