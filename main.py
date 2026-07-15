# main.py - Enterprise-Grade DockMove API Orchestrator (With WordPress Auto-Heal)
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

app = FastAPI(title="DockMove API Service", version="2.2.0")

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


def check_for_unsafe_tags(image_string: str) -> bool:
    """
    Returns True if the image uses ':latest' or has no explicit tag version,
    which implicitly defaults to 'latest'.
    """
    if ":" not in image_string:
        return True  # e.g., "mysql" defaults to mysql:latest
    
    parts = image_string.split(":")
    tag = parts[-1]
    
    # Check if the tag refers specifically to latest
    if tag.lower() == "latest":
        return True
        
    return False


def heal_wp_config(file_path: str):
    """
    Injects dynamic HTTP_HOST detection into wp-config.php right after the opening <?php tag
    to guarantee it executes before WordPress boots (wp-settings.php). This prevents port/IP 
    redirection lockouts when migrating between servers.
    """
    try:
        with open(file_path, 'r', errors='ignore') as f:
            content = f.read()
        
        # Guard clause: Don't double inject if already auto-healed
        if "WP_HOME" in content and "$_SERVER['HTTP_HOST']" in content:
            return

        override_code = (
            "\n"
            "/** Added dynamically by DockMove Auto-Heal to support multi-port migration **/\n"
            "if (isset($_SERVER['HTTP_HOST'])) {\n"
            "    define('WP_HOME', 'http://' . $_SERVER['HTTP_HOST']);\n"
            "    define('WP_SITEURL', 'http://' . $_SERVER['HTTP_HOST']);\n"
            "}\n"
            "define('FORCE_SSL_ADMIN', false);\n"
            "define('FORCE_SSL_LOGIN', false);\n"
        )
        
        # Inject immediately after the opening <?php tag (guarantees early boot execution)
        if "<?php" in content:
            content = content.replace("<?php", "<?php" + override_code, 1)
        else:
            # Fallback if no <?php tag is found
            content = override_code + content
            
        with open(file_path, 'w') as f:
            f.write(content)
        print(f"[DockMove Auto-Heal] Successfully updated dynamic URL rules in: {file_path}")
    except Exception as e:
        print(f"[DockMove Auto-Heal] Error patching wp-config.php: {e}")


def heal_htaccess(file_path: str):
    """
    Scans the .htaccess file and comments out any active RewriteRules 
    forcing HTTPS redirection. This keeps connections plain-text HTTP.
    """
    try:
        with open(file_path, 'r', errors='ignore') as f:
            lines = f.readlines()
        
        modified = False
        new_lines = []
        for line in lines:
            strip_line = line.strip().lower()
            # Comment out HTTPS-forcing rewrites
            if "https://" in strip_line and "rewriterule" in strip_line:
                new_lines.append("# " + line)
                modified = True
            elif "rewritecond" in strip_line and "%{https}" in strip_line:
                new_lines.append("# " + line)
                modified = True
            else:
                new_lines.append(line)
                
        if modified:
            with open(file_path, 'w') as f:
                f.writelines(new_lines)
            print(f"[DockMove Auto-Heal] Commented out HTTPS-forcing rules in: {file_path}")
    except Exception as e:
        print(f"[DockMove Auto-Heal] Error patching .htaccess: {e}")


def prepare_and_heal_volume(temp_dir: str, vol_filename: str):
    """
    Inspects volume raw structures, unpackages inner backup payload, runs the 
    auto-heal algorithms (WordPress/wp-config), and repacks cleanly for restoration.
    """
    tar_archive_path = os.path.join(temp_dir, "volumes", vol_filename)
    if not os.path.exists(tar_archive_path):
        return
        
    vol_extract_temp = os.path.join(temp_dir, f"extracted_{vol_filename.replace('.', '_')}")
    os.makedirs(vol_extract_temp, exist_ok=True)
    
    try:
        # Extract outer nested payload (contains the container's sidecar-produced backup.tar)
        with tarfile.open(tar_archive_path, 'r') as outer_tar:
            outer_tar.extractall(vol_extract_temp)
        
        inner_tar_path = os.path.join(vol_extract_temp, "backup.tar")
        if os.path.exists(inner_tar_path):
            files_extract_temp = os.path.join(vol_extract_temp, "files")
            os.makedirs(files_extract_temp, exist_ok=True)
            
            # Extract actual volume files
            with tarfile.open(inner_tar_path, 'r') as inner_tar:
                inner_tar.extractall(files_extract_temp)
            
            # Scan files for WordPress configurations to heal
            wp_config_healed = False
            for root, dirs, files in os.walk(files_extract_temp):
                if "wp-config.php" in files:
                    wp_path = os.path.join(root, "wp-config.php")
                    heal_wp_config(wp_path)
                    wp_config_healed = True
                if ".htaccess" in files:
                    ht_path = os.path.join(root, ".htaccess")
                    heal_htaccess(ht_path)
            
            # If we auto-healed a config file, repack the tarballs so they get restored properly
            if wp_config_healed:
                os.remove(inner_tar_path)
                with tarfile.open(inner_tar_path, 'w') as inner_tar:
                    inner_tar.add(files_extract_temp, arcname=".")
                
                os.remove(tar_archive_path)
                with tarfile.open(tar_archive_path, 'w') as outer_tar:
                    outer_tar.add(inner_tar_path, arcname="backup.tar")
                    
    except Exception as e:
        print(f"[DockMove Auto-Heal] Warning: Error during volume scanning: {e}")


@app.get("/", response_class=HTMLResponse)
def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    raise HTTPException(status_code=404, detail="index.html not found")


@app.get("/api/containers")
def list_containers():
    """Discover active and stopped containers, extracting volumes and network mappings."""
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


@app.get("/api/projects")
def list_compose_projects():
    """Groups active containers by project and flags unsafe images with a warning."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Offline")
    
    projects = {}
    try:
        for c in client.containers.list(all=True):
            project_name = c.labels.get("com.docker.compose.project")
            if project_name:
                if project_name not in projects:
                    projects[project_name] = {
                        "name": project_name,
                        "has_warnings": False,
                        "containers": []
                    }
                
                image_name = c.image.tags[0] if c.image.tags else c.attrs['Config']['Image']
                is_unsafe = check_for_unsafe_tags(image_name)
                
                if is_unsafe:
                    projects[project_name]["has_warnings"] = True
                
                projects[project_name]["containers"].append({
                    "id": c.short_id,
                    "name": c.name,
                    "service": c.labels.get("com.docker.compose.service"),
                    "status": c.status,
                    "image": image_name,
                    "unsafe_tag_warning": is_unsafe
                })
        return list(projects.values())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backup")
def execute_backup(
    container_id: str = Form(...),
    include_volumes: bool = Form(True),
    pause_during_backup: bool = Form(True)
):
    """Backs up a single container's configurations and volume structures."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Offline")

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
                        
                        sidecar = client.containers.run(
                            "alpine:latest",
                            command=f"tar -cf /backup.tar -C {mount['Destination']} .",
                            volumes={volume_name: {'bind': mount['Destination'], 'mode': 'ro'}},
                            detach=True,
                            remove=False
                        )
                        
                        sidecar.wait()
                        
                        ststream, stat = sidecar.get_archive("/backup.tar")
                        with open(tar_file_path, "wb") as f:
                            for chunk in ststream:
                                f.write(chunk)
                        
                        sidecar.remove()
                        zipf.write(tar_file_path, f"volumes/volume_{volume_name}.tar")
        
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


@app.post("/api/backup-project")
async def backup_compose_project(
    project_name: str = Form(...),
    include_volumes: bool = Form(True),
    include_images: bool = Form(False),
    pause_during_backup: bool = Form(True)
):
    """Backs up an entire Docker Compose stack dynamically grouped under a project name."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Offline")

    try:
        all_containers = client.containers.list(all=True)
        project_containers = [
            c for c in all_containers 
            if c.labels.get("com.docker.compose.project") == project_name
        ]

        if not project_containers:
            raise HTTPException(status_code=404, detail=f"No containers found for project: {project_name}")

        temp_dir = tempfile.mkdtemp()
        backup_zip_path = os.path.join(temp_dir, f"project_{project_name}_backup.zip")

        paused_containers = []
        if pause_during_backup:
            for container in project_containers:
                if container.status == "running":
                    container.pause()
                    paused_containers.append(container)

        processed_images = set()
        unsafe_tag_found = False

        with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
            stack_metadata = {
                "project_name": project_name,
                "images_included": include_images,
                "migration_safety_warning": False,
                "services": []
            }

            for container in project_containers:
                attrs = container.attrs
                service_name = container.labels.get("com.docker.compose.service", container.name)
                image_tag = attrs['Config']['Image']
                
                is_unsafe = check_for_unsafe_tags(image_tag)
                if is_unsafe:
                    unsafe_tag_found = True
                
                safe_image_filename = image_tag.replace("/", "_").replace(":", "-") + ".tar"

                service_meta = {
                    "container_name": container.name,
                    "service_name": service_name,
                    "image": image_tag,
                    "unsafe_tag": is_unsafe,
                    "image_filename": safe_image_filename if include_images else None,
                    "env": attrs['Config'].get('Env', []),
                    "ports": attrs['NetworkSettings'].get('Ports', {}),
                    "networks": list(attrs['NetworkSettings'].get('Networks', {}).keys()),
                    "mounts": []
                }

                if include_images and image_tag not in processed_images:
                    processed_images.add(image_tag)
                    image_tar_path = os.path.join(temp_dir, safe_image_filename)
                    
                    try:
                        image_obj = client.images.get(image_tag)
                        with open(image_tar_path, 'wb') as f:
                            for chunk in image_obj.save(named=True):
                                f.write(chunk)
                        zipf.write(image_tar_path, f"images/{safe_image_filename}")
                    except Exception as img_err:
                        print(f"Warning: Could not save image layers for {image_tag}: {img_err}")
                        service_meta["image_filename"] = None

                for mount in attrs.get('Mounts', []):
                    if mount['Type'] == 'volume':
                        vol_name = mount['Name']
                        dest = mount['Destination']
                        service_meta["mounts"].append({
                            "type": "volume",
                            "name": vol_name,
                            "destination": dest
                        })

                        if include_volumes:
                            tar_file_path = os.path.join(temp_dir, f"vol_{vol_name}.tar")
                            sidecar = client.containers.run(
                                "alpine:latest",
                                command=f"tar -cf /backup.tar -C {dest} .",
                                volumes={vol_name: {'bind': dest, 'mode': 'ro'}},
                                detach=True,
                                remove=False
                            )
                            sidecar.wait()
                            ststream, _ = sidecar.get_archive("/backup.tar")
                            with open(tar_file_path, "wb") as f:
                                for chunk in ststream:
                                    f.write(chunk)
                            sidecar.remove()
                            zipf.write(tar_file_path, f"volumes/vol_{vol_name}.tar")

                stack_metadata["services"].append(service_meta)

            if unsafe_tag_found:
                stack_metadata["migration_safety_warning"] = True

            metadata_file_path = os.path.join(temp_dir, "stack_metadata.json")
            with open(metadata_file_path, "w") as f:
                json.dump(stack_metadata, f, indent=4)
            zipf.write(metadata_file_path, "stack_metadata.json")

            compose_file_path = os.path.join(temp_dir, "docker-compose.yml")
            compose_yaml = generate_project_compose(stack_metadata)
            with open(compose_file_path, "w") as f:
                f.write(compose_yaml)
            zipf.write(compose_file_path, "docker-compose.yml")

        if pause_during_backup:
            for container in paused_containers:
                try:
                    container.reload()
                    if container.status == "paused":
                        container.unpause()
                except Exception:
                    pass

        return FileResponse(backup_zip_path, media_type="application/zip", filename=f"{project_name}_backup.zip")

    except Exception as e:
        if 'paused_containers' in locals():
            for container in paused_containers:
                try:
                    container.reload()
                    if container.status == "paused":
                        container.unpause()
                except Exception:
                    pass
        raise HTTPException(status_code=500, detail=f"Stack backup failed: {str(e)}")


@app.post("/api/restore")
async def execute_restore(file: UploadFile = File(...)):
    """Unified route that automatically delegates single-container or multi-service stack restores."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker Daemon Connection Offline")

    try:
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, file.filename)
        
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Extract package to temporary folder
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Inspect Zip format for dispatch targeting
        stack_meta_path = os.path.join(temp_dir, "stack_metadata.json")
        single_meta_path = os.path.join(temp_dir, "metadata.json")

        if os.path.exists(stack_meta_path):
            return await run_restore_project(temp_dir)
        elif os.path.exists(single_meta_path):
            return await run_restore_single(temp_dir)
        else:
            raise HTTPException(status_code=400, detail="Invalid package layout: Metadata tags missing.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore pipeline failed: {str(e)}")


async def run_restore_single(temp_dir: str):
    """Executes single container restore with active auto-healing."""
    metadata_path = os.path.join(temp_dir, "metadata.json")
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
            network = client.networks.create(net_name, driver="bridge")
        restored_networks.append((network, net_data))

    # 3. Create volumes, scan/auto-heal configs, & restore archived files
    mounts = metadata.get('Mounts', [])
    volume_binds = {}
    for mount in mounts:
        if mount['Type'] == 'volume':
            vol_name = mount['Name']
            dest = mount['Destination']
            
            client.volumes.create(name=vol_name)
            volume_binds[vol_name] = {'bind': dest, 'mode': 'rw'}
            
            vol_filename = f"volume_{vol_name}.tar"
            tar_archive_path = os.path.join(temp_dir, "volumes", vol_filename)
            
            if os.path.exists(tar_archive_path):
                # Run the Auto-Heal engine on the volume contents before injection
                prepare_and_heal_volume(temp_dir, vol_filename)
                
                sidecar = client.containers.run(
                    "alpine:latest",
                    command="sleep 3600",
                    volumes={vol_name: {'bind': dest, 'mode': 'rw'}},
                    detach=True
                )
                
                with open(tar_archive_path, 'rb') as tar_file:
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
    restored_container = client.containers.create(
        image,
        name=name,
        ports=formatted_ports,
        environment=metadata['Config'].get('Env', []),
        volumes=volume_binds,
        restart_policy={"Name": "always"}
    )

    # 6. Connect container to custom networks
    for network, net_data in restored_networks:
        try:
            if network.name != "bridge":
                network.connect(
                    restored_container,
                    aliases=net_data.get('Aliases', []),
                    ipv4_address=net_data.get('IPAddress', None)
                )
        except Exception as net_err:
            print(f"Network bind warning: {net_err}")

    restored_container.start()
    return {"status": "success", "message": f"Container '{name}' and all dependent volumes/networks restored successfully!"}


async def run_restore_project(temp_dir: str):
    """Restores an entire compose stack with auto-healing and side-loaded image layers."""
    metadata_path = os.path.join(temp_dir, "stack_metadata.json")
    with open(metadata_path, "r") as f:
        stack_metadata = json.load(f)

    project_name = stack_metadata["project_name"]

    # 1. Setup custom default bridge network for the stack
    network_name = f"{project_name}_default"
    try:
        network = client.networks.get(network_name)
    except docker.errors.NotFound:
        network = client.networks.create(network_name, driver="bridge")

    # 2. Side-load embedded images directly to host engine if available
    if stack_metadata.get("images_included", False):
        for service in stack_metadata["services"]:
            img_filename = service.get("image_filename")
            if img_filename:
                tar_path = os.path.join(temp_dir, "images", img_filename)
                if os.path.exists(tar_path):
                    with open(tar_path, 'rb') as f:
                        client.images.load(f.read())

    # 3. Process volumes, auto-heal configurations, and create containers
    for service in stack_metadata["services"]:
        image = service["image"]
        service_name = service["service_name"]
        
        if not stack_metadata.get("images_included", False):
            client.images.pull(image)

        volume_binds = {}
        for mount in service.get("mounts", []):
            if mount["type"] == "volume":
                vol_name = mount["name"]
                dest = mount["destination"]
                
                client.volumes.create(name=vol_name)
                volume_binds[vol_name] = {'bind': dest, 'mode': 'rw'}
                
                vol_filename = f"vol_{vol_name}.tar"
                tar_archive_path = os.path.join(temp_dir, "volumes", vol_filename)
                
                if os.path.exists(tar_archive_path):
                    # Run the Auto-Heal engine on the stack volumes before injection
                    prepare_and_heal_volume(temp_dir, vol_filename)
                    
                    sidecar = client.containers.run(
                        "alpine:latest",
                        command="sleep 3600",
                        volumes={vol_name: {'bind': dest, 'mode': 'rw'}},
                        detach=True
                    )
                    with open(tar_archive_path, 'rb') as tar_file:
                        with tarfile.open(fileobj=tar_file) as outer_tar:
                            inner_tar_extracted = outer_tar.extractfile("backup.tar")
                            if inner_tar_extracted:
                                sidecar.put_archive(dest, inner_tar_extracted.read())
                    sidecar.stop()
                    sidecar.remove()

        ports_map = service.get("ports", {})
        formatted_ports = {}
        if ports_map:
            for container_port, host_ports in ports_map.items():
                if host_ports:
                    formatted_ports[container_port] = host_ports[0]['HostPort']

        restored_container = client.containers.create(
            image,
            name=service["container_name"],
            ports=formatted_ports,
            environment=service.get("env", []),
            volumes=volume_binds,
            labels={
                "com.docker.compose.project": project_name,
                "com.docker.compose.service": service_name
            },
            restart_policy={"Name": "always"}
        )

        network.connect(restored_container, aliases=[service_name])
        restored_container.start()

    return {"status": "success", "message": f"Stack '{project_name}' completely restored using auto-healed configurations!"}


def generate_compose_blueprint(attrs: dict) -> str:
    """Helper to compile dynamic single container blueprints."""
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


def generate_project_compose(stack_metadata: dict) -> str:
    """Helper to compile full project-level compose blueprints."""
    services_block = {}
    networks_block = {"default": {"external": True, "name": f"{stack_metadata['project_name']}_default"}}
    volumes_block = {}

    for service in stack_metadata["services"]:
        s_name = service["service_name"]
        service_config = {
            "image": service["image"],
            "container_name": service["container_name"],
            "restart": "always",
            "networks": ["default"]
        }

        if service.get("env"):
            service_config["environment"] = service["env"]

        ports_map = service.get("ports", {})
        if ports_map:
            mapped_ports = []
            for container_port, host_ports in ports_map.items():
                if host_ports:
                    mapped_ports.append(f"{host_ports[0]['HostPort']}:{container_port.split('/')[0]}")
            if mapped_ports:
                service_config["ports"] = mapped_ports

        mounts = service.get("mounts", [])
        if mounts:
            vols = []
            for m in mounts:
                if m["type"] == "volume":
                    vols.append(f"{m['name']}:{m['destination']}")
                    volumes_block[m['name']] = {"external": True}
            if vols:
                service_config["volumes"] = vols

        services_block[s_name] = service_config

    compose_data = {
        "version": "3.8",
        "services": services_block,
        "networks": networks_block
    }

    if volumes_block:
        compose_data["volumes"] = volumes_block

    return yaml.dump(compose_data, default_flow_style=False)


if __name__ == "__main__":
    import uvicorn
    # Serves traffic on internal port 6767
    uvicorn.run("main:app", host="0.0.0.0", port=6767, reload=False)
