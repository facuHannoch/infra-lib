import os
import json
import uuid
import configparser
import urllib.request

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


def _graph_request(method: str, path: str, token: str, body: dict = None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"https://graph.microsoft.com/v1.0{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()) if resp.length != 0 else {}


def _arm_request(method: str, url: str, token: str, body: dict = None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()) if resp.length != 0 else {}


def auth_azure():
    from azure.identity import DeviceCodeCredential
    from azure.mgmt.subscription import SubscriptionClient

    credential = DeviceCodeCredential()

    # pick subscription
    sub_client = SubscriptionClient(credential)
    subscriptions = list(sub_client.subscriptions.list())
    if not subscriptions:
        raise RuntimeError("No Azure subscriptions found.")
    if len(subscriptions) == 1:
        sub = subscriptions[0]
    else:
        print("Available subscriptions:")
        for i, s in enumerate(subscriptions):
            print(f"  {i+1}. {s.display_name} ({s.subscription_id})")
        sub = subscriptions[int(input("Select: ")) - 1]

    subscription_id = sub.subscription_id
    tenant_id = sub.tenant_id

    graph_token = credential.get_token("https://graph.microsoft.com/.default").token
    arm_token = credential.get_token("https://management.azure.com/.default").token

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

    # assign Contributor role
    role_assignment_id = str(uuid.uuid4())
    _arm_request(
        "PUT",
        f"https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleAssignments/{role_assignment_id}?api-version=2022-04-01",
        arm_token,
        {
            "properties": {
                "roleDefinitionId": f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{_CONTRIBUTOR_ROLE}",
                "principalId": sp_object_id,
                "principalType": "ServicePrincipal",
            }
        },
    )

    _save("azure", {
        "client_id": app_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
    })

    print(f"Authenticated successfully.")
    print(f"  Subscription: {sub.display_name} ({subscription_id})")
    print(f"  Credentials saved to {_CREDENTIALS_FILE}")


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
