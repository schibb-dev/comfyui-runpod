#!/usr/bin/env python3
"""
Custom Workflow Saver for ComfyUI
Allows saving workflows to specific subdirectories with organization
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

def save_workflow_to_folder(workflow_data, category="misc", workflow_name=None):
    """
    Save a workflow to a specific category folder
    
    Args:
        workflow_data: The workflow JSON data
        category: The category folder (e.g., 'video-generation', 'character-generation', 'experimental')
        workflow_name: Optional custom name (defaults to timestamp)
    """
    
    # Base workflow directory
    base_dir = Path(__file__).parent
    category_dir = base_dir / "current" / category
    
    # Create category directory if it doesn't exist
    category_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate filename
    if workflow_name:
        filename = f"{workflow_name}.json"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"workflow_{timestamp}.json"
    
    # Ensure unique filename
    filepath = category_dir / filename
    counter = 1
    while filepath.exists():
        name_part = filename.rsplit('.', 1)[0]
        ext_part = filename.rsplit('.', 1)[1]
        filename = f"{name_part}_{counter}.{ext_part}"
        filepath = category_dir / filename
        counter += 1
    
    # Save the workflow
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(workflow_data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Workflow saved to: {filepath}")
    return str(filepath)

def main():
    """Command line interface for saving workflows"""
    if len(sys.argv) < 2 or sys.argv[1] in ['--help', '-h']:
        print("Usage: python save_workflow_to_folder.py <workflow_file> [category] [name]")
        print("Categories: video-generation, character-generation, experimental, flux-generation, misc")
        print("Example: python save_workflow_to_folder.py MyWorkflow.json video-generation")
        sys.exit(1)
    
    workflow_file = sys.argv[1]
    category = sys.argv[2] if len(sys.argv) > 2 else "misc"
    workflow_name = sys.argv[3] if len(sys.argv) > 3 else None
    
    # Load workflow
    with open(workflow_file, 'r', encoding='utf-8') as f:
        workflow_data = json.load(f)
    
    # Save to category
    save_workflow_to_folder(workflow_data, category, workflow_name)

if __name__ == "__main__":
    main()
