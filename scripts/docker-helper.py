#!/usr/bin/env python3

import subprocess
import sys
import os
import shutil


def get_running_containers():
    """
    Returns a list of tuples (container_id, image_name) for all running containers.
    """
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.ID}} {{.Image}}"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    lines = result.stdout.strip().split("\n")

    containers = []
    for line in lines:
        # Each line is something like "2fb2baa5c93f ubuntu:latest"
        if line.strip():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                container_id, image_name = parts
                containers.append((container_id, image_name))
            else:
                # If something unexpected is in the output
                containers.append((parts[0], "unknown_image"))
    return containers


def select_container(containers):
    """
    If there's only one container, returns that container ID directly.
    If multiple containers are found, presents a prompt to select one.
    """
    if not containers:
        print("No running containers found.")
        sys.exit(1)

    if len(containers) == 1:
        return containers[0][0]  # Only one container ID

    print("Multiple containers are running:")
    for idx, (cid, img) in enumerate(containers, start=1):
        print(f"{idx}. {cid} ({img})")
    choice = input("Select a container by number: ")

    try:
        choice_index = int(choice) - 1
        if choice_index < 0 or choice_index >= len(containers):
            raise ValueError
    except ValueError:
        print("Invalid selection.")
        sys.exit(1)

    return containers[choice_index][0]


def run_bash(container_id):
    """
    Runs an interactive bash shell (/bin/bash) in the given container.
    """
    cmd = (
        "if [ -d /crs-workdir/worker-0/HarnessRunner ]; then "
        "  cd /crs-workdir/worker-0/HarnessRunner; "
        "fi; "
        "exec bash"
    )

    print(f"Attaching to container {container_id} with /bin/bash...")
    subprocess.run(["docker", "exec", "-it", container_id, "/bin/bash", "-c", cmd])


def prompt_and_remove(path, target_name=None):
    """
    Prompts the user for confirmation before removing an existing file or directory.
    If path is a directory, target_name specifies the file or directory inside path to remove.
    """
    if os.path.exists(path):
        if os.path.isdir(path) and target_name:
            target_path = os.path.join(path, target_name)
        else:
            target_path = path

        if os.path.exists(target_path):
            response = (
                input(
                    f"{target_path} already exists. Do you want to remove it? (y/n): "
                )
                .strip()
                .lower()
            )
            if response == "y":
                if os.path.isdir(target_path):
                    shutil.rmtree(target_path)
                else:
                    os.remove(target_path)
            else:
                print("Operation cancelled by the user.")
                sys.exit(1)
        else:
            print(f"{target_path} does not exist, no need to remove.")
    else:
        print(f"{path} does not exist, no need to remove.")


def copy_file_out(container_id, source_path, dest_path):
    """
    Copies a file out from the container to the local filesystem.
    Equivalent to: docker cp <container_id>:<source_path> <dest_path>
    """
    target_name = os.path.basename(source_path)
    prompt_and_remove(dest_path, target_name)

    if not source_path.startswith("/"):
        source_path = f"/crs-workdir/worker-0/HarnessRunner/{source_path}"

    print(f"Copying from {container_id}:{source_path} to {dest_path}...")
    subprocess.run(["docker", "cp", f"{container_id}:{source_path}", dest_path])


def copy_workdir(container_id, dest_path):
    """
    Copies the entire /crs-workdir directory from the container to the local filesystem.
    """
    target_name = "crs-workdir"
    prompt_and_remove(dest_path, target_name)

    print(f"Copying /crs-workdir from container {container_id} to {dest_path}...")
    subprocess.run(["docker", "cp", f"{container_id}:/crs-workdir", dest_path])


def main():
    if len(sys.argv) < 2:
        mode = "run-bash"
    else:
        mode = sys.argv[1]

    # Get running containers
    containers = get_running_containers()
    container_id = select_container(containers)

    if mode == "run-bash":
        run_bash(container_id)

    elif mode == "cp":
        # For cp we need two additional arguments: source_path and dest_path
        if len(sys.argv) < 4:
            print("Usage for copy out:")
            print(
                "  python docker_helper.py cp <container_source_path> <local_destination>"
            )
            sys.exit(1)
        source_path = sys.argv[2]
        dest_path = sys.argv[3]
        copy_file_out(container_id, source_path, dest_path)

    elif mode == "cp-workdir":
        if len(sys.argv) < 2:
            print("Usage for copying /crs-workdir:")
            print("  python docker_helper.py cp-workdir <local_destination>")
            sys.exit(1)
        if len(sys.argv) < 3:
            dest_path = "."
        else:
            dest_path = sys.argv[2]
        copy_workdir(container_id, dest_path)

    else:
        print(f"Unknown mode: {mode}")
        print("Supported modes: run-bash, cp, cp-workdir")
        sys.exit(1)


if __name__ == "__main__":
    main()
