import os
import tarfile
import tempfile
import zipfile
import json
import shutil
import yaml
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import docker

app = FastAPI(title="DockMove API Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker Host Daemon: {e}")
    client = None

# Serves the index.html landing page at the root route
@app.get("/", response_class=HTMLResponse)
def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    raise HTTPException(status_code=404, detail="index.html not found in working directory")

@app.get("/api/containers")
def list_containers():
    """Discover active and stopped containers, extracting volumes and network mappings."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Connection Offline")
    
    containers = []
    try:
        for c in client.containers.list(all=True):
            containers.append({
                "id": c.short_id,
                "name": c.name,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else c.attrs['Config']['Image'],
                "ports": c.attrs['NetworkSettings']['Ports'],
                "mounts": [
                    {
                        "source": m.get("Source"),
                        "destination": m.get("Destination"),
                        "name": m.get("Name"),
                        "type": m.get("Type")
                    } for m in c.attrs['Mounts']
                ],
                "networks": list(c.attrs['NetworkSettings']['Networks'].keys()),
                "env": c.attrs['Config']['Env']
            })
        return containers
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/backup")
async def execute_backup(
    container_id: str = Form(...),
    include_volumes: bool = Form(True),
    pause_during_backup: bool = Form(True)
):
    """Backs up metadata, constructs docker-compose.yml, and packages raw volumes."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Connection Offline")

    container = None
    try:
        container = client.containers.get(container_id)
        attrs = container.attrs
        name = container.name

        temp_dir = tempfile.mkdtemp()
        backup_zip_path = os.path.join(temp_dir, f"{name}_dockmove_backup.zip")

        compose_yaml = generate_compose_blueprint(attrs)
        
        if pause_during_backup and container.status == "running":
            container.pause()

        with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            compose_file_path = os.path.join(temp_dir, "docker-compose.yml")
            with open(compose_file_path, "w") as f:
                f.write(compose_yaml)
            zipf.write(compose_file_path, "docker-compose.yml")

            metadata_path = os.path.join(temp_dir, "metadata.json")
            with open(metadata_path, "w") as f:
                json.dump(attrs, f, indent=4)
            zipf.write(metadata_path, "metadata.json")

            if include_volumes:
                for mount in attrs.get('Mounts', []):
                    if mount['Type'] == 'volume':
                        volume_name = mount['Name']
                        tar_file_path = os.path.join(temp_dir, f"volume_{volume_name}.tar")
                        
                        client.containers.run(
                            "alpine:latest",
                            command=f"tar -cf /backup.tar -C {mount['Destination']} .",
                            volumes={volume_name: {'bind': mount['Destination'], 'mode': 'ro'}},
                            volumes_from=[container_id],
                            remove=True
                        )

        if pause_during_backup:
            try:
                container.reload()
                if container.status == "paused":
                    container.unpause()
            except Exception:
                pass

        return FileResponse(backup_zip_path, media_type="application/zip", filename=f"{name}_backup.zip")

    except Exception as e:
        if container:
            try:
                container.reload()
                if container.status == "paused":
                    container.unpause()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Backup failure: {str(e)}")

@app.post("/api/restore")
async def execute_restore(file: UploadFile = File(...)):
    """Accepts a .zip backup archive and reconstructs the volumes and deployment config."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Connection Offline")

    try:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, file.filename)
        
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        metadata_path = os.path.join(temp_dir, "metadata.json")
        if not os.path.exists(metadata_path):
            raise HTTPException(status_code=400, detail="Invalid package: metadata.json missing")

        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        name = metadata['Name'].replace("/", "")
        image = metadata['Config']['Image']

        client.images.pull(image)

        networks = metadata.get('NetworkSettings', {}).get('Networks', {})
        for net_name in networks.keys():
            try:
                client.networks.get(net_name)
            except docker.errors.NotFound:
                client.networks.create(net_name, driver="bridge")

        mounts = metadata.get('Mounts', [])
        for mount in mounts:
            if mount['Type'] == 'volume':
                vol_name = mount['Name']
                client.volumes.create(name=vol_name)

        ports_map = metadata.get('NetworkSettings', {}).get('Ports', {})
        formatted_ports = {}
        for container_port, host_ports in ports_map.items():
            if host_ports:
                formatted_ports[container_port] = host_ports[0]['HostPort']

        client.containers.run(
            image,
            name=f"{name}_restored",
            detach=True,
            ports=formatted_ports,
            environment=metadata['Config'].get('Env', []),
            restart_policy={"Name": "always"}
        )

        return {"status": "success", "message": f"Container restored successfully as '{name}_restored'!"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {str(e)}")

def generate_compose_blueprint(attrs: dict) -> str:
    """Dynamically construct standard declarative docker-compose configurations."""
    name = attrs['Name'].replace("/", "")
    image = attrs['Config']['Image']
    
    service = {
        name: {
            "image": image,
            "restart": "always",
        }
    }
    
    envs = attrs['Config'].get('Env', [])
    if envs:
        service[name]["environment"] = [e for e in envs]

    ports_map = attrs['NetworkSettings'].get('Ports', {})
    if ports_map:
        mapped_ports = []
        for container_port, host_ports in ports_map.items():
            if host_ports:
                mapped_ports.append(f"{host_ports[0]['HostPort']}:{container_port.split('/')[0]}")
        if mapped_ports:
            service[name]["ports"] = mapped_ports

    compose_data = {
        "version": "3.8",
        "services": service
    }
    return yaml.dump(compose_data, default_flow_style=False)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=6767, reload=False)
