#!/usr/bin/env python3
"""Objective checks for the agent lifecycle test (see agent-lifecycle.md).

This is the *verification* half of the test. The agent drives the lifecycle with
the normal CLI (deploy / pause / resume / down); it calls these subcommands to get
unambiguous PASS/FAIL on the things that must be true at each stage. Everything
here uses only the public Python API + stdlib, which also demonstrates the
programmatic (agent/MCP) path.

Usage:
    python lifecycle_check.py url     NAME
    python lifecycle_check.py listed  NAME
    python lifecycle_check.py http    NAME [--path /] [--insecure]
    python lifecycle_check.py write   NAME --path P --marker TEXT
    python lifecycle_check.py read    NAME --path P --expect TEXT

Exit code is 0 on PASS, 1 on FAIL — so the agent can branch on it.
"""
import argparse
import ssl
import sys
import urllib.request

import infra_lib


def _ok(msg):
    print(f"PASS: {msg}")
    return 0


def _fail(msg):
    print(f"FAIL: {msg}")
    return 1


def _deployment(name):
    d = infra_lib.get(name)
    if not d:
        sys.exit(_fail(f"deployment '{name}' not found (is it deployed?)"))
    return d


def cmd_url(args):
    d = _deployment(args.name)
    print(f"name={d.name} ip={d.ip} url={d.url or '-'} ssh={'yes' if d.ssh_key else 'no'}")
    return 0


def cmd_listed(args):
    names = [d.name for d in infra_lib.list_deployments()]
    return _ok(f"'{args.name}' is listed") if args.name in names \
        else _fail(f"'{args.name}' not in list: {names}")


def cmd_http(args):
    d = _deployment(args.name)
    if not d.url:
        return _fail(f"'{args.name}' has no URL to curl")
    url = d.url.rstrip("/") + args.path
    ctx = ssl._create_unverified_context() if args.insecure else ssl.create_default_context()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "infra-lib/lifecycle"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            code = r.getcode()
        return _ok(f"GET {url} -> {code}") if 200 <= code < 400 \
            else _fail(f"GET {url} -> {code}")
    except Exception as e:
        return _fail(f"GET {url} raised {type(e).__name__}: {e}")


def cmd_write(args):
    # Single-quote the marker so it survives the remote shell verbatim.
    safe = args.marker.replace("'", "'\\''")
    out = infra_lib.run(args.name, f"mkdir -p $(dirname '{args.path}') && printf '%s' '{safe}' > '{args.path}' && echo wrote")
    return _ok(f"wrote marker to {args.path}") if "wrote" in out \
        else _fail(f"write did not confirm: {out!r}")


def cmd_read(args):
    out = infra_lib.run(args.name, f"cat '{args.path}' 2>/dev/null || echo __MISSING__").strip()
    if out == args.expect:
        return _ok(f"{args.path} == {args.expect!r} (survived)")
    if out == "__MISSING__":
        return _fail(f"{args.path} is gone after resume — did the marker land on the *persistent* volume?")
    return _fail(f"{args.path} == {out!r}, expected {args.expect!r}")


def main():
    p = argparse.ArgumentParser(description="Lifecycle test checks (PASS/FAIL, exit 0/1).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("url", help="print the deployment's ip/url/ssh-availability")
    s.add_argument("name"); s.set_defaults(fn=cmd_url)

    s = sub.add_parser("listed", help="assert the deployment shows in `list`")
    s.add_argument("name"); s.set_defaults(fn=cmd_listed)

    s = sub.add_parser("http", help="GET the deployment URL, assert a 2xx/3xx")
    s.add_argument("name"); s.add_argument("--path", default="/")
    s.add_argument("--insecure", action="store_true", help="skip TLS verification")
    s.set_defaults(fn=cmd_http)

    s = sub.add_parser("write", help="write a marker file over SSH")
    s.add_argument("name"); s.add_argument("--path", required=True)
    s.add_argument("--marker", required=True); s.set_defaults(fn=cmd_write)

    s = sub.add_parser("read", help="read the marker back and assert it matches")
    s.add_argument("name"); s.add_argument("--path", required=True)
    s.add_argument("--expect", required=True); s.set_defaults(fn=cmd_read)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
