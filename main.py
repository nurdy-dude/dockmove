# main.py - Compose-Project Level Stack Backup & Restore Orchestrator with Tag Warnings
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

app = FastAPI(title="DockMove Stack Service", version="2.1.0")

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


@app.get("/", response_class=HTMLResponse)
def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    raise HTTPException(status_code=404, detail="index.html not found")


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


@app.post("/api/backup-project")
async def backup_compose_project(
    project_name: str = Form(...),
    include_volumes: bool = Form(True),
    include_images: bool = Form(False),  # Optional: defaults to False to save space
    pause_during_backup: bool = Form(True)
):
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

        # Safely pause running resources
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
                
                # Check for tag safety
                is_unsafe = check_for_unsafe_tags(image_tag)
                if is_unsafe:
                    unsafe_tag_found = True
                
                # Sanitize name for filename use
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

                # 1. Export the Exact Docker Image Layers (only if user opted-in)
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
                        # Fallback if image has no local ID/cannot be saved
                        print(f"Warning: Could not save image layers for {image_tag}: {img_err}")
                        service_meta["image_filename"] = None

                # 2. Back up Volumes
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

            # Global migration alert check
            if unsafe_tag_found:
                stack_metadata["migration_safety_warning"] = True

            # Metadata and compose structure outputs
            metadata_file_path = os.path.join(temp_dir, "stack_metadata.json")
            with open(metadata_file_path, "w") as f:
                json.dump(stack_metadata, f, indent=4)
            zipf.write(metadata_file_path, "stack_metadata.json")

            compose_file_path = os.path.join(temp_dir, "docker-compose.yml")
            compose_yaml = generate_project_compose(stack_metadata)
            with open(compose_file_path, "w") as f:
                f.write(compose_yaml)
            zipf.write(compose_file_path, "docker-compose.yml")

        # Resume original stack execution
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
