**DockMove**: Docker Host Migration & Backup Pipeline

DockMove is a lightweight, self-contained migration and backup pipeline designed to capture, archive, and redeploy Docker environments on the fly.

By interfacing directly with the host's Docker socket, DockMove lets you inspect running containers, package their settings, networks, and data volumes into a unified .zip bundle, and restore them flawlessly on any secondary host running DockMove.

**Features**

Automated Discovery: Scans and parses metadata from the host's /var/run/docker.sock Unix socket instantly.

Sidecar-powered Extraction: Automatically spins up temporary Alpine sidecars to extract bind-mount and named-volume states into compressed TAR archives.

Dynamic Compose Translation: Re-constructs container configuration (environment variables, port binds, entrypoints, and network configs) into a standard, clean docker-compose.yml.

Zero-Dependency Restoration: Upload a .zip archive on a brand-new host to extract volume structures, replicate custom bridges, and bring your containers back online.

Real-Time Pipeline Feed: Full visual dashboard console output mirroring host commands.

**Repository File Structure**

Ensure your GitHub repository has the following files:

├── Dockerfile                  # Builds Python alpine image with Docker CLI client
├── docker-compose.yml          # Local test runner composition
├── main.py                     # Python FastAPI back-end & Docker SDK integration
├── requirements.txt            # Python ecosystem dependencies
├── index.html                  # Lightweight web frontend dashboard
└── README.md                   # This instruction documentation


**Quick Start**

1. Run via Docker CLI (Direct)

You can launch the tool locally by executing a single command. To interact with your host engine, DockMove must have access to your local Unix socket:

docker run -d \
  -p 6767:6767 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes:/var/lib/docker/volumes:ro \
  --name dockmove \
  --restart unless-stopped \
  yourusername/dockmove:latest


2. Run via Docker Compose

Create a docker-compose.yml file and run:

version: "3.8"

services:
  dockmove:
    image: yourusername/dockmove:latest
    container_name: dockmove
    restart: unless-stopped
    ports:
      - "6767:6767"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes:ro
    environment:
      - DOCKER_HOST=unix:///var/run/docker.sock


docker compose up -d


Access the control dashboard by navigating to http://localhost:6767.

**How It Works** (The Mechanics)

             [ Source Host ]                             [ Target Host ]
       ┌────────────────────────┐                   ┌────────────────────────┐
       │   Running Containers   │                   │  Upload .zip Archive   │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │  Inspect socket / API  │                   │ Extract JSON blueprint │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │ Sidecar: tar volumes   │                   │ Restructures networks  │
       └───────────┬────────────┘                   └───────────┬────────────┘
                   │                                            │
       ┌───────────▼────────────┐                   ┌───────────▼────────────┐
       │ Compile to .zip & DL   ├──────────────────►│ Deploy volume payloads │
       └────────────────────────┘                   └───────────┬────────────┘
                                                                │
                                                    ┌───────────▼────────────┐
                                                    │ Spin up target compose │
                                                    └────────────────────────┘


**The Backup Pipeline**

Metadata Querying: DockMove queries the Docker engine API (/containers/{id}/json) to extract full configurations: custom network setups, image hashes, tags, host bindings, and environment arrays.

Sidecar Staging: DockMove boots a lightweight target-architecture alpine scratch space. This sidecar mounts the user-specified volume paths from the parent engine and tars the filesystem state directly to a buffer.

Packaging: Creates an output archive consisting of:

metadata.json: The raw layout configuration schema.

docker-compose.yml: Auto-generated structural config.

volumes/: Individual volume .tar.gz data payloads.

**The Restore Pipeline**

Intake Processing: The uploaded package is uncompressed inside the secure isolated host execution sandbox.

Structural Setup: Dedicated Docker networks are parsed and rebuilt to guarantee seamless host IP routing.

Payload Injection: Docker named volumes are allocated. Temporary recovery sidecars run to unpack the zipped directories.

Composition Launch: Re-launches the container configuration onto the target physical engine.

**Security Best Practices**

[!WARNING]
Mounting /var/run/docker.sock provides root privileges to the container backend. Anyone who can access the DockMove Web UI can control all containers on your host machine.

Hardening Your Deployment:

Private Binding: Do not expose port 6767 directly to the open Internet (0.0.0.0:6767). Change port exposures in your docker-compose.yml to loopback only (127.0.0.1:6767:6767) and access the dashboard using a secure private network, such as Tailscale or WireGuard.

Reverse Proxy Credentials: Put DockMove behind a reverse proxy (like Traefik, Nginx, or Caddy) with Basic Auth or OAuth (e.g., Authelia/Keycloak) configured.

Temporary Usage: We recommend keeping DockMove stopped or suspended, running it only when performing active migrations or backup tasks.

**Contributing**

Contributions make the open-source community an amazing place to learn, inspire, and create. Any contributions you make are greatly appreciated.

Fork the Project

Create your Feature Branch (git checkout -b feature/AmazingFeature)

Commit your Changes (git commit -m 'Add some AmazingFeature')

Push to the Branch (git push origin feature/AmazingFeature)

Open a Pull Request

**License**

Distributed under the MIT License. See LICENSE for more information.
