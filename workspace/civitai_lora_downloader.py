#!/usr/bin/env python3
"""
Advanced Civitai LoRA Downloader for WAN 2.1 FaceBlast Workflow
Uses the existing Civitai API infrastructure to download LoRAs automatically
"""

import os
import sys
import json
import requests
import subprocess
import argparse
from pathlib import Path
from tqdm import tqdm

def load_defaults():
    """Load default configuration from ~/.civitai_lora_defaults.json"""
    config_file = Path.home() / ".civitai_lora_defaults.json"
    
    # Default configuration
    defaults = {
        'wan_version': '2.1',
        'modality': 'i2v', 
        'resolution': '480',
        'noise_level': 'any'
    }
    
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                user_defaults = json.load(f)
                defaults.update(user_defaults)
                print(f"Using Loaded custom defaults from {config_file}")
        except Exception as e:
            print(f"Warning  Error loading config file {config_file}: {e}")
            print("Using built-in defaults")
    else:
        print("Using built-in defaults")
    
    return defaults

def save_defaults(defaults):
    """Save default configuration to ~/.civitai_lora_defaults.json"""
    config_file = Path.home() / ".civitai_lora_defaults.json"
    
    try:
        with open(config_file, 'w') as f:
            json.dump(defaults, f, indent=2)
        print(f"💾 Saved defaults to {config_file}")
        return True
    except Exception as e:
        print(f"Error Error saving config file {config_file}: {e}")
        return False

def show_current_defaults():
    """Display current default configuration"""
    defaults = load_defaults()
    print("\nUsing Current Default Configuration:")
    print(f"  WAN Version: {defaults['wan_version']}")
    print(f"  Modality: {defaults['modality']}")
    print(f"  Resolution: {defaults['resolution']}")
    print(f"  Noise Level: {defaults['noise_level']}")
    print(f"  Config file: {Path.home() / '.civitai_lora_defaults.json'}")

class CivitaiLoRADownloader:
    def __init__(self, comfyui_dir=None, base_dir=None, wan_version='2.1', modality='i2v', resolution='480', noise_level='any'):
        """
        Initialize the Civitai LoRA Downloader
        
        Args:
            comfyui_dir: Path to ComfyUI root directory (e.g., "/path/to/ComfyUI")
            base_dir: Base directory for token file (defaults to ComfyUI parent directory)
            wan_version: Preferred WAN version ('2.1', '2.2', or 'any')
            modality: Preferred modality ('i2v', 't2v', or 'any')
            resolution: Preferred resolution ('480', '720', or 'any')
            noise_level: Preferred noise level ('low', 'high', or 'any')
        """
        if comfyui_dir:
            self.comfyui_dir = Path(comfyui_dir)
            self.base_dir = self.comfyui_dir.parent if base_dir is None else Path(base_dir)
        else:
            # Fallback to default for backward compatibility
            self.base_dir = Path(base_dir) if base_dir else Path("/home/yuji/Code/Umeiart")
            self.comfyui_dir = self.base_dir / "ComfyUI"
        
        self.token_file = self.base_dir / ".civitai_token"
        self.lora_dir = self.comfyui_dir / "models" / "loras"
        self.index_file = self.lora_dir / "loras_index.json"
        self.lora_dir.mkdir(parents=True, exist_ok=True)
        
        # Civitai API configuration
        self.api_base = "https://civitai.com/api/v1"
        self.token = None
        
        # Filtering preferences
        self.wan_version = wan_version
        self.modality = modality
        self.resolution = resolution
        self.noise_level = noise_level
        
        # LoRA definitions with Civitai search terms
        self.loras = {
            'wan-nsfw-e14-fixed.safetensors': {
                'description': 'WAN NSFW Enhancement LoRA',
                'strength': 1.0,
                'enabled': True,
                'search_terms': ['wan nsfw e14', 'wan enhancement', 'wan nsfw'],
                # Primary Civitai model ID (WAN 25 Realistic). Likely matches HIGH/LOW files:
                #   - version_id: 2265257, file_id: 2157376, name: W25_Realistic_HIGH.safetensors (~1.14 GB)
                #   - version_id: 2265286, file_id: 2157414, name: W25_Realistic_LOW.safetensors (~1.14 GB)
                'civitai_id': '2001317',
                'priority': 3
            },
            'wan_cumshot_i2v.safetensors': {
                'description': 'Wan Cumshot (2.2 / 2.1)',
                'strength': 0.95,
                'enabled': True,
                'search_terms': ['wan cumshot (2.2 / 2.1)', 'wan cumshot'],
                'civitai_id': '1350447',
                'version_id': 1602715,
                'file_id': 1502760,
                'priority': 1
            },
            'wan-thiccum-v3.safetensors': {
                'description': 'WAN Thiccum v3 LoRA (WAN 2.1 I2V)',
                'strength': 0.95,
                'enabled': True,
                'search_terms': ['wan thiccum v3', 'wan thiccum', 'thiccum v3'],
                'civitai_id': '1643871',
                'version_id': 1860691,
                'file_id': 1760392,
                'priority': 1
            },
            'WAN_dr34mj0b.safetensors': {
                'description': 'WAN Dr34mj0b LoRA (WAN 2.1 I2V)',
                'strength': 1.0,
                'enabled': True,
                'search_terms': ['wan dr34mj0b', 'dr34mj0b', 'wan dr34'],
                'civitai_id': '1395313',
                # Pin to the WAN 2.1 I2V 480p variant
                'version_id': 1639409,
                'file_id': 1539760,
                # Alternatives for version/file IDs under this model:
                #   - version_id: 1639409, file_id: 1539760, name: wan_dr34mj0b_t2v.safetensors (~146 MB) ← WAN 2.1 I2V 480p
                #   - version_id: 1610465, file_id: 1510563, name: WAN_dr34mj0b.safetensors (~171 MB) ← WAN 2.1 I2V 720p
                #   - version_id: 2235299, file_id: 2128196, name: DR34MJOB_I2V_14b_HighNoise.safetensors (~293 MB) ← WAN 2.2 I2V
                #   - version_id: 2235288, file_id: 2128187, name: DR34MJOB_I2V_14b_LowNoise.safetensors (~293 MB) ← WAN 2.2 I2V
                #   - version_id: 1672099, file_id: 1573106, name: wan_dr34mj0b_t2v_HD.safetensors (~146 MB) ← WAN 2.1 T2V
                'priority': 1
            },
            'bounceV_01.safetensors': {
                'description': 'Bounce V01 LoRA',
                'strength': 1.0,
                'enabled': True,
                'search_terms': ['bounceV 01', 'bounceV', 'bounce'],
                'civitai_id': '1343431',
                # Pin to the I2V 720p variant (matches ~293MB file size)
                'version_id': '1517164',
                'file_id': '1417396',
                # Alternatives for version/file IDs under this model (same filename across versions):
                #   - version_id: 1517164, file_id: 1417396, name: bounceV_01.safetensors
                #   - version_id: 1836694, file_id: 1736845, name: bounceV_01.safetensors
                #   - version_id: 1836649, file_id: 1736799, name: bounceV_01.safetensors
                'priority': 1
            },
            # Added per request: Facial Cumshot - Hun | Wan Video Lora
            # Source: https://civitai.com/models/1598362?modelVersionId=1952633
            'wan-cumshot-I2V-22epo-k3nk.safetensors': {
                'description': 'Facial Cumshot - Hun | Wan Video LoRA (I2V v1.0)',
                'strength': 1.0,
                'enabled': True,
                'search_terms': ['wan cumshot i2v', 'cumshot hun wan', 'wan cumshot'],
                'civitai_id': '1598362',
                'version_id': 1952633,
                'file_id': 1850112,
                # Alternatives:
                #   - version_id: 1952633, file_id: 1850112, name: wan-cumshot-I2V-22epo-k3nk.safetensors (~343 MB) Success I2V
                #   - version_id: 1999588, file_id: 1896725, name: wan-cumshot-T2V-22epo-k3nk.safetensors (~343 MB) Error T2V
                #   - version_id: 1808720, file_id: 1709261, name: cumshot-v1-18epo-hunyuan-k3nk.safetensors (~308 MB)
                'priority': 2
            }
        }
    
    def load_token(self):
        """Load Civitai API token from file"""
        if self.token_file.exists():
            try:
                with open(self.token_file, 'r') as f:
                    token_data = json.load(f)
                self.token = token_data.get("civitai_token")
                if self.token:
                    print("Success Loaded Civitai API token")
                    return True
            except (json.JSONDecodeError, KeyError):
                pass
        
        print("Error No valid Civitai API token found")
        print("Please run: ./scripts/civitai_downloader.sh lora 'wan'")
        print("This will prompt you to enter your API token")
        return False
    
    def search_civitai(self, search_term):
        """Search Civitai for LoRAs"""
        url = f"{self.api_base}/models"
        params = {
            'query': search_term,
            'types': 'LORA',
            'sort': 'Most Downloaded',
            'limit': 10
        }
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error Search failed for '{search_term}': {e}")
            return None
    
    def find_lora_by_name(self, lora_name, search_results):
        """Find a specific LoRA by name in search results"""
        if not search_results or 'items' not in search_results:
            return None
        
        # Look for exact filename match first
        for item in search_results['items']:
            model_versions = item.get('modelVersions', [])
            for version in model_versions:
                files = version.get('files', [])
                for file_info in files:
                    if file_info.get('name') == lora_name:
                        return {
                            'model_id': item.get('id'),
                            'model_name': item.get('name'),
                            'version_id': version.get('id'),
                            'file_info': file_info,
                            'download_url': file_info.get('downloadUrl')
                        }
        
        # Look for partial name match
        lora_base = lora_name.replace('.safetensors', '').lower()
        for item in search_results['items']:
            model_name = item.get('name', '').lower()
            if lora_base in model_name or any(term.lower() in model_name for term in lora_base.split('-')):
                model_versions = item.get('modelVersions', [])
                if model_versions:
                    version = model_versions[0]  # Use first version
                    files = version.get('files', [])
                    if files:
                        return {
                            'model_id': item.get('id'),
                            'model_name': item.get('name'),
                            'version_id': version.get('id'),
                            'file_info': files[0],
                            'download_url': files[0].get('downloadUrl')
                        }
        
        return None
    
    def get_model_details(self, model_id):
        """Fetch full model details from Civitai (includes versions/files)."""
        url = f"{self.api_base}/models/{model_id}"
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Warning  Failed to fetch model details for {model_id}: {e}")
            return None

    def _build_metadata(self, filename, model_data, version_id, file_info):
        """Construct a compact metadata dict for sidecar and index."""
        version_obj = None
        if model_data:
            for ver in model_data.get('modelVersions', []):
                if ver.get('id') == version_id:
                    version_obj = ver
                    break
        meta = {
            'filename': filename,
            'model_id': model_data.get('id') if model_data else None,
            'model_name': model_data.get('name') if model_data else None,
            'model_type': model_data.get('type') if model_data else None,
            'version_id': version_id,
            'version_name': (version_obj or {}).get('name'),
            'base_model': (version_obj or {}).get('baseModel'),
            'trained_words': (version_obj or {}).get('trainedWords'),
            'file': {
                'name': file_info.get('name') if isinstance(file_info, dict) else None,
                'type': file_info.get('type') if isinstance(file_info, dict) else None,
                'sizeKB': file_info.get('sizeKB') if isinstance(file_info, dict) else None,
                'downloadUrl': file_info.get('downloadUrl') if isinstance(file_info, dict) else None,
                'hashes': file_info.get('hashes') if isinstance(file_info, dict) else None,
            }
        }
        return meta

    def _write_sidecar(self, filename, metadata):
        """Write a sidecar JSON next to the LoRA file."""
        try:
            sidecar_path = self.lora_dir / f"{filename}.json"
            with open(sidecar_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            print(f"Warning  Failed to write sidecar for {filename}: {e}")

    def _extract_wan_info(self, metadata):
        """Extract WAN version and modality from metadata for renaming."""
        base_model = metadata.get('base_model') or ''
        version_name = metadata.get('version_name') or ''
        model_name = metadata.get('model_name') or ''
        
        # Extract WAN version (e.g., "2.1", "2.2")
        wan_version = None
        if 'wan video 14b i2v' in base_model.lower():
            wan_version = '21'  # WAN 2.1 I2V
        elif 'wan video 2.1' in base_model.lower():
            wan_version = '21'
        elif 'wan video 2.2' in base_model.lower():
            wan_version = '22'
        elif 'wan 2.1' in base_model.lower():
            wan_version = '21'
        elif 'wan 2.2' in base_model.lower():
            wan_version = '22'
        elif 'wan 14b' in base_model.lower() and 'i2v' in base_model.lower():
            wan_version = '21'  # WAN 2.1 I2V 14B
        elif 'wan 14b' in base_model.lower():
            wan_version = '22'  # WAN 2.2 T2V 14B
        elif 'wan' in model_name.lower() and '14b' in model_name.lower():
            wan_version = '21'  # Default 14B models to WAN 2.1
        elif 'wan 25' in model_name.lower():
            wan_version = '22'  # "WAN 25 Realistic" is actually WAN 2.2
        elif 'wan' in model_name.lower():
            # If it has "wan" in the name but no specific version, assume 21
            wan_version = '21'
        
        # Extract modality (I2V/T2V)
        modality = None
        if 'i2v' in version_name.lower() or 'i2v' in base_model.lower():
            modality = 'i2v'
        elif 't2v' in version_name.lower() or 't2v' in base_model.lower():
            modality = 't2v'
        elif 'i2v' in model_name.lower():
            modality = 'i2v'
        elif 't2v' in model_name.lower():
            modality = 't2v'
        elif 'wan video 14b i2v' in base_model.lower():
            modality = 'i2v'  # Explicit I2V detection
        elif 'wan video' in base_model.lower() and 't2v' in base_model.lower():
            modality = 't2v'  # Explicit T2V detection
        elif 'wan' in model_name.lower():
            # If it has "wan" in the name but no specific modality, assume i2v for 14B models
            if '14b' in base_model.lower() or '14b' in model_name.lower():
                modality = 'i2v'
            else:
                modality = 't2v'
        
        return wan_version, modality

    def _parse_version_metadata(self, version):
        """Parse version metadata to extract compatibility information."""
        base_model = version.get('base_model', '').lower()
        version_name = version.get('name', '').lower()
        
        # Extract WAN version
        wan_version = None
        if 'wan video 2.1' in base_model or 'wan 2.1' in base_model:
            wan_version = '2.1'
        elif 'wan video 2.2' in base_model or 'wan 2.2' in base_model:
            wan_version = '2.2'
        elif 'wan video 14b' in base_model:
            # 14B models are typically WAN 2.1
            wan_version = '2.1'
        
        # Extract modality
        modality = None
        if 'i2v' in base_model or 'i2v' in version_name:
            modality = 'i2v'
        elif 't2v' in base_model or 't2v' in version_name:
            modality = 't2v'
        
        # Extract resolution
        resolution = None
        if '720' in base_model or '720' in version_name:
            resolution = '720'
        elif '480' in base_model or '480' in version_name:
            resolution = '480'
        
        # Extract noise level
        noise_level = None
        if 'high' in version_name or 'highnoise' in version_name:
            noise_level = 'high'
        elif 'low' in version_name or 'lownoise' in version_name:
            noise_level = 'low'
        
        return {
            'wan_version': wan_version,
            'modality': modality,
            'resolution': resolution,
            'noise_level': noise_level,
            'base_model': base_model,
            'version_name': version_name
        }

    def _score_version_compatibility(self, version_metadata):
        """Score a version based on compatibility with preferences."""
        score = 0
        
        # WAN version scoring
        if self.wan_version == 'any':
            score += 10  # Any version is acceptable
        elif version_metadata['wan_version'] == self.wan_version:
            score += 20  # Perfect match
        elif version_metadata['wan_version'] is not None:
            score += 5   # Different WAN version but still WAN
        
        # Modality scoring
        if self.modality == 'any':
            score += 10
        elif version_metadata['modality'] == self.modality:
            score += 20
        elif version_metadata['modality'] is not None:
            score += 5
        
        # Resolution scoring
        if self.resolution == 'any':
            score += 10
        elif version_metadata['resolution'] == self.resolution:
            score += 20
        elif version_metadata['resolution'] is not None:
            score += 5
        
        # Noise level scoring (only applies to WAN 2.2)
        if version_metadata['wan_version'] == '2.2':
            if self.noise_level == 'any':
                score += 10
            elif version_metadata['noise_level'] == self.noise_level:
                score += 20
            elif version_metadata['noise_level'] is not None:
                score += 5
        else:
            # For non-WAN 2.2 models, noise level preference doesn't apply
            score += 10
        
        return score

    def _select_best_version(self, model_data, lora_name):
        """Select the best version based on preferences with fallback logic."""
        versions = model_data.get('modelVersions', [])
        if not versions:
            return None
        
        scored_versions = []
        
        for version in versions:
            # Only consider versions with .safetensors files
            safetensors_files = [f for f in version.get('files', []) if f.get('name', '').endswith('.safetensors')]
            if not safetensors_files:
                continue
            
            version_metadata = self._parse_version_metadata(version)
            score = self._score_version_compatibility(version_metadata)
            
            # Use the first (usually largest) safetensors file
            file_info = safetensors_files[0]
            
            scored_versions.append({
                'version': version,
                'file': file_info,
                'metadata': version_metadata,
                'score': score
            })
        
        if not scored_versions:
            return None
        
        # Sort by score (highest first)
        scored_versions.sort(key=lambda x: x['score'], reverse=True)
        
        best = scored_versions[0]
        
        # Log the selection reasoning
        metadata = best['metadata']
        print(f"  Trigger Selected version: {best['version'].get('name', 'Unknown')}")
        print(f"      WAN: {metadata['wan_version'] or 'Unknown'} (preferred: {self.wan_version})")
        print(f"      Modality: {metadata['modality'] or 'Unknown'} (preferred: {self.modality})")
        print(f"      Resolution: {metadata['resolution'] or 'Unknown'} (preferred: {self.resolution})")
        print(f"      Noise: {metadata['noise_level'] or 'Unknown'} (preferred: {self.noise_level})")
        print(f"      Score: {best['score']}/80")
        
        return best

    def _generate_new_filename(self, original_name, metadata):
        """Generate new filename with WAN version and modality prefix, using smart naming."""
        wan_version, modality = self._extract_wan_info(metadata)
        
        if wan_version and modality:
            # Extract descriptive name from metadata
            descriptive_name = self._extract_descriptive_name(metadata, original_name)
            
            # Get model and version IDs
            model_id = metadata.get('model_id', 'unknown')
            version_id = metadata.get('version_id', 'unknown')
            
            # Create new name: wan-{version}-{modality}-{descriptive}-{model_id}-{version_id}
            new_name = f"wan-{wan_version}-{modality}-{descriptive_name}-{model_id}-{version_id}.safetensors"
            return new_name
        
        return original_name
    
    def _extract_descriptive_name(self, metadata, original_name):
        """Extract a descriptive name from metadata, falling back to original name."""
        model_name = (metadata.get('model_name', '') or '').lower()
        version_name = (metadata.get('version_name', '') or '').lower()
        
        # Try to extract meaningful names from model_name
        descriptive_parts = []
        
        # Common patterns to extract meaningful parts
        if 'thiccum' in model_name:
            descriptive_parts.append('thiccum')
        elif 'cumshot' in model_name:
            if 'facial' in model_name:
                descriptive_parts.append('facial-cumshot')
            else:
                descriptive_parts.append('cumshot')
        elif 'bouncing' in model_name and 'boobs' in model_name:
            descriptive_parts.append('bouncing-boobs')
        elif 'dr34mj0b' in model_name or 'dreamjob' in model_name:
            descriptive_parts.append('dreamjob')
        elif 'nsfw' in model_name:
            descriptive_parts.append('nsfw')
        elif 'bounce' in model_name:
            descriptive_parts.append('bounce')
        
        # If we found descriptive parts, use them
        if descriptive_parts:
            return '-'.join(descriptive_parts)
        
        # Fallback: clean up the original filename
        base_name = original_name.replace('.safetensors', '')
        
        # Remove common prefixes/suffixes that aren't descriptive
        base_name = base_name.replace('wan-', '').replace('WAN_', '').replace('_', '-')
        
        # Remove version numbers and training metadata
        import re
        base_name = re.sub(r'-v\d+$', '', base_name)  # Remove -v1, -v2, etc.
        base_name = re.sub(r'-\d+epo-\w+$', '', base_name)  # Remove -22epo-k3nk
        base_name = re.sub(r'-I2V-\d+epo-\w+$', '', base_name)  # Remove -I2V-22epo-k3nk
        
        # Clean up multiple dashes
        base_name = re.sub(r'-+', '-', base_name)
        base_name = base_name.strip('-')
        
        return base_name if base_name else 'unknown'

    def _rename_file(self, old_path, new_name):
        """Rename file and update sidecar/index if needed."""
        try:
            new_path = self.lora_dir / new_name
            if old_path != new_path and not new_path.exists():
                old_path.rename(new_path)
                print(f"  📝 Renamed: {old_path.name} → {new_name}")
                
                # Update sidecar JSON filename
                old_sidecar = old_path.with_suffix('.safetensors.json')
                new_sidecar = new_path.with_suffix('.safetensors.json')
                if old_sidecar.exists():
                    old_sidecar.rename(new_sidecar)
                
                return new_path
        except Exception as e:
            print(f"  Warning  Failed to rename {old_path.name}: {e}")
        
        return old_path

    def _update_index(self, metadata):
        """Merge/update an index JSON mapping filename -> metadata summary."""
        try:
            index = {}
            if self.index_file.exists():
                with open(self.index_file, 'r') as f:
                    try:
                        index = json.load(f) or {}
                    except json.JSONDecodeError:
                        index = {}
            index[metadata['filename']] = metadata
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.index_file, 'w') as f:
                json.dump(index, f, indent=2)
        except Exception as e:
            print(f"Warning  Failed to update index: {e}")
    
    def _file_already_exists(self, filename):
        """Check if file already exists (including renamed versions)"""
        # Check original filename
        filepath = self.lora_dir / filename
        if filepath.exists() and filepath.stat().st_size > 1024:
            return filepath
        
        # Check for renamed versions by looking at metadata and model names
        base_name = filename.replace('.safetensors', '')
        
        # First, try to match by model name patterns
        for existing_file in self.lora_dir.glob("wan-*-*.safetensors"):
            json_file = existing_file.with_suffix('.safetensors.json')
            if json_file.exists():
                try:
                    with open(json_file, 'r') as f:
                        metadata = json.load(f)
                    
                    # Check if this file corresponds to the original by model name
                    model_name = (metadata.get('model_name', '') or '').lower()
                    
                    # Pattern matching for common cases
                    if filename == 'wan-thiccum-v3.safetensors' and 'thiccum' in model_name:
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                    elif filename == 'bounceV_01.safetensors' and 'bouncing' in model_name and 'boobs' in model_name:
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                    elif filename == 'WAN_dr34mj0b.safetensors' and ('dr34mjob' in model_name or 'dr34mj0b' in model_name or 'dreamjob' in model_name):
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                    elif filename == 'wan-cumshot-I2V-22epo-k3nk.safetensors' and 'cumshot' in model_name and 'facial' in model_name:
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                    elif filename == 'wan_cumshot_i2v.safetensors' and 'cumshot' in model_name:
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                    elif filename == 'wan-nsfw-e14-fixed.safetensors' and ('nsfw' in model_name or 'realistic' in model_name):
                        if existing_file.stat().st_size > 1024:
                            return existing_file
                            
                except:
                    pass
        
        # Fallback: check for any file that contains the base name
        for existing_file in self.lora_dir.glob(f"*{base_name}*.safetensors"):
            if existing_file.stat().st_size > 1024:
                return existing_file
        
        return None
    
    def download_file(self, url, filename):
        """Download a file with progress bar"""
        # Check if file already exists (including renamed versions)
        existing_file = self._file_already_exists(filename)
        if existing_file:
            print(f"Success {filename} already exists as {existing_file.name} ({existing_file.stat().st_size:,} bytes)")
            return True
        
        print(f"📥 Downloading {filename}...")
        
        filepath = self.lora_dir / filename
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'User-Agent': 'CivitaiLoRADownloader/1.0'
        }
        
        try:
            response = requests.get(url, stream=True, headers=headers, timeout=300)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(filepath, 'wb') as f, tqdm(
                desc=filename,
                total=total_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    size = f.write(chunk)
                    pbar.update(size)
            
            print(f"Success Downloaded {filename} ({filepath.stat().st_size:,} bytes)")
            return True
            
        except Exception as e:
            print(f"Error Failed to download {filename}: {e}")
            if filepath.exists():
                filepath.unlink()  # Remove partial file
            return False
    
    def download_lora(self, lora_name, lora_info):
        """Download a specific LoRA"""
        print(f"\nAuto-detected Processing {lora_name}...")
        
        # Check if file already exists (including renamed versions)
        existing_file = self._file_already_exists(lora_name)
        if existing_file:
            print(f"Success {lora_name} already exists as {existing_file.name}")
            # Still update metadata if sidecar doesn't exist
            sidecar_path = existing_file.with_suffix('.safetensors.json')
            if not sidecar_path.exists():
                print(f"  📝 Creating missing metadata for {existing_file.name}")
                # We need to fetch metadata for the existing file
                if 'civitai_id' in lora_info:
                    model_details = self.get_model_details(lora_info['civitai_id'])
                    if model_details and 'version_id' in lora_info and 'file_id' in lora_info:
                        # Find the file info
                        target_file = None
                        for version in model_details.get('modelVersions', []):
                            if version['id'] == lora_info['version_id']:
                                for file in version.get('files', []):
                                    if file['id'] == lora_info['file_id']:
                                        target_file = file
                                        break
                                break
                        
                        if target_file:
                            metadata = self._build_metadata(
                                filename=existing_file.name,
                                model_data=model_details,
                                version_id=lora_info['version_id'],
                                file_info=target_file
                            )
                            self._write_sidecar(existing_file.name, metadata)
                            self._update_index(metadata)
            return True
        
        # Check if we have pinned version/file IDs for direct download
        if 'version_id' in lora_info and 'file_id' in lora_info:
            print(f"  📌 Using pinned version: {lora_info['version_id']}")
            model_details = self.get_model_details(lora_info['civitai_id'])
            if model_details:
                # Find the specific file in the pinned version
                target_file = None
                for ver in model_details.get('modelVersions', []):
                    if str(ver.get('id')) == str(lora_info['version_id']):
                        for f in ver.get('files', []):
                            if str(f.get('id')) == str(lora_info['file_id']):
                                target_file = f
                                break
                        break
                
                if target_file and target_file.get('downloadUrl'):
                    print(f"  Success Found pinned file: {target_file.get('name', lora_name)}")
                    print(f"  Using Model ID: {lora_info['civitai_id']}")
                    
                    # Perform download (or skip if already present)
                    ok = self.download_file(target_file['downloadUrl'], lora_name)
                    # Fetch metadata and write sidecar/index even if file existed
                    metadata = self._build_metadata(
                        filename=lora_name,
                        model_data=model_details,
                        version_id=lora_info['version_id'],
                        file_info=target_file
                    )
                    self._write_sidecar(lora_name, metadata)
                    self._update_index(metadata)
                    
                    # Rename file with WAN version and modality prefix
                    file_path = self.lora_dir / lora_name
                    if file_path.exists():
                        new_name = self._generate_new_filename(lora_name, metadata)
                        renamed_path = self._rename_file(file_path, new_name)
                        if renamed_path != file_path:
                            # Update metadata filename and re-save
                            metadata['filename'] = new_name
                            self._write_sidecar(new_name, metadata)
                            self._update_index(metadata)
                    
                    return ok
                else:
                    print(f"  Error Pinned file not found in version")
            else:
                print(f"  Error Could not fetch model details")
        
        # Fallback to search-based approach
        print(f"  Auto-detected Searching for {lora_name}...")
        
        # Try each search term
        for search_term in lora_info['search_terms']:
            print(f"  Searching: '{search_term}'")
            search_results = self.search_civitai(search_term)
            
            if search_results:
                lora_match = self.find_lora_by_name(lora_name, search_results)
                
                if lora_match:
                    print(f"  Success Found: {lora_match['model_name']}")
                    print(f"  Using Model ID: {lora_match['model_id']}")
                    
                    if lora_match['download_url']:
                        # Perform download (or skip if already present)
                        ok = self.download_file(lora_match['download_url'], lora_name)
                        # Fetch metadata and write sidecar/index even if file existed
                        model_details = self.get_model_details(lora_match['model_id'])
                        metadata = self._build_metadata(
                            filename=lora_name,
                            model_data=model_details,
                            version_id=lora_match['version_id'],
                            file_info=lora_match.get('file_info') or {}
                        )
                        self._write_sidecar(lora_name, metadata)
                        self._update_index(metadata)
                        
                        # Rename file with WAN version and modality prefix
                        file_path = self.lora_dir / lora_name
                        if file_path.exists():
                            new_name = self._generate_new_filename(lora_name, metadata)
                            renamed_path = self._rename_file(file_path, new_name)
                            if renamed_path != file_path:
                                # Update metadata filename and re-save
                                metadata['filename'] = new_name
                                self._write_sidecar(new_name, metadata)
                                self._update_index(metadata)
                        
                        return ok
                    else:
                        print(f"  Error No download URL found")
                else:
                    print(f"  Warning  No exact match found")
            else:
                print(f"  Error Search failed")
        
        print(f"  Error Could not find {lora_name}")
        return False
    
    def use_existing_script(self, lora_name):
        """Use the existing Civitai downloader script"""
        script_path = self.base_dir / "scripts" / "civitai_downloader.sh"
        
        if not script_path.exists():
            print(f"Error Civitai downloader script not found: {script_path}")
            return False
        
        print(f"🚀 Using existing Civitai downloader for {lora_name}")
        
        # Try different search terms
        search_terms = self.loras[lora_name]['search_terms']
        
        for search_term in search_terms:
            try:
                print(f"  Trying search: '{search_term}'")
                result = subprocess.run([
                    str(script_path), 'lora', search_term
                ], capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    print(f"  Success Download successful with search: '{search_term}'")
                    return True
                else:
                    print(f"  Warning  Search '{search_term}' failed: {result.stderr}")
            
            except subprocess.TimeoutExpired:
                print(f"  ⏰ Search '{search_term}' timed out")
            except Exception as e:
                print(f"  Error Error with search '{search_term}': {e}")
        
        return False
    
    def run(self):
        """Main execution"""
        print("🎭 Advanced Civitai LoRA Downloader")
        print("=" * 50)
        
        # Load API token
        if not self.load_token():
            print("\nUsing Falling back to existing Civitai downloader script...")
            return self.run_with_existing_script()
        
        # Sort LoRAs by priority (enabled first)
        sorted_loras = sorted(
            self.loras.items(),
            key=lambda x: (x[1]['priority'], x[0])
        )
        
        print(f"\nUsing LoRAs to download ({len(sorted_loras)} total):")
        for lora_name, info in sorted_loras:
            status = "Success Enabled" if info['enabled'] else "Error Disabled"
            print(f"  - {lora_name} - {info['description']} - {status}")
        
        # Download LoRAs (only enabled ones)
        enabled_loras = [(name, info) for name, info in sorted_loras if info.get('enabled', False)]
        success_count = 0
        total_count = len(enabled_loras)
        
        print(f"\n🚀 Starting downloads...")
        
        for lora_name, lora_info in enabled_loras:
            if self.download_lora(lora_name, lora_info):
                success_count += 1
        
        print(f"\nSummary Download Results: {success_count}/{total_count} LoRAs downloaded")
        
        if success_count < total_count:
            print("\nRenaming Trying with existing Civitai downloader script...")
            remaining_loras = []
            for lora_name, lora_info in enabled_loras:
                # Check if file exists using the same method as download_lora
                existing_file = self._file_already_exists(lora_name)
                if not existing_file:
                    remaining_loras.append(lora_name)
            
            for lora_name in remaining_loras:
                if self.use_existing_script(lora_name):
                    success_count += 1
        
        print(f"\nComplete Final Results: {success_count}/{total_count} LoRAs available")
        print(f"Using LoRAs directory: {self.lora_dir}")
        
        # Generate and display results table
        self._display_results_table()
        
        return success_count == total_count

    def _display_results_table(self):
        """Display a comprehensive results table showing all LoRAs grouped by WAN version"""
        print(f"\n{'='*80}")
        print("Summary LoRA Download Results Summary")
        print(f"{'='*80}")
        
        # Get all .safetensors files in the directory
        safetensors_files = list(self.lora_dir.glob("*.safetensors"))
        
        # Group files by WAN version
        grouped_files = {}
        
        for safetensors_file in safetensors_files:
            lora_name = safetensors_file.name
            
            # Get file size
            size_mb = safetensors_file.stat().st_size / (1024 * 1024)
            
            # Check for metadata file
            json_file = safetensors_file.with_suffix('.safetensors.json')
            if json_file.exists():
                try:
                    with open(json_file, 'r') as f:
                        metadata = json.load(f)
                    
                    # Extract WAN info
                    wan_version, modality = self._extract_wan_info(metadata)
                    wan_str = f"2.{wan_version[1]}" if wan_version and len(wan_version) == 2 else "?"
                    modality_str = modality.upper() if modality else "?"
                    
                    # Check if this was renamed
                    original_name = metadata.get('original_filename', lora_name)
                    if original_name != lora_name:
                        changes = "Rename"
                    else:
                        changes = "Download"
                        
                except Exception as e:
                    wan_str = "?"
                    modality_str = "?"
                    changes = "Error"
            else:
                wan_str = "?"
                modality_str = "?"
                changes = "No meta"
            
            # Determine single state
            if size_mb > 100:  # Good size file
                state = "Ready"
            elif size_mb > 1:  # Small file might be placeholder
                state = "Small"
            else:
                state = "Missing"
            
            # Group by WAN version
            wan_key = wan_str
            
            if wan_key not in grouped_files:
                grouped_files[wan_key] = []
            
            grouped_files[wan_key].append({
                'name': lora_name,
                'state': state,
                'size': f"{size_mb:.1f}MB",
                'modality': modality_str
            })
        
        # Sort WAN versions (put ? last)
        wan_versions = sorted(grouped_files.keys(), key=lambda x: (x == "?", x))
        
        # Display grouped results
        for wan_version in wan_versions:
            print(f"\n=== WAN {wan_version} ===")
            
            files = grouped_files[wan_version]
            # Sort files by name (this will naturally group modalities)
            files.sort(key=lambda x: x['name'])
            
            for file_info in files:
                # Truncate long names with ellipses (expanded width)
                display_name = file_info['name']
                if len(display_name) > 60:
                    display_name = display_name[:57] + "..."
                
                print(f"{display_name:<60} {file_info['state']:<8} {file_info['size']:<10} {file_info['modality']:<6}")
        
        # Summary statistics
        total_files = len(safetensors_files)
        available_files = len([f for f in safetensors_files if f.stat().st_size > 100 * 1024 * 1024])
        total_size = sum(f.stat().st_size for f in safetensors_files) / (1024 * 1024 * 1024)  # GB
        
        print(f"\nSummary {available_files}/{total_files} LoRAs available ({total_size:.1f}GB total)")
    
    def _display_trigger_words(self):
        """Display trigger words for all LoRAs"""
        print(f"\n{'='*80}")
        print("LoRA Trigger Words")
        print(f"{'='*80}")
        
        # Get all .safetensors files in the directory
        safetensors_files = list(self.lora_dir.glob("*.safetensors"))
        
        # Group files by WAN version
        grouped_files = {}
        
        for safetensors_file in safetensors_files:
            lora_name = safetensors_file.name
            
            # Check for metadata file
            json_file = safetensors_file.with_suffix('.safetensors.json')
            if json_file.exists():
                try:
                    with open(json_file, 'r') as f:
                        metadata = json.load(f)
                    
                    # Extract WAN info
                    wan_version, modality = self._extract_wan_info(metadata)
                    wan_str = f"2.{wan_version[1]}" if wan_version and len(wan_version) == 2 else "?"
                    modality_str = modality.upper() if modality else "?"
                    
                    # Get trigger words
                    triggers = metadata.get('trained_words', [])
                    
                except Exception as e:
                    wan_str = "?"
                    modality_str = "?"
                    triggers = []
            else:
                wan_str = "?"
                modality_str = "?"
                triggers = []
            
            # Group by WAN version
            wan_key = wan_str
            
            if wan_key not in grouped_files:
                grouped_files[wan_key] = []
            
            grouped_files[wan_key].append({
                'name': lora_name,
                'modality': modality_str,
                'triggers': triggers
            })
        
        # Sort WAN versions (put ? last)
        wan_versions = sorted(grouped_files.keys(), key=lambda x: (x == "?", x))
        
        # Display grouped results
        for wan_version in wan_versions:
            print(f"\n=== WAN {wan_version} ===")
            
            files = grouped_files[wan_version]
            # Sort files by name
            files.sort(key=lambda x: x['name'])
            
            for file_info in files:
                # Truncate long names with ellipses
                display_name = file_info['name']
                if len(display_name) > 50:
                    display_name = display_name[:47] + "..."
                
                print(f"\n{display_name}")
                
                if file_info['triggers']:
                    print(f"   Triggers:")
                    for trigger in file_info['triggers']:
                        print(f"      - {trigger}")
                else:
                    print(f"   Triggers: None")
    
    def _lookup_trigger_words(self, filename):
        """Look up trigger words for matching LoRA files (searches both filenames and trigger words)"""
        search_term = filename.lower()
        matching_files = []
        
        for safetensors_file in self.lora_dir.glob("*.safetensors"):
            json_file = safetensors_file.with_suffix('.safetensors.json')
            
            # Check filename match
            filename_match = search_term in safetensors_file.name.lower()
            
            # Check trigger words match
            trigger_match = False
            if json_file.exists():
                try:
                    with open(json_file, 'r') as f:
                        metadata = json.load(f)
                    triggers = metadata.get('trained_words', [])
                    for trigger in triggers:
                        if search_term in trigger.lower():
                            trigger_match = True
                            break
                except:
                    pass
            
            # Include file if it matches filename OR trigger words
            if filename_match or trigger_match:
                matching_files.append(safetensors_file)
        
        if not matching_files:
            print(f"No LoRA files found matching '{filename}'")
            return
        
        print(f"Trigger Words for '{filename}' matches:")
        
        for safetensors_file in matching_files:
            json_file = safetensors_file.with_suffix('.safetensors.json')
            
            print(f"\n{safetensors_file.name}")
            
            if json_file.exists():
                try:
                    with open(json_file, 'r') as f:
                        metadata = json.load(f)
                    
                    # Get trigger words
                    triggers = metadata.get('trained_words', [])
                    
                    if triggers:
                        # Show each trigger word on its own line
                        for trigger in triggers:
                            print(f"   - {trigger}")
                    else:
                        print(f"   - (none)")
                        
                except Exception as e:
                    print(f"   Error reading metadata")
            else:
                print(f"   No metadata")
    
    def run_with_existing_script(self):
        """Run using the existing Civitai downloader script"""
        print("Using existing Civitai downloader script...")
        
        script_path = self.base_dir / "scripts" / "civitai_downloader.sh"
        
        if not script_path.exists():
            print(f"Error Civitai downloader script not found: {script_path}")
            return False
        
        success_count = 0
        total_count = len(self.loras)
        
        # Try downloading with different search terms
        search_terms_to_try = [
            'wan thiccum',
            'wan dr34mj0b', 
            'bounceV',
            'wan nsfw',
            'wan cumshot',
            'facials60',
            'handjob wan'
        ]
        
        for search_term in search_terms_to_try:
            print(f"\nAuto-detected Trying search: '{search_term}'")
            try:
                result = subprocess.run([
                    str(script_path), 'lora', search_term
                ], capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    print(f"Success Download successful with search: '{search_term}'")
                    success_count += 1
                else:
                    print(f"Warning  Search '{search_term}' failed")
                    print(f"Error: {result.stderr}")
            
            except subprocess.TimeoutExpired:
                print(f"⏰ Search '{search_term}' timed out")
            except Exception as e:
                print(f"Error Error with search '{search_term}': {e}")
        
        print(f"\nSummary Results: {success_count} successful downloads")
        return success_count > 0

def fetch_model_info_from_url(url):
    """
    Helper function to fetch Civitai model metadata from URL
    Returns a dictionary with model info for easy addition to self.loras
    """
    try:
        import requests
        from urllib.parse import urlparse, parse_qs
        
        # Parse URL to extract model_id and version_id
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        
        if 'models' not in path_parts:
            print("Error Invalid Civitai model URL")
            return None
            
        model_id = None
        version_id = None
        
        # Extract model ID from path
        for i, part in enumerate(path_parts):
            if part == 'models' and i + 1 < len(path_parts):
                model_id = path_parts[i + 1]
                break
        
        # Extract version ID from query params
        query_params = parse_qs(parsed.query)
        if 'modelVersionId' in query_params:
            version_id = query_params['modelVersionId'][0]
        
        if not model_id:
            print("Error Could not extract model ID from URL")
            return None
            
        print(f"Auto-detected Fetching metadata for model {model_id}...")
        if version_id:
            print(f"Trigger Target version: {version_id}")
        
        # Get model info
        model_url = f'https://civitai.com/api/v1/models/{model_id}'
        response = requests.get(model_url)
        
        if response.status_code != 200:
            print(f"Error Error fetching model: {response.status_code}")
            return None
            
        model_data = response.json()
        
        # Find the specific version
        target_version = None
        if version_id:
            for version in model_data.get('modelVersions', []):
                if str(version['id']) == version_id:
                    target_version = version
                    break
        
        if target_version:
            # Find the .safetensors file
            safetensors_file = None
            for file in target_version.get('files', []):
                if file.get('name', '').endswith('.safetensors'):
                    safetensors_file = file
                    break
            
            if safetensors_file:
                return {
                    'filename': safetensors_file['name'],
                    'description': model_data['name'],
                    'strength': 1.0,
                    'enabled': True,
                    'search_terms': [model_data['name'].lower(), 'wan'],
                    'civitai_id': str(model_data['id']),
                    'version_id': target_version['id'],
                    'file_id': safetensors_file['id'],
                    'priority': 1,
                    'base_model': target_version.get('baseModel', 'Unknown')
                }
            else:
                print("Error No .safetensors file found in this version")
        else:
            print(f"Error Version {version_id} not found")
            
    except Exception as e:
        print(f"Error Error fetching model info: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Advanced Civitai LoRA Downloader for WAN 2.1 FaceBlast Workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
    Examples:
      # List all configured LoRAs (safe - no downloads)
      python3 civitai_lora_downloader.py --list-loras
      
      # Preview what would be downloaded (safe - no downloads)
      python3 civitai_lora_downloader.py --dry-run
      
      # Disable all LoRAs and preview (safe - no downloads)
      python3 civitai_lora_downloader.py --disable-all --dry-run
      
      # Use default ComfyUI directory
      python3 civitai_lora_downloader.py
      
      # Specify ComfyUI directory
      python3 civitai_lora_downloader.py --comfyui-dir /path/to/ComfyUI
      
      # Specify both ComfyUI and base directory
      python3 civitai_lora_downloader.py --comfyui-dir /path/to/ComfyUI --base-dir /path/to/base
      
      # Use relative path
      python3 civitai_lora_downloader.py --comfyui-dir ./ComfyUI
      
      # Add a new model from Civitai URL (helper function)
      python3 -c "from civitai_lora_downloader import fetch_model_info_from_url; print(fetch_model_info_from_url('https://civitai.com/models/123456/model-name?modelVersionId=789012'))"
      
      # Filter by preferences with fallback (uses config file defaults)
      python3 civitai_lora_downloader.py
      python3 civitai_lora_downloader.py --wan-version 2.1 --modality i2v --resolution 480
      python3 civitai_lora_downloader.py --wan-version 2.2 --modality t2v --resolution 720 --noise-level low
      python3 civitai_lora_downloader.py --wan-version any --modality any --resolution any
      
      # Configuration management
      python3 civitai_lora_downloader.py --show-defaults
      python3 civitai_lora_downloader.py --set-defaults wan_version=2.2 modality=t2v
      python3 civitai_lora_downloader.py --reset-defaults
        """
    )
    
    parser.add_argument(
        '--comfyui-dir', 
        type=str,
        help='Path to ComfyUI root directory (default: auto-detect or ./ComfyUI)'
    )
    
    parser.add_argument(
        '--base-dir',
        type=str, 
        help='Base directory for token file (default: ComfyUI parent directory)'
    )
    
    parser.add_argument(
        '--list-loras',
        action='store_true',
        help='List all configured LoRAs and exit'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be downloaded without actually downloading'
    )
    
    parser.add_argument(
        '--disable-all',
        action='store_true',
        help='Disable all LoRAs before running (useful for testing)'
    )
    
    parser.add_argument(
        '--wan-version',
        choices=['2.1', '2.2', 'any'],
        default=None,
        help='Preferred WAN version (2.1, 2.2, or any). Falls back to other versions if preferred not available.'
    )
    
    parser.add_argument(
        '--modality',
        choices=['i2v', 't2v', 'any'],
        default=None,
        help='Preferred modality (i2v, t2v, or any). Falls back to other modalities if preferred not available.'
    )
    
    parser.add_argument(
        '--resolution',
        choices=['480', '720', 'any'],
        default=None,
        help='Preferred resolution (480, 720, or any). Falls back to other resolutions if preferred not available.'
    )
    
    parser.add_argument(
        '--noise-level',
        choices=['low', 'high', 'any'],
        default=None,
        help='Preferred noise level (low, high, or any). Only applies to WAN 2.2 models. Falls back to other noise levels if preferred not available.'
    )
    
    parser.add_argument(
        '--show-defaults',
        action='store_true',
        help='Show current default configuration and exit'
    )
    
    parser.add_argument(
        '--set-defaults',
        nargs='+',
        metavar='KEY=VALUE',
        help='Set default configuration values (e.g., --set-defaults wan_version=2.2 modality=t2v)'
    )
    
    parser.add_argument(
        '--reset-defaults',
        action='store_true',
        help='Reset to built-in defaults and exit'
    )
    
    parser.add_argument(
        '--show-results',
        action='store_true',
        help='Show results table and exit (no downloads)'
    )
    parser.add_argument(
        '--show-triggers',
        action='store_true',
        help='Show trigger words for all LoRAs'
    )
    parser.add_argument(
        '--lookup-triggers',
        type=str,
        metavar='FILENAME',
        help='Look up trigger words for a specific LoRA file'
    )
    
    # Load defaults first
    defaults = load_defaults()
    
    # Update argument defaults with loaded configuration
    parser.set_defaults(
        wan_version=defaults['wan_version'],
        modality=defaults['modality'],
        resolution=defaults['resolution'],
        noise_level=defaults['noise_level']
    )
    
    args = parser.parse_args()
    
    # Use loaded defaults for any None values
    if args.wan_version is None:
        args.wan_version = defaults['wan_version']
    if args.modality is None:
        args.modality = defaults['modality']
    if args.resolution is None:
        args.resolution = defaults['resolution']
    if args.noise_level is None:
        args.noise_level = defaults['noise_level']
    
    # Handle configuration management commands
    if args.show_defaults:
        show_current_defaults()
        return
    
    if args.reset_defaults:
        config_file = Path.home() / ".civitai_lora_defaults.json"
        if config_file.exists():
            config_file.unlink()
            print("Renaming Reset to built-in defaults")
        else:
            print("Using Already using built-in defaults")
        return
    
    if args.set_defaults:
        defaults = load_defaults()
        for setting in args.set_defaults:
            if '=' in setting:
                key, value = setting.split('=', 1)
                if key in ['wan_version', 'modality', 'resolution', 'noise_level']:
                    defaults[key] = value
                    print(f"Using Set {key} = {value}")
                else:
                    print(f"Warning  Unknown setting: {key}")
            else:
                print(f"Warning  Invalid format: {setting} (use KEY=VALUE)")
        
        if save_defaults(defaults):
            print("Success Defaults updated successfully")
        return
    
    if args.show_results:
        # Initialize downloader just for results table
        downloader = CivitaiLoRADownloader(
            comfyui_dir=args.comfyui_dir,
            base_dir=args.base_dir,
            wan_version=args.wan_version,
            modality=args.modality,
            resolution=args.resolution,
            noise_level=args.noise_level
        )
        downloader._display_results_table()
        return
    
    if args.show_triggers:
        # Initialize downloader just for trigger words
        downloader = CivitaiLoRADownloader(
            comfyui_dir=args.comfyui_dir,
            base_dir=args.base_dir,
            wan_version=args.wan_version,
            modality=args.modality,
            resolution=args.resolution,
            noise_level=args.noise_level
        )
        downloader._display_trigger_words()
        return
    
    if args.lookup_triggers:
        # Initialize downloader just for trigger lookup
        downloader = CivitaiLoRADownloader(
            comfyui_dir=args.comfyui_dir,
            base_dir=args.base_dir,
            wan_version=args.wan_version,
            modality=args.modality,
            resolution=args.resolution,
            noise_level=args.noise_level
        )
        downloader._lookup_trigger_words(args.lookup_triggers)
        return
    
    # Auto-detect ComfyUI directory if not provided
    if not args.comfyui_dir:
        # Try to find ComfyUI in current directory or common locations
        current_dir = Path.cwd()
        possible_paths = [
            current_dir / "ComfyUI",
            current_dir.parent / "ComfyUI", 
            Path("/home/yuji/Code/Umeiart/ComfyUI"),  # Default fallback
        ]
        
        for path in possible_paths:
            if path.exists() and (path / "main.py").exists():
                args.comfyui_dir = str(path)
                print(f"Auto-detected Auto-detected ComfyUI directory: {args.comfyui_dir}")
                break
        
        if not args.comfyui_dir:
            print("Error Could not auto-detect ComfyUI directory")
            print("Tip Please specify --comfyui-dir /path/to/ComfyUI")
            sys.exit(1)
    
    # Validate ComfyUI directory
    comfyui_path = Path(args.comfyui_dir)
    if not comfyui_path.exists():
        print(f"Error ComfyUI directory does not exist: {comfyui_path}")
        sys.exit(1)
    
    if not (comfyui_path / "main.py").exists():
        print(f"Error Invalid ComfyUI directory (main.py not found): {comfyui_path}")
        sys.exit(1)
    
    print(f"Using ComfyUI directory: {comfyui_path}")
    print(f"Using base directory: {Path(args.base_dir) if args.base_dir else comfyui_path.parent}")
    
    # Initialize downloader
    downloader = CivitaiLoRADownloader(
        comfyui_dir=args.comfyui_dir,
        base_dir=args.base_dir,
        wan_version=args.wan_version,
        modality=args.modality,
        resolution=args.resolution,
        noise_level=args.noise_level
    )
    
    # Handle disable-all option
    if args.disable_all:
        print("Using Disabling all LoRAs...")
        for lora_name in downloader.loras:
            downloader.loras[lora_name]['enabled'] = False
    
    if args.list_loras:
        print("\nUsing Configured LoRAs:")
        for lora_name, lora_info in downloader.loras.items():
            status = "Success Enabled" if lora_info.get('enabled', False) else "Error Disabled"
            print(f"  - {lora_name} - {lora_info.get('description', 'No description')} - {status}")
        return
    
    if args.dry_run:
        print("\nAuto-detected DRY RUN MODE - No files will be downloaded")
        print("Using LoRAs that would be processed:")
        enabled_loras = [name for name, info in downloader.loras.items() if info.get('enabled', False)]
        if enabled_loras:
            for lora_name in enabled_loras:
                print(f"  - {lora_name}")
        else:
            print("  (No LoRAs are currently enabled)")
        return
    
    # Run the downloader
    success = downloader.run()
    
    if success:
        print("\nComplete All LoRAs downloaded successfully!")
        print("🚀 Your FaceBlast workflow is ready to use!")
    else:
        print("\nWarning  Some LoRAs may need manual download")
        print("📖 Check LORA_DOWNLOAD_INSTRUCTIONS.md for manual steps")

if __name__ == "__main__":
    main()






