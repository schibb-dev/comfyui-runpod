#!/usr/bin/env python3
"""
Image Content Sorter using CLIP embeddings
==========================================

This script analyzes images using CLIP (Contrastive Language-Image Pre-training) 
to sort them according to their visual content and semantic meaning.

Features:
- Automatic content-based image sorting
- Clustering by visual similarity
- Text-based category matching
- Batch processing of image folders
- Export sorted results to organized folders

Requirements:
- torch
- transformers
- PIL (Pillow)
- numpy
- scikit-learn
- pathlib

Usage:
    python image_content_sorter.py --input_dir /path/to/images --output_dir /path/to/sorted
"""

import os
import argparse
import shutil
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import numpy as np
from PIL import Image
import torch
from transformers import CLIPProcessor, CLIPModel
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import cosine_similarity
import json

class ImageContentSorter:
    def __init__(self, model_name: str = "openai/clip-vit-base-patch32"):
        """
        Initialize the Image Content Sorter with CLIP model.
        
        Args:
            model_name: HuggingFace model name for CLIP
        """
        print(f"Loading CLIP model: {model_name}")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CLIPModel.from_pretrained(model_name).to(self.device)
        self.processor = CLIPProcessor.from_pretrained(model_name)
        
        # Predefined categories for content-based sorting
        self.categories = {
            "portraits": ["portrait", "face", "person", "headshot", "selfie"],
            "landscapes": ["landscape", "nature", "scenery", "mountains", "forest", "ocean", "sky"],
            "animals": ["animal", "pet", "dog", "cat", "wildlife", "bird", "horse"],
            "architecture": ["building", "house", "city", "urban", "architecture", "street"],
            "vehicles": ["car", "truck", "motorcycle", "vehicle", "transportation"],
            "food": ["food", "meal", "cooking", "restaurant", "kitchen", "dining"],
            "art": ["artwork", "painting", "drawing", "sculpture", "creative", "artistic"],
            "abstract": ["abstract", "pattern", "texture", "geometric", "artistic", "design"],
            "objects": ["object", "still life", "product", "item", "thing"],
            "sports": ["sport", "athletic", "game", "competition", "exercise", "fitness"]
        }
        
    def load_images(self, input_dir: Path) -> List[Tuple[Path, Image.Image]]:
        """
        Load all images from the input directory.
        
        Args:
            input_dir: Path to directory containing images
            
        Returns:
            List of tuples (file_path, PIL_Image)
        """
        supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}
        images = []
        
        print(f"Loading images from: {input_dir}")
        
        for file_path in input_dir.rglob("*"):
            if file_path.suffix.lower() in supported_formats:
                try:
                    img = Image.open(file_path).convert('RGB')
                    images.append((file_path, img))
                except Exception as e:
                    print(f"Warning: Could not load {file_path}: {e}")
                    
        print(f"Loaded {len(images)} images")
        return images
    
    def get_image_embeddings(self, images: List[Tuple[Path, Image.Image]]) -> np.ndarray:
        """
        Generate CLIP embeddings for all images.
        
        Args:
            images: List of (file_path, PIL_Image) tuples
            
        Returns:
            numpy array of image embeddings
        """
        embeddings = []
        
        print("Generating image embeddings...")
        
        for i, (file_path, img) in enumerate(images):
            try:
                inputs = self.processor(images=img, return_tensors="pt").to(self.device)
                
                with torch.no_grad():
                    image_features = self.model.get_image_features(**inputs)
                    # Normalize embeddings
                    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                    
                embeddings.append(image_features.cpu().numpy().flatten())
                
                if (i + 1) % 50 == 0:
                    print(f"Processed {i + 1}/{len(images)} images")
                    
            except Exception as e:
                print(f"Warning: Could not process {file_path}: {e}")
                # Add zero embedding as placeholder
                embeddings.append(np.zeros(512))  # CLIP embedding size
        
        return np.array(embeddings)
    
    def get_text_embeddings(self, texts: List[str]) -> np.ndarray:
        """
        Generate CLIP embeddings for text descriptions.
        
        Args:
            texts: List of text descriptions
            
        Returns:
            numpy array of text embeddings
        """
        inputs = self.processor(text=texts, return_tensors="pt", padding=True).to(self.device)
        
        with torch.no_grad():
            text_features = self.model.get_text_features(**inputs)
            # Normalize embeddings
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            
        return text_features.cpu().numpy()
    
    def sort_by_similarity_clustering(self, 
                                    images: List[Tuple[Path, Image.Image]], 
                                    embeddings: np.ndarray,
                                    n_clusters: int = 10) -> Dict[int, List[Tuple[Path, Image.Image]]]:
        """
        Sort images using K-means clustering on embeddings.
        
        Args:
            images: List of (file_path, PIL_Image) tuples
            embeddings: Image embeddings array
            n_clusters: Number of clusters to create
            
        Returns:
            Dictionary mapping cluster_id to list of images
        """
        print(f"Clustering images into {n_clusters} groups...")
        
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        cluster_labels = kmeans.fit_predict(embeddings)
        
        clusters = {}
        for i, label in enumerate(cluster_labels):
            if label not in clusters:
                clusters[label] = []
            clusters[label].append(images[i])
            
        return clusters
    
    def sort_by_content_categories(self, 
                                 images: List[Tuple[Path, Image.Image]], 
                                 embeddings: np.ndarray) -> Dict[str, List[Tuple[Path, Image.Image]]]:
        """
        Sort images by predefined content categories using text-image similarity.
        
        Args:
            images: List of (file_path, PIL_Image) tuples
            embeddings: Image embeddings array
            
        Returns:
            Dictionary mapping category names to lists of images
        """
        print("Sorting images by content categories...")
        
        # Generate text embeddings for all category descriptions
        all_category_texts = []
        category_mapping = {}
        
        for category, descriptions in self.categories.items():
            for desc in descriptions:
                all_category_texts.append(f"a photo of {desc}")
                category_mapping[len(all_category_texts) - 1] = category
        
        text_embeddings = self.get_text_embeddings(all_category_texts)
        
        # Calculate similarity between each image and all category descriptions
        similarities = cosine_similarity(embeddings, text_embeddings)
        
        # Assign each image to the best matching category
        categorized_images = {category: [] for category in self.categories.keys()}
        categorized_images["uncategorized"] = []
        
        for i, (file_path, img) in enumerate(images):
            # Find the category with highest similarity
            best_match_idx = np.argmax(similarities[i])
            best_similarity = similarities[i][best_match_idx]
            
            # Only assign to category if similarity is above threshold
            if best_similarity > 0.25:  # Adjust threshold as needed
                category = category_mapping[best_match_idx]
                categorized_images[category].append((file_path, img))
            else:
                categorized_images["uncategorized"].append((file_path, img))
                
        return categorized_images
    
    def save_sorted_images(self, 
                          sorted_images: Dict[str, List[Tuple[Path, Image.Image]]], 
                          output_dir: Path,
                          copy_files: bool = True) -> None:
        """
        Save sorted images to organized directory structure.
        
        Args:
            sorted_images: Dictionary mapping categories to image lists
            output_dir: Output directory path
            copy_files: Whether to copy files (True) or create symlinks (False)
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Saving sorted images to: {output_dir}")
        
        # Save sorting results as JSON
        results = {}
        
        for category, image_list in sorted_images.items():
            if not image_list:
                continue
                
            category_dir = output_dir / category
            category_dir.mkdir(exist_ok=True)
            
            results[category] = []
            
            for file_path, _ in image_list:
                # Generate unique filename to avoid conflicts
                new_filename = f"{len(results[category]):04d}_{file_path.name}"
                new_path = category_dir / new_filename
                
                try:
                    if copy_files:
                        shutil.copy2(file_path, new_path)
                    else:
                        # Create symlink (Unix/Linux only)
                        new_path.symlink_to(file_path.absolute())
                        
                    results[category].append({
                        "original_path": str(file_path),
                        "new_path": str(new_path),
                        "filename": new_filename
                    })
                    
                except Exception as e:
                    print(f"Warning: Could not copy {file_path}: {e}")
        
        # Save results metadata
        with open(output_dir / "sorting_results.json", "w") as f:
            json.dump(results, f, indent=2)
            
        # Print summary
        print("\nSorting Summary:")
        for category, image_list in sorted_images.items():
            if image_list:
                print(f"  {category}: {len(image_list)} images")

def main():
    parser = argparse.ArgumentParser(description="Sort images by content using CLIP embeddings")
    parser.add_argument("--input_dir", type=str, required=True, 
                       help="Input directory containing images")
    parser.add_argument("--output_dir", type=str, required=True,
                       help="Output directory for sorted images")
    parser.add_argument("--method", type=str, choices=["clustering", "categories", "both"], 
                       default="categories", help="Sorting method")
    parser.add_argument("--n_clusters", type=int, default=10,
                       help="Number of clusters for clustering method")
    parser.add_argument("--model", type=str, default="openai/clip-vit-base-patch32",
                       help="CLIP model to use")
    parser.add_argument("--copy", action="store_true", default=True,
                       help="Copy files instead of creating symlinks")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    
    if not input_dir.exists():
        print(f"Error: Input directory {input_dir} does not exist")
        return
    
    # Initialize sorter
    sorter = ImageContentSorter(model_name=args.model)
    
    # Load images
    images = sorter.load_images(input_dir)
    if not images:
        print("No images found to process")
        return
    
    # Generate embeddings
    embeddings = sorter.get_image_embeddings(images)
    
    # Sort images based on method
    if args.method == "clustering" or args.method == "both":
        print("\n" + "="*50)
        print("CLUSTERING-BASED SORTING")
        print("="*50)
        
        clusters = sorter.sort_by_similarity_clustering(images, embeddings, args.n_clusters)
        cluster_output_dir = output_dir / "by_similarity"
        
        # Convert cluster numbers to string keys
        cluster_dict = {f"cluster_{k:02d}": v for k, v in clusters.items()}
        sorter.save_sorted_images(cluster_dict, cluster_output_dir, args.copy)
    
    if args.method == "categories" or args.method == "both":
        print("\n" + "="*50)
        print("CATEGORY-BASED SORTING")
        print("="*50)
        
        categorized = sorter.sort_by_content_categories(images, embeddings)
        category_output_dir = output_dir / "by_content" if args.method == "both" else output_dir
        sorter.save_sorted_images(categorized, category_output_dir, args.copy)
    
    print(f"\nSorting completed! Results saved to: {output_dir}")

if __name__ == "__main__":
    main()
