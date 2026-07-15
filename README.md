
# DockMove: Docker Host Migration & Backup Pipeline

**DockMove** is a lightweight, self-contained migration and backup pipeline designed to capture, archive, and redeploy Docker environments on the fly.

By interfacing directly with the host's Docker socket, **DockMove** lets you inspect running containers, package their settings, networks, and data volumes into a unified .zip bundle, and restore them flawlessly on any secondary host running **DockMove**.


## Features

- <ins>Automated Discovery:</ins> Scans and parses metadata from the host's /var/run/docker.sock Unix socket instantly.
- <ins>Sidecar-powered Extraction:</ins> Automatically spins up temporary Alpine sidecars to extract bind-mount and named-volume states into compressed TAR archives.
- <ins>Dynamic Compose Translation:</ins> Re-constructs container configuration (environment variables, port binds, entrypoints, and network configs) into a standard, clean docker-compose.yml.
- <ins>Zero-Dependency Restoration:</ins> Upload a .zip archive on a brand-new host to extract volume structures, replicate custom bridges, and bring your containers back online.
- <ins>Real-Time Pipeline Feed:</ins> Full visual dashboard console output mirroring host commands.


## Quick Start
1. <ins>Run via Docker CLI (Direct)</ins>

You can launch the tool locally by executing a single command. To interact with your host engine, DockMove must have access to your local Unix socket:

```bash
docker run -d
-p 6767:6767
-v /var/run/docker.sock:/var/run/docker.sock
-v /var/lib/docker/volumes:/var/lib/docker/volumes:ro
--name dockmove
--restart unless-stopped
nurdy-dude/dockmove:latest
```

2. <ins>Run via Docker Compose</ins>

Create a docker-compose.yml file and run:

```bash
version: "3.8"

services: dockmove: image: nurdy-dude/dockmove:latest container_name: dockmove restart: unless-stopped ports: - "6767:6767" volumes: - /var/run/docker.sock:/var/run/docker.sock - /var/lib/docker/volumes:/var/lib/docker/volumes:ro environment: - DOCKER_HOST=unix:///var/run/docker.sock
```
Start the container by running

```bash
docker compose up -d
```

>[!TIP]
>Access the control dashboard by navigating to http://localhost:6767.

## How It Works

           [ Source Host ]                             [ Target Host ]
       ┌────────────────────────┐                   ┌────────────────────────┐
       │   Running Containers   │                   │  Upload .zip Archive   │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │   Inspect Stack & DB   │                   │ Extract JSON blueprint │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │  Freeze Image Layers   │                   │ Side-loads frozen imgs │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │ Sidecar: tar volumes   │                   │ Recreates custom net   │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │ Compile to .zip & DL   ├──────────────────►│ Injects volume state   │
       └────────────────────────┘                   └───────────┬────────────┘
                                                                │
                                                    ┌───────────▼────────────┐
                                                    │ Spin up target stack   │
                                                    └────────────────────────┘

## Security Best Practices

>[!WARNING]
>Mounting /var/run/docker.sock provides root privileges to the container backend. Anyone who can access the DockMove Web UI can control all containers on your host machine.

<ins>Hardening Your Deployment:</ins>

Private Binding: *Do not expose port 6767 directly to the open Internet (0.0.0.0:6767). Change port exposures in your docker-compose.yml to loopback only (127.0.0.1:6767:6767) and access the dashboard using a secure private network, such as Tailscale or WireGuard.*

Reverse Proxy Credentials: *Put DockMove behind a reverse proxy (like Traefik, Nginx, or Caddy) with Basic Auth or OAuth (e.g., Authelia/Keycloak) configured.*

Temporary Usage: *We recommend keeping DockMove stopped or suspended, running it only when performing active migrations or backup tasks.*

## License

Distributed under the MIT License. See [LICENSE](https://choosealicense.com/licenses/mit/) for more information.

