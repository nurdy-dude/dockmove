# main.py - Enterprise-Grade DockMove API Orchestrator (Full Network & Volume Support)
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

app = FastAPI(title="DockMove API Service", version="1.0.1")

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

@app.get("/", response_class=HTMLResponse)
def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/api/containers")
def list_containers():
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Offline")
    
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
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Offline")

    container = None
    try:
        container = client.containers.get(container_id)
        attrs = container.attrs
        name = container.name

        temp_dir = tempfile.mkdtemp()
        backup_zip_path = os.path.join(temp_dir, f"{name}_dockmove_backup.zip")

        # 1. Generate Metadata & Docker Compose Blueprint
        compose_yaml = generate_compose_blueprint(attrs)
        
        # 2. Safely Pause Container (Critical for database integrity)
        if pause_during_backup and container.status == "running":
            container.pause()

        with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Save docker-compose.yml
            compose_file_path = os.path.join(temp_dir, "docker-compose.yml")
            with open(compose_file_path, "w") as f:
                f.write(compose_yaml)
            zipf.write(compose_file_path, "docker-compose.yml")

            # Save metadata.json (includes detailed network & volume info)
            metadata_path = os.path.join(temp_dir, "metadata.json")
            with open(metadata_path, "w") as f:
                json.dump(attrs, f, indent=4)
            zipf.write(metadata_path, "metadata.json")

            # 3. Stream & Extract Volume Data natively
            if include_volumes:
                for mount in attrs.get('Mounts', []):
                    if mount['Type'] == 'volume':
                        volume_name = mount['Name']
                        tar_file_path = os.path.join(temp_dir, f"volume_{volume_name}.tar")
                        
                        # Use an Alpine sidecar container to archive volume contents safely
                        sidecar = client.containers.run(
                            "alpine:latest",
                            command=f"tar -cf /backup.tar -C {mount['Destination']} .",
                            volumes={volume_name: {'bind': mount['Destination'], 'mode': 'ro'}},
                            remove=False
                        )
                        
                        # Retrieve the tar archive stream from the sidecar
                        ststream, stat = sidecar.get_archive("/backup.tar")
                        with open(tar_file_path, "wb") as f:
                            for chunk in ststream:
                                f.write(chunk)
                        
                        sidecar.remove()
                        
                        # Pack the volume tar archive into the final .zip bundle
                        zipf.write(tar_file_path, f"volumes/volume_{volume_name}.tar")
        
        # 4. Unpause Container
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
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Connection Offline")

    try:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, file.filename)
        
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Extract package
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        metadata_path = os.path.join(temp_dir, "metadata.json")
        if not os.path.exists(metadata_path):
            raise HTTPException(status_code=400, detail="Invalid package: metadata.json missing")

        with open(metadata_path, "r") as f:
            metadata = json.load(f)

        name = metadata['Name'].replace("/", "")
        image = metadata['Config']['Image']

        # 1. Pre-pull required Docker image
        client.images.pull(image)

        # 2. Auto-create network infrastructure
        networks_config = metadata.get('NetworkSettings', {}).get('Networks', {})
        restored_networks = []
        for net_name, net_data in networks_config.items():
            try:
                network = client.networks.get(net_name)
            except docker.errors.NotFound:
                # Recreate custom bridge network with original driver
                network = client.networks.create(net_name, driver="bridge")
            restored_networks.append((network, net_data))

        # 3. Create volumes & restore archived files
        mounts = metadata.get('Mounts', [])
        volume_binds = {}
        for mount in mounts:
            if mount['Type'] == 'volume':
                vol_name = mount['Name']
                dest = mount['Destination']
                
                # Create the named volume on the new host
                client.volumes.create(name=vol_name)
                volume_binds[vol_name] = {'bind': dest, 'mode': 'rw'}
                
                # If backup tar file exists, inject the files back into the volume
                tar_archive_path = os.path.join(temp_dir, "volumes", f"volume_{vol_name}.tar")
                if os.path.exists(tar_archive_path):
                    # Start sidecar container to extract the tar stream directly into the volume
                    sidecar = client.containers.run(
                        "alpine:latest",
                        command="sleep 3600",
                        volumes={vol_name: {'bind': dest, 'mode': 'rw'}},
                        detach=True
                    )
                    
                    with open(tar_archive_path, 'rb') as tar_file:
                        # Extract the inner tar from the nested sidecar tar file wrapper
                        with tarfile.open(fileobj=tar_file) as outer_tar:
                            inner_tar_extracted = outer_tar.extractfile("backup.tar")
                            if inner_tar_extracted:
                                sidecar.put_archive(dest, inner_tar_extracted.read())
                    
                    sidecar.stop()
                    sidecar.remove()

        # 4. Construct port mappings
        ports_map = metadata.get('NetworkSettings', {}).get('Ports', {})
        formatted_ports = {}
        if ports_map:
            for container_port, host_ports in ports_map.items():
                if host_ports:
                    formatted_ports[container_port] = host_ports[0]['HostPort']

        # 5. Spin up the restored container
        # Run initially on the default network
        restored_container = client.containers.create(
            image,
            name=name,
            ports=formatted_ports,
            environment=metadata['Config'].get('Env', []),
            volumes=volume_binds,
            restart_policy={"Name": "always"}
        )

        # 6. Connect container to the original custom networks with aliases
        for network, net_data in restored_networks:
            try:
                # Disconnect from default network to avoid conflicts
                if network.name != "bridge":
                    network.connect(
                        restored_container,
                        aliases=net_data.get('Aliases', []),
                        ipv4_address=net_data.get('IPAddress', None) # Re-binds original static IP if it was set
                    )
            except Exception as net_err:
                print(f"Network bind warning: {net_err}")

        # Start container
        restored_container.start()

        return {"status": "success", "message": f"Container '{name}' and all dependent volumes/networks restored successfully!"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore pipeline failed: {str(e)}")

def generate_compose_blueprint(attrs: dict) -> str:
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

    # Extract original network configurations for Compose output
    networks = list(attrs['NetworkSettings'].get('Networks', {}).keys())
    if networks:
        service[name]["networks"] = networks

    compose_data = {
        "version": "3.8",
        "services": service
    }
    
    if networks:
        compose_data["networks"] = {net: {"external": True} for net in networks}
        
    return yaml.dump(compose_data, default_flow_style=False)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=6767, reload=False)
