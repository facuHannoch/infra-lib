# Infra Deployment Library — Context and Motivation

I am building an agent orchestration system. The system lets me run coding agents, such as Claude Code, Codex, or other agent backends, inside a repository. It already supports useful execution concepts like running agents locally, in containers, or against a specific workspace such as a Git worktree.

The broader goal is to make agents more useful by giving them a persistent, manageable environment where they can work, expose status, communicate, and be controlled from a web interface. I also have a server layer that lets me connect from other devices, including my phone, through something like Tailscale. This server talks to an ACP/client/adapter layer, which then talks to the underlying agent backend.

The question I have been exploring is whether infrastructure deployment should live inside the agent library itself, inside a higher-level orchestration library, or in a separate dedicated infra library.

The current conclusion is that a separate infra deployment library may make sense.

---

# Why I Want This Library

The core reason is simple:

> I want to make deploying an agent, or an agent system, feel almost as simple as running it locally.

A lot of developers can build web apps or mobile apps but still find infrastructure unpleasant or intimidating. Even experienced developers often dislike setting up cloud resources, VMs, SSH access, firewalls, Docker environments, secrets, ports, domains, and deployment scripts. Tools like Vercel are popular because they hide most of that complexity.

But Vercel is not exactly what I need. My use case is not only “deploy a website”. I want to deploy **agents**: active processes that run inside workspaces, possibly inside containers, possibly on remote VMs, and possibly as part of a larger orchestration system.

So the desired library is not a generic app deployment platform, and not a full cloud provider abstraction. It should be a small, focused deployment layer for turning local agent setups into remotely running agent environments.

---

# Important Mental Model

The key distinction is:

> I am not just deploying a directory.
> I am deploying a runtime unit that may use a directory as its workspace.

An agent is basically a program or process that runs somewhere. To deploy it, I need to define:

* what code or executable should run
* what workspace it should operate on
* what environment it needs
* what command starts it
* what ports, secrets, and files it needs
* where it should run

This applies whether the “agent” is a single process or an entire repo-level system with a server, agent manager, workspace, and multiple agents.

---

# Two Main Deployment Cases

## 1. Deploying a Single Agent

Example:

```bash
agent-infra deploy --agent codex --workspace .
```

Meaning:

* take the current workspace or repo
* provision or reuse a remote environment
* copy or mount the workspace there
* install/build what is needed
* start one agent process or container
* expose whatever interface is needed

This is useful when I want one remote agent working on one repo or task.

---

## 2. Deploying a Whole Agent System

Example:

```bash
agent-infra deploy --stack .
```

Meaning:

* deploy the web server / control server
* deploy the agent manager
* deploy or attach workspaces
* deploy multiple agents if needed
* expose the dashboard or API
* keep the whole agent environment available remotely

This is useful when I want the full agent orchestration system to live on a server, so I can connect from anywhere and manage agents continuously.

---

# Why Separate the Infra Library?

There are potentially two or three layers in the larger project:

## 1. Agent Library

Responsible for agent-specific behavior:

* create agents
* run agents
* manage workspaces
* talk to Claude Code, Codex, etc.
* expose agent status
* handle agent roles, state, chat, files, progress

## 2. Higher-Level Orchestrator

Responsible for coordinating multiple agents or projects:

* create several agents
* assign tasks
* manage agent groups
* decide which agents should run where
* coordinate system-level workflows

## 3. Infra Library

Responsible only for making software run somewhere:

* authenticate with cloud/SSH/local providers
* package code/workspaces
* provision machines or containers
* configure networking
* deploy and start processes
* destroy/stop resources

The reason for extracting the infra layer is that both the agent library and the higher-level orchestrator might need deployment functionality. Instead of duplicating infra logic in both places, they can both call a shared infra library.

---

# What the Infra Library Should Do

At the minimum, it should support:

```ts
deploy(spec)
destroy(deploymentId)
status(deploymentId)
logs(deploymentId)
```

A deployment spec could look conceptually like:

```ts
AgentDeploymentSpec {
  name: string

  source: {
    type: "local-dir" | "git-repo" | "docker-image"
    path?: string
    repoUrl?: string
    image?: string
  }

  runtime: {
    type: "docker" | "host-process"
    startCommand: string
  }

  workspace: {
    type: "copied" | "mounted" | "git-worktree" | "persistent-volume"
    path: string
  }

  env: Record<string, string>

  secrets: string[]

  ports: {
    internal: number
    external?: number
  }[]

  provider: {
    type: "local" | "ssh-vm" | "azure-vm" | "aws-ec2" | "gcp-vm"
  }
}
```

The exact structure can change, but the conceptual pieces are:

* source
* runtime
* workspace
* environment
* secrets
* ports
* provider

---

# Initial Scope

The first version should be intentionally limited.

A good MVP could support:

```bash
agent-infra init
agent-infra deploy
agent-infra status
agent-infra logs
agent-infra destroy
```

And only one or two providers, for example:

* local Docker
* SSH into an existing VM

This avoids building a huge cloud abstraction too early.

A very practical first target would be:

> “I already have a VM. Deploy this agent system to it over SSH.”

That means the library does not initially need to create cloud infrastructure. It only needs to:

* connect to a machine
* copy files or clone repo
* create env files
* run Docker Compose or a start command
* expose ports
* show logs/status

Later, provisioning can be added.

---

# Possible Internal Modules

The library could be structured around modules like:

```txt
src/
  config/
    loadConfig.ts
    schema.ts

  providers/
    local/
    ssh/
    azure/
    aws/

  package/
    createBundle.ts
    dockerBuild.ts
    gitClone.ts

  deploy/
    deployAgent.ts
    deployStack.ts
    destroyDeployment.ts

  runtime/
    dockerRuntime.ts
    hostRuntime.ts

  network/
    ports.ts
    firewall.ts

  secrets/
    envFile.ts
    secretStore.ts

  logs/
    streamLogs.ts

  status/
    getStatus.ts
```

The provider should answer: “Where does this run?”

The runtime should answer: “How does this run?”

The workspace should answer: “What files/data does the agent work on?”

The deployment spec ties those together.

---

# Design Principle

The library should not try to expose every possible infrastructure option. That would recreate Terraform complexity.

Instead, it should have opinionated defaults.

For example, the user should not need to think about 40 cloud settings. In many cases they should only provide:

* app/agent name
* provider
* workspace path
* start command
* ports
* env/secrets

Everything else should have sane defaults.

The goal is not maximum flexibility at first. The goal is:

> make the common path simple and reliable.

Advanced customization can come later.

---

# Relationship to Terraform / Ansible / Docker

The library does not necessarily need to replace Terraform, Ansible, Docker, or Docker Compose.

It can wrap them.

For example:

* Terraform could provision a VM.
* Ansible could configure the VM.
* Docker could package and run the agent.
* Docker Compose could run multi-service stacks.
* The infra library gives a simpler interface over those tools.

The user-facing interface should be closer to:

```bash
agent-infra deploy
```

Not:

```bash
terraform init
terraform apply
ansible-playbook setup.yml
docker compose up -d
```

At least not unless the user wants to go deeper.

---

# Long-Term Vision

Eventually, this library could support a “spawn agent” flow.

For example, a user clicks a button in a UI:

> Start new agent

Behind the scenes, the system could:

* create a container
* attach a repo/workspace
* start the selected agent backend
* register it with the orchestrator
* expose logs/status/chat
* tear it down when done

At small scale, this can run on one VM with Docker.

At larger scale, the same abstraction could target Kubernetes or managed container platforms.

The important part is that the higher-level orchestrator should not care whether the agent runs:

* locally
* in Docker
* on an SSH VM
* on Kubernetes
* on a cloud container service

It should just ask the infra layer to deploy a runtime unit.

---

# Important Open Design Questions

## 1. Is this an “agent deployment” library or a generic deployment library?

Probably start agent-focused.

A generic deployment library becomes too broad. The initial value is clearer if it is designed around agents and workspaces.

## 2. Should it provision infrastructure or only deploy to existing infrastructure?

For v0, probably deploy to existing infrastructure.

Example:

```bash
agent-infra deploy --provider ssh --host my-vm
```

Later:

```bash
agent-infra provision --provider azure
```

Provisioning can be a separate phase.

## 3. Should the unit be called an app, agent, service, runtime unit, or deployment?

Internally, “deployment unit” or “runtime unit” may be more general.

Externally, for this project, “agent” is probably fine.

## 4. How much config should be required?

As little as possible.

Something like:

```yaml
name: my-agent-system

provider:
  type: ssh
  host: my-vm

runtime:
  type: docker-compose
  file: docker-compose.yml

workspace:
  type: git
  repo: git@github.com:user/repo.git

ports:
  - 3000
```

Or for a single agent:

```yaml
name: codex-agent

provider:
  type: ssh
  host: my-vm

runtime:
  type: docker
  image: my-agent-image
  start: codex

workspace:
  type: local-dir
  path: .
```

---

# Simplest First Implementation

The first useful implementation could be:

```bash
agent-infra init
agent-infra deploy --host user@ip --path .
agent-infra logs
agent-infra destroy
```

Under the hood:

1. Read config.
2. Create a deployment bundle.
3. SSH into the VM.
4. Upload files.
5. Write `.env`.
6. Run `docker compose up -d` or a configured start command.
7. Save deployment metadata locally.
8. Provide logs/status commands.

This is enough to prove the abstraction before doing cloud provisioning.

---

# One-Sentence Summary

I want to build a small, opinionated infra deployment library that lets my agent system deploy single agents or full agent stacks to remote environments with minimal friction, by treating an agent as a runtime unit plus workspace, and hiding the repetitive infrastructure steps behind a simple deployment spec and CLI.
