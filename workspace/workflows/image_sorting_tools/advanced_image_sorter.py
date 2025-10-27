#!/usr/bin/env python3
"""
Advanced Image Content Sorter with CLIP
Advanced version with YAML configuration, custom queries, and t-SNE visualization
"""

import os
import shutil
import argparse
import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from PIL import Image
import torch
import clip
from sklearn.cluster import KMeans
from sklearn.manifold import TSNE
import matplotlib.pyplot as plt
import seaborn as sns
from tqdm import tqdm

class AdvancedImageSorter:
    def __init__(self, config_path: str = "sorter_config.yaml"):
        """Initialize the sorter with configuration."""
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Using device: {self.device}")
        
        # Load CLIP model
        self.model, self.preprocess = clip.load("ViT-B/32", device=self.device)
        
        # Load configuration
        self.config = self.load_config(config_path)
        
        # Image extensions
        self.image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
        
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Config file {config_path} not found. Using default configuration.")
            return self.get_default_config()
    
    def get_default_config(self) -> Dict[str, Any]:
        """Return default configuration."""
        return {
            'categories': {
                'portraits': ['portrait', 'face', 'person', 'headshot'],
                'landscapes': ['landscape', 'nature', 'scenery', 'outdoor'],
                'animals': ['animal', 'cat', 'dog', 'wildlife'],
                'architecture': ['building', 'architecture', 'house'],
                'vehicles': ['car', 'vehicle', 'transportation'],
                'food': ['food', 'meal', 'cooking'],
                'abstract': ['abstract', 'pattern', 'texture'],
                'art': ['artwork', 'painting', 'drawing']
            },
            'similarity_threshold': 0.7,
            'clustering': {
                'max_clusters': 10,
                'min_cluster_size': 3
            },
            'visualization': {
                'figure_size': [12, 8],
                'dpi': 150
            }
        }
    
    def get_image_files(self, directory: str) -> List[str]:
        """Get all image files from directory."""
        image_files = []
        for root, _, files in os.walk(directory):
            for file in files:
                if Path(file).suffix.lower() in self.image_extensions:
                    image_files.append(os.path.join(root, file))
        return image_files
    
    def encode_images(self, image_paths: List[str]) -> np.ndarray:
        """Encode images using CLIP."""
        features = []
        
        print("Encoding images...")
        for img_path in tqdm(image_paths):
            try:
                image = Image.open(img_path).convert('RGB')
                image = self.preprocess(image).unsqueeze(0).to(self.device)
                
                with torch.no_grad():
                    image_features = self.model.encode_image(image)
                    image_features /= image_features.norm(dim=-1, keepdim=True)
                    features.append(image_features.cpu().numpy().flatten())
                    
            except Exception as e:
                print(f"Error processing {img_path}: {e}")
                continue
        
        return np.array(features)
    
    def encode_text(self, texts: List[str]) -> np.ndarray:
        """Encode text descriptions using CLIP."""
        text_tokens = clip.tokenize(texts).to(self.device)
        
        with torch.no_grad():
            text_features = self.model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            
        return text_features.cpu().numpy()
    
    def sort_by_categories(self, input_dir: str, output_dir: str, copy_files: bool = True):
        """Sort images by predefined categories."""
        image_paths = self.get_image_files(input_dir)
        if not image_paths:
            print("No images found in the input directory.")
            return
        
        print(f"Found {len(image_paths)} images")
        
        # Encode images
        image_features = self.encode_images(image_paths)
        if len(image_features) == 0:
            print("No images could be processed.")
            return
        
        # Prepare categories
        categories = self.config['categories']
        category_texts = {}
        
        for category, keywords in categories.items():
            # Create text prompts for each category
            prompts = [f"a photo of {keyword}" for keyword in keywords]
            category_texts[category] = prompts
        
        # Create output directories
        os.makedirs(output_dir, exist_ok=True)
        uncategorized_dir = os.path.join(output_dir, "uncategorized")
        
        for category in categories.keys():
            os.makedirs(os.path.join(output_dir, category), exist_ok=True)
        
        # Sort images
        threshold = self.config['similarity_threshold']
        results = {}
        
        print("Categorizing images...")
        for i, img_path in enumerate(tqdm(image_paths)):
            if i >= len(image_features):
                continue
                
            img_feature = image_features[i:i+1]
            best_category = None
            best_score = 0
            
            # Check each category
            for category, prompts in category_texts.items():
                text_features = self.encode_text(prompts)
                
                # Calculate similarity with all prompts in category
                similarities = np.dot(img_feature, text_features.T).flatten()
                max_similarity = np.max(similarities)
                
                if max_similarity > best_score:
                    best_score = max_similarity
                    best_category = category
            
            # Place image in best category if above threshold
            if best_score >= threshold and best_category:
                target_dir = os.path.join(output_dir, best_category)
                results[img_path] = (best_category, best_score)
            else:
                target_dir = uncategorized_dir
                results[img_path] = ("uncategorized", best_score)
            
            # Copy or move file
            os.makedirs(target_dir, exist_ok=True)
            filename = os.path.basename(img_path)
            target_path = os.path.join(target_dir, filename)
            
            if copy_files:
                shutil.copy2(img_path, target_path)
            else:
                shutil.move(img_path, target_path)
        
        # Save results
        self.save_results(results, os.path.join(output_dir, "categorization_results.json"))
        self.print_summary(results)
    
    def cluster_images(self, input_dir: str, output_dir: str, n_clusters: Optional[int] = None, copy_files: bool = True):
        """Cluster images by content similarity."""
        image_paths = self.get_image_files(input_dir)
        if not image_paths:
            print("No images found in the input directory.")
            return
        
        print(f"Found {len(image_paths)} images")
        
        # Encode images
        image_features = self.encode_images(image_paths)
        if len(image_features) == 0:
            print("No images could be processed.")
            return
        
        # Determine number of clusters
        if n_clusters is None:
            n_clusters = min(self.config['clustering']['max_clusters'], 
                           max(2, len(image_features) // self.config['clustering']['min_cluster_size']))
        
        print(f"Clustering into {n_clusters} groups...")
        
        # Perform clustering
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(image_features)
        
        # Create output directories
        os.makedirs(output_dir, exist_ok=True)
        for i in range(n_clusters):
            os.makedirs(os.path.join(output_dir, f"cluster_{i+1:02d}"), exist_ok=True)
        
        # Move images to clusters
        results = {}
        for i, (img_path, cluster_id) in enumerate(zip(image_paths, cluster_labels)):
            target_dir = os.path.join(output_dir, f"cluster_{cluster_id+1:02d}")
            filename = os.path.basename(img_path)
            target_path = os.path.join(target_dir, filename)
            
            if copy_files:
                shutil.copy2(img_path, target_path)
            else:
                shutil.move(img_path, target_path)
            
            results[img_path] = f"cluster_{cluster_id+1:02d}"
        
        # Save results
        self.save_cluster_results(results, image_features, cluster_labels, 
                                os.path.join(output_dir, "clustering_results.json"))
        
        # Create visualization
        self.visualize_clusters(image_features, cluster_labels, 
                              os.path.join(output_dir, "cluster_visualization.png"))
        
        print(f"Images clustered into {n_clusters} groups")
        
    def query_images(self, input_dir: str, query: str, output_dir: str, 
                    top_k: int = 20, copy_files: bool = True):
        """Find images matching a text query."""
        image_paths = self.get_image_files(input_dir)
        if not image_paths:
            print("No images found in the input directory.")
            return
        
        print(f"Found {len(image_paths)} images")
        print(f"Searching for: '{query}'")
        
        # Encode images and query
        image_features = self.encode_images(image_paths)
        if len(image_features) == 0:
            print("No images could be processed.")
            return
            
        query_features = self.encode_text([query])
        
        # Calculate similarities
        similarities = np.dot(image_features, query_features.T).flatten()
        
        # Get top-k most similar images
        top_indices = np.argsort(similarities)[::-1][:top_k]
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        # Copy top matching images
        results = {}
        print(f"Copying top {len(top_indices)} matches...")
        
        for rank, idx in enumerate(top_indices, 1):
            img_path = image_paths[idx]
            similarity = similarities[idx]
            
            filename = os.path.basename(img_path)
            name, ext = os.path.splitext(filename)
            new_filename = f"{rank:02d}_{similarity:.3f}_{name}{ext}"
            target_path = os.path.join(output_dir, new_filename)
            
            if copy_files:
                shutil.copy2(img_path, target_path)
            else:
                shutil.move(img_path, target_path)
            
            results[img_path] = {
                'rank': rank,
                'similarity': float(similarity),
                'new_filename': new_filename
            }
        
        # Save results
        query_results = {
            'query': query,
            'total_images': len(image_paths),
            'top_k': top_k,
            'results': results
        }
        
        with open(os.path.join(output_dir, "query_results.json"), 'w') as f:
            json.dump(query_results, f, indent=2)
        
        print(f"Found {len(top_indices)} matches for query: '{query}'")
    
    def visualize_clusters(self, features: np.ndarray, labels: np.ndarray, output_path: str):
        """Create t-SNE visualization of clusters."""
        print("Creating visualization...")
        
        # Reduce dimensionality with t-SNE
        tsne = TSNE(n_components=2, random_state=42, perplexity=min(30, len(features)-1))
        features_2d = tsne.fit_transform(features)
        
        # Create plot
        fig_size = self.config['visualization']['figure_size']
        dpi = self.config['visualization']['dpi']
        
        plt.figure(figsize=fig_size, dpi=dpi)
        scatter = plt.scatter(features_2d[:, 0], features_2d[:, 1], 
                            c=labels, cmap='tab10', alpha=0.7, s=50)
        
        plt.title('Image Clusters Visualization (t-SNE)', fontsize=16)
        plt.xlabel('t-SNE Component 1', fontsize=12)
        plt.ylabel('t-SNE Component 2', fontsize=12)
        plt.colorbar(scatter, label='Cluster')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close()
        
        print(f"Visualization saved to: {output_path}")
    
    def save_results(self, results: Dict, output_path: str):
        """Save categorization results to JSON."""
        # Convert results for JSON serialization
        json_results = {}
        for img_path, (category, score) in results.items():
            json_results[img_path] = {
                'category': category,
                'score': float(score)
            }
        
        with open(output_path, 'w') as f:
            json.dump(json_results, f, indent=2)
        
        print(f"Results saved to: {output_path}")
    
    def save_cluster_results(self, results: Dict, features: np.ndarray, 
                           labels: np.ndarray, output_path: str):
        """Save clustering results to JSON."""
        cluster_info = {}
        for i in range(len(np.unique(labels))):
            cluster_images = [img for img, cluster in results.items() 
                            if cluster == f"cluster_{i+1:02d}"]
            cluster_info[f"cluster_{i+1:02d}"] = {
                'count': len(cluster_images),
                'images': cluster_images
            }
        
        results_data = {
            'cluster_info': cluster_info,
            'total_images': len(results),
            'num_clusters': len(np.unique(labels))
        }
        
        with open(output_path, 'w') as f:
            json.dump(results_data, f, indent=2)
        
        print(f"Clustering results saved to: {output_path}")
    
    def print_summary(self, results: Dict):
        """Print categorization summary."""
        category_counts = {}
        for _, (category, _) in results.items():
            category_counts[category] = category_counts.get(category, 0) + 1
        
        print("\nCategorization Summary:")
        print("-" * 30)
        for category, count in sorted(category_counts.items()):
            print(f"{category}: {count} images")
        print(f"Total: {len(results)} images")

def main():
    parser = argparse.ArgumentParser(description="Advanced Image Content Sorter with CLIP")
    parser.add_argument("input_dir", help="Input directory containing images")
    parser.add_argument("output_dir", help="Output directory for sorted images")
    parser.add_argument("--mode", choices=["categories", "cluster", "query"], 
                       default="categories", help="Sorting mode")
    parser.add_argument("--query", help="Text query for query mode")
    parser.add_argument("--clusters", type=int, help="Number of clusters for cluster mode")
    parser.add_argument("--top-k", type=int, default=20, 
                       help="Number of top results for query mode")
    parser.add_argument("--config", default="sorter_config.yaml", 
                       help="Configuration file path")
    parser.add_argument("--move", action="store_true", 
                       help="Move files instead of copying")
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.mode == "query" and not args.query:
        parser.error("Query mode requires --query argument")
    
    if not os.path.exists(args.input_dir):
        print(f"Error: Input directory '{args.input_dir}' does not exist")
        return
    
    # Initialize sorter
    sorter = AdvancedImageSorter(args.config)
    copy_files = not args.move
    
    # Run sorting based on mode
    if args.mode == "categories":
        sorter.sort_by_categories(args.input_dir, args.output_dir, copy_files)
    elif args.mode == "cluster":
        sorter.cluster_images(args.input_dir, args.output_dir, args.clusters, copy_files)
    elif args.mode == "query":
        sorter.query_images(args.input_dir, args.query, args.output_dir, 
                          args.top_k, copy_files)

if __name__ == "__main__":
    main()
