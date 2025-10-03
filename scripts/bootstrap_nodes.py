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

def install_requirements(node_dir, retry_attempts=3):
    """Install requirements.txt for a node"""
    requirements_file = os.path.join(node_dir, "requirements.txt")
    if not os.path.exists(requirements_file):
        print(f"ℹ️  No requirements.txt found for {os.path.basename(node_dir)}")
        return True
    
    for attempt in range(retry_attempts):
        try:
            print(f"📋 Installing requirements for {os.path.basename(node_dir)} (attempt {attempt + 1})")
            result = subprocess.run(
                ["pip", "install", "-r", requirements_file],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                print(f"✅ Requirements installed for {os.path.basename(node_dir)}")
                return True
            else:
                print(f"⚠️  Requirements installation failed (attempt {attempt + 1}): {result.stderr}")
                if attempt < retry_attempts - 1:
                    time.sleep(5)  # Wait before retry
                    
        except subprocess.TimeoutExpired:
            print(f"⏰ Timeout installing requirements for {os.path.basename(node_dir)}")
        except Exception as e:
            print(f"❌ Error installing requirements: {e}")
    
    return False

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
        
        target_dir = os.path.join(custom_nodes_dir, node_name)
        
        # Skip if already exists and skip_existing is True
        if skip_existing and os.path.exists(target_dir):
            print(f"⏭️  Skipping {node_name} (already exists)")
            success_count += 1
            continue
        
        # Remove existing directory if it exists
        if os.path.exists(target_dir):
            print(f"🗑️  Removing existing {node_name}")
            subprocess.run(["rm", "-rf", target_dir])
        
        # Clone the repository
        if clone_repo(repo_url, target_dir, branch, timeout):
            success_count += 1
            
            # Install requirements if enabled
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts)
        else:
            if required:
                print(f"❌ Failed to install required node: {node_name}")
                return False
    
    # Process optional nodes
    optional_nodes = nodes.get('optional', [])
    for node in optional_nodes:
        total_count += 1
        node_name = node['name']
        repo_url = node['repo']
        branch = node.get('branch', 'main')
        
        target_dir = os.path.join(custom_nodes_dir, node_name)
        
        # Skip if already exists and skip_existing is True
        if skip_existing and os.path.exists(target_dir):
            print(f"⏭️  Skipping {node_name} (already exists)")
            success_count += 1
            continue
        
        # Remove existing directory if it exists
        if os.path.exists(target_dir):
            print(f"🗑️  Removing existing {node_name}")
            subprocess.run(["rm", "-rf", target_dir])
        
        # Clone the repository
        if clone_repo(repo_url, target_dir, branch, timeout):
            success_count += 1
            
            # Install requirements if enabled
            if auto_install_requirements:
                install_requirements(target_dir, retry_attempts)
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
