#!/usr/bin/env python3
"""
Custom Nodes Bootstrap Script
Reads custom_nodes.yaml and installs required custom nodes
"""

import yaml
import os
import subprocess
import sys
import time
import shutil
import hashlib
from pathlib import Path

def load_config(config_path="/workspace/custom_nodes.yaml"):
    """Load the custom nodes configuration"""
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"❌ Config file not found: {config_path}")
        return None
    except yaml.YAMLError as e:
        print(f"❌ Error parsing YAML: {e}")
        return None

def clone_repo(repo_url, target_dir, branch="main", timeout=300):
    """Clone a git repository with timeout"""
    try:
        print(f"📦 Cloning {repo_url} to {target_dir}")
        cmd = ["git", "clone", "-b", branch, repo_url, target_dir]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        
        if result.returncode == 0:
            print(f"✅ Successfully cloned {repo_url}")
            return True
        else:
            print(f"❌ Failed to clone {repo_url}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"⏰ Timeout cloning {repo_url}")
        return False
    except Exception as e:
        print(f"❌ Error cloning {repo_url}: {e}")
        return False

def checkout_repo_ref(target_dir: str, ref: str, timeout: int = 300) -> bool:
    """Checkout a specific git ref (commit/tag/branch) in an existing clone."""
    try:
        if not ref:
            return True
        print(f"📌 Pinning {os.path.basename(target_dir)} to {ref}")
        subprocess.run(
            ["git", "-C", target_dir, "fetch", "--all", "--tags"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result = subprocess.run(
            ["git", "-C", target_dir, "checkout", ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return True
        print(f"⚠️  Failed to checkout ref {ref} for {os.path.basename(target_dir)}: {result.stderr}")
        return False
    except Exception as e:
        print(f"⚠️  Error pinning {os.path.basename(target_dir)} to {ref}: {e}")
        return False

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install_requirements(node_dir: str, retry_attempts: int = 3, force: bool = False) -> bool:
    """
    Install requirements.txt for a node.

    Important: this repo commonly mounts host `custom_nodes/` into `/ComfyUI/custom_nodes`.
    In that scenario, the node folder may exist but its Python deps may not be installed in
    the container venv yet. To avoid reinstalling on every boot, we write a hash marker.
    """
    requirements_file = os.path.join(node_dir, "requirements.txt")
    if not os.path.exists(requirements_file):
        print(f"ℹ️  No requirements.txt found for {os.path.basename(node_dir)}")
        return True

    marker_path = os.path.join(node_dir, ".requirements.sha256")
    current_hash = _sha256_file(requirements_file)
    previous_hash = None
    try:
        if os.path.exists(marker_path):
            with open(marker_path, "r", encoding="utf-8") as f:
                previous_hash = f.read().strip() or None
    except Exception:
        previous_hash = None

    if not force and previous_hash == current_hash:
        print(f"✅ Requirements up-to-date for {os.path.basename(node_dir)}")
        return True

    for attempt in range(retry_attempts):
        try:
            print(
                f"📋 Installing requirements for {os.path.basename(node_dir)} "
                f"(attempt {attempt + 1})"
            )
            # Use the running interpreter's environment (container venv) instead of a random `pip` on PATH.
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                try:
                    with open(marker_path, "w", encoding="utf-8") as f:
                        f.write(current_hash + "\n")
                except Exception:
                    pass
                print(f"✅ Requirements installed for {os.path.basename(node_dir)}")
                return True

            print(
                f"⚠️  Requirements installation failed (attempt {attempt + 1}): {result.stderr}"
            )
            if attempt < retry_attempts - 1:
                time.sleep(5)  # Wait before retry

        except subprocess.TimeoutExpired:
            print(f"⏰ Timeout installing requirements for {os.path.basename(node_dir)}")
        except Exception as e:
            print(f"❌ Error installing requirements: {e}")

    return False

def has_executable(cmd: str) -> bool:
    """Return True if `cmd` is available on PATH."""
    try:
        result = subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False

def is_frontend_built(node_dir: str) -> bool:
    """Heuristic: treat dist/index.html as a successful frontend build."""
    dist_index = os.path.join(node_dir, "dist", "index.html")
    return os.path.exists(dist_index)

def npm_install_and_build(node_dir: str, retry_attempts: int = 2) -> bool:
    """
    If a node includes a JS frontend, install deps and build it.
    Intended for nodes that explicitly opt-in via config (`npm_build: true`).
    """
    pkg_json = os.path.join(node_dir, "package.json")
    if not os.path.exists(pkg_json):
        print(f"ℹ️  No package.json found for {os.path.basename(node_dir)}; skipping npm build")
        return True

    if not has_executable("npm"):
        print("⚠️  npm is not available in this container; cannot build frontend")
        return False

    lockfile = os.path.join(node_dir, "package-lock.json")
    install_cmd = ["npm", "ci", "--no-audit", "--no-fund"] if os.path.exists(lockfile) else ["npm", "install", "--no-audit", "--no-fund"]

    env = os.environ.copy()
    env.setdefault("npm_config_update_notifier", "false")

    for attempt in range(retry_attempts):
        try:
            print(f"📦 npm install for {os.path.basename(node_dir)} (attempt {attempt + 1})")
            r1 = subprocess.run(
                install_cmd,
                cwd=node_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if r1.returncode != 0:
                print(f"⚠️  npm install failed: {r1.stderr}")
                if attempt < retry_attempts - 1:
                    time.sleep(5)
                continue

            print(f"🏗️  npm run build for {os.path.basename(node_dir)}")
            r2 = subprocess.run(
                ["npm", "run", "build"],
                cwd=node_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if r2.returncode == 0:
                print(f"✅ Frontend build completed for {os.path.basename(node_dir)}")
                return True
            print(f"⚠️  npm run build failed: {r2.stderr}")
        except subprocess.TimeoutExpired:
            print(f"⏰ Timeout building frontend for {os.path.basename(node_dir)}")
        except Exception as e:
            print(f"❌ Error building frontend: {e}")

        if attempt < retry_attempts - 1:
            time.sleep(5)

    return False

def is_valid_node_install(node_dir: str) -> bool:
    """
    Determine whether an existing node directory looks like a real install.

    This repository commonly volume-mounts a host `custom_nodes/` directory into
    `/ComfyUI/custom_nodes`. If that host directory contains empty placeholder
    folders (or partially-copied folders), ComfyUI will log import tracebacks
    like "missing __init__.py". Treat such directories as invalid so we re-clone.
    """
    if not os.path.isdir(node_dir):
        return False

    try:
        # Empty directory = not a valid install
        if not any(os.scandir(node_dir)):
            return False
    except OSError:
        return False

    # For directories, ComfyUI expects an __init__.py entrypoint.
    # (If a node is a single .py file, it should live directly under custom_nodes/
    # as a file, not a folder with no __init__.py.)
    init_py = os.path.join(node_dir, "__init__.py")
    return os.path.exists(init_py)

def remove_path(path: str) -> None:
    """Best-effort removal for files or directories."""
    try:
        if os.path.islink(path) or os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        # Avoid hard-failing bootstrap because cleanup hit a permissions edge.
        pass

def bootstrap_nodes(config):
    """Bootstrap custom nodes based on configuration"""
    if not config:
        return False
    
    custom_nodes_dir = "/ComfyUI/custom_nodes"
    os.makedirs(custom_nodes_dir, exist_ok=True)
    
    nodes = config.get('nodes', {})
    install_settings = config.get('install', {})
    
    auto_install_requirements = install_settings.get('auto_install_requirements', True)
    skip_existing = install_settings.get('skip_existing', True)
    timeout = install_settings.get('timeout', 300)
    retry_attempts = install_settings.get('retry_attempts', 3)
    force_reinstall = os.environ.get("FORCE_REINSTALL_NODE_REQUIREMENTS", "").strip().lower() in ("1", "true", "yes")
    if force_reinstall:
        print("🔄 FORCE_REINSTALL_NODE_REQUIREMENTS is set; reinstalling all node requirements")

    success_count = 0
    total_count = 0
    
    # Process essential nodes first
    essential_nodes = nodes.get('essential', [])
    for node in essential_nodes:
        total_count += 1
        node_name = node['name']
        repo_url = node['repo']
        branch = node.get('branch', 'main')
        required = node.get('required', True)
        ref = node.get('ref')
        npm_build = node.get('npm_build', False)
        
        target_dir = os.path.join(custom_nodes_dir, node_name)
        
        # Skip if already exists and skip_existing is True (unless we still need an npm build).
        # Even when skipping the clone step, we still ensure Python deps are installed.
        if skip_existing and is_valid_node_install(target_dir):
            if ref and not checkout_repo_ref(target_dir, ref, timeout):
                if required:
                    print(f"❌ Failed to pin required node {node_name} to {ref}")
                    return False
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts, force=force_reinstall)
            if npm_build and not is_frontend_built(target_dir):
                print(f"🏗️  {node_name} exists but frontend not built; running npm build")
                npm_install_and_build(target_dir)
            else:
                print(f"⏭️  Skipping {node_name} (already exists)")
            success_count += 1
            continue
        
        # Remove existing directory if it exists
        if os.path.exists(target_dir):
            print(f"🗑️  Removing existing/invalid {node_name}")
            remove_path(target_dir)
        
        # Clone the repository
        if clone_repo(repo_url, target_dir, branch, timeout):
            if ref and not checkout_repo_ref(target_dir, ref, timeout):
                if required:
                    print(f"❌ Failed to pin required node {node_name} to {ref}")
                    return False
            success_count += 1

            # Build node frontend if requested
            if npm_build:
                npm_install_and_build(target_dir)
            
            # Install requirements if enabled
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts, force=force_reinstall)
        else:
            if required:
                print(f"❌ Failed to install required node: {node_name}")
                return False
    
    # Process optional nodes (skip Acly/Krita bridge nodes unless INSTALL_KRITA_BACKEND_NODES=true)
    install_krita_backend = os.environ.get("INSTALL_KRITA_BACKEND_NODES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    optional_nodes = nodes.get("optional", [])
    for node in optional_nodes:
        if node.get("krita_backend") and not install_krita_backend:
            print(
                f"⏭️  Skipping optional node {node.get('name', '?')} "
                f"(Krita AI Diffusion backend; set INSTALL_KRITA_BACKEND_NODES=true to install)"
            )
            continue
        total_count += 1
        node_name = node['name']
        repo_url = node['repo']
        branch = node.get('branch', 'main')
        ref = node.get('ref')
        npm_build = node.get('npm_build', False)
        enabled_env = node.get("enabled_env")
        if enabled_env:
            enabled = os.environ.get(enabled_env, "").strip().lower() in ("1", "true", "yes")
            if not enabled:
                print(
                    f"⏭️  Skipping optional node {node_name} "
                    f"(set {enabled_env}=true to install)"
                )
                continue
        
        target_dir = os.path.join(custom_nodes_dir, node_name)
        
        # Skip if already exists and skip_existing is True (unless we still need an npm build).
        # Even when skipping the clone step, we still ensure Python deps are installed.
        if skip_existing and is_valid_node_install(target_dir):
            if ref and not checkout_repo_ref(target_dir, ref, timeout):
                print(f"⚠️  Failed to pin optional node {node_name} to {ref}")
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts, force=force_reinstall)
            if npm_build and not is_frontend_built(target_dir):
                print(f"🏗️  {node_name} exists but frontend not built; running npm build")
                npm_install_and_build(target_dir)
            else:
                print(f"⏭️  Skipping {node_name} (already exists)")
            success_count += 1
            continue
        
        # Remove existing directory if it exists
        if os.path.exists(target_dir):
            print(f"🗑️  Removing existing/invalid {node_name}")
            remove_path(target_dir)
        
        # Clone the repository
        if clone_repo(repo_url, target_dir, branch, timeout):
            if ref and not checkout_repo_ref(target_dir, ref, timeout):
                print(f"⚠️  Failed to pin optional node {node_name} to {ref}")
            success_count += 1

            # Build node frontend if requested
            if npm_build:
                npm_install_and_build(target_dir)
            
            # Install requirements if enabled
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts, force=force_reinstall)
        else:
            print(f"⚠️  Failed to install optional node: {node_name}")
    
    print(f"\n📊 Bootstrap Summary: {success_count}/{total_count} nodes installed successfully")
    return success_count > 0

def main():
    """Main bootstrap function"""
    print("🚀 Starting Custom Nodes Bootstrap")
    
    # Load configuration
    config = load_config()
    if not config:
        print("❌ Failed to load configuration")
        sys.exit(1)
    
    # Bootstrap nodes
    if bootstrap_nodes(config):
        print("✅ Bootstrap completed successfully")
        sys.exit(0)
    else:
        print("❌ Bootstrap failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
