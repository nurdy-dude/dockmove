<p align="center">
  <img width="460" src="https://github.com/nurdy-dude/dockmove/blob/main/images/dockmove.png">
</p>

# DockMove: Docker Host Migration & Backup Pipeline

**DockMove** is a lightweight, self-contained migration and backup pipeline designed to capture, archive, and redeploy Docker environments on the fly.

By interfacing directly with the host's Docker socket, **DockMove** lets you inspect running containers, package their settings, networks, and data volumes into a unified .zip bundle, and restore them flawlessly on any secondary host running **DockMove**.

---

## Features

* **Automated Discovery:** Scans and parses metadata from the host's `/var/run/docker.sock` Unix socket instantly.
* **Symmetrical Scrollable Dashboard:** Features a clean layout utilizing a custom color identity with standalone containers and Active Compose Stacks neatly encapsulated in space-conscious, scrollable viewports.
* **Dual Sequential Processing Queues:** Select multiple stacks or containers for export, or drop multiple `.zip` archives into the restore zone simultaneously. Tasks are processed one-by-one to prevent CPU and Docker socket congestion.
* **WordPress Auto-Heal Engine:** Automatically patches migrated WordPress environments on the fly. Injects dynamic URL overrides early in the bootstrap phase (`wp-config.php`), disables forced SSL constants, and sanitizes `.htaccess` routing to eliminate post-migration `301 Moved Permanently` loops.
* **Sidecar-powered Extraction:** Spins up temporary Alpine sidecars to extract bind-mount and named-volume states into compressed TAR archives.
* **Dynamic Compose Translation:** Reconstructs container configuration (environment variables, port binds, entrypoints, and network configs) into a standard, clean `docker-compose.yml`.
* **Real-Time Pipeline Feed:** A dedicated layout terminal panel mirrors active host commands and migration logs in real time.

---

## Quick Start
### 1. Run via Docker CLI (Direct)

Launch the tool locally by executing the following command. To interact with your host engine, DockMove must have access to your local Unix socket:

```bash
docker run -d
-p 6767:6767
-v /var/run/docker.sock:/var/run/docker.sock
-v /var/lib/docker/volumes:/var/lib/docker/volumes:ro
--name dockmove
--restart unless-stopped
nurdy-dude/dockmove:latest
```

### 2. Run via Docker Compose

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
>Access the control dashboard by navigating to 
http://localhost:6767.

---

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

---

## Security Best Practices

>[!WARNING]
>Mounting /var/run/docker.sock provides root privileges to the container backend. Anyone who can access the DockMove Web UI can control all containers on your host machine.

<ins>Hardening Your Deployment:</ins>

* **Private Binding:** Do not expose port 6767 directly to the open Internet (0.0.0.0:6767). Change port exposures in your docker-compose.yml to loopback only (127.0.0.1:6767:6767) and access the dashboard using a secure private network, such as Tailscale or WireGuard.
* **Reverse Proxy Credentials:** Put DockMove behind a reverse proxy (like Traefik, Nginx, or Caddy) with Basic Auth or OAuth (e.g., Authelia/Keycloak) configured.
* **Temporary Usage:** We recommend keeping DockMove stopped or suspended, running it only when performing active migrations or backup tasks.

---

## License

Distributed under the MIT License. See [LICENSE](https://choosealicense.com/licenses/mit/) for more information.

