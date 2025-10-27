# Image Content Sorter - Usage Guide

A powerful tool for organizing images based on their visual content using CLIP (Contrastive Language-Image Pre-training). This tool can automatically categorize, cluster, and search through your image collections.

## 🚀 Quick Start

### Windows Users (Easiest)
1. Double-click `sort_images.bat`
2. Follow the interactive prompts
3. Choose your sorting mode and directories

### Command Line Users
```bash
# Install dependencies
pip install -r requirements_sorter.txt

# Basic categorization
python advanced_image_sorter.py "input_folder" "output_folder" --mode categories

# Clustering
python advanced_image_sorter.py "input_folder" "output_folder" --mode cluster

# Search for specific content
python advanced_image_sorter.py "input_folder" "output_folder" --mode query --query "sunset landscape"
```

## 📋 Installation

### Prerequisites
- Python 3.7 or higher
- 4GB+ RAM recommended
- CUDA-compatible GPU (optional, for faster processing)

### Step-by-Step Installation

1. **Install Python** (if not already installed)
   - Download from [python.org](https://python.org)
   - Make sure to check "Add Python to PATH" during installation

2. **Install Dependencies**
   ```bash
   pip install -r requirements_sorter.txt
   ```

3. **For GPU Acceleration** (optional)
   - Visit [PyTorch Installation Guide](https://pytorch.org/get-started/locally/)
   - Install CUDA-enabled PyTorch for your system

## 🛠️ Usage Modes

### 1. Category-Based Sorting

Automatically sorts images into predefined categories like portraits, landscapes, animals, etc.

```bash
python advanced_image_sorter.py "C:\Photos" "C:\Sorted" --mode categories
```

**Default Categories:**
- Portraits (faces, people, selfies)
- Landscapes (nature, scenery, outdoors)
- Animals (pets, wildlife, zoo animals)
- Architecture (buildings, houses, cities)
- Vehicles (cars, trucks, planes, boats)
- Food (meals, cooking, restaurants)
- Abstract (patterns, textures, geometric)
- Art (paintings, drawings, sculptures)
- Technology (computers, gadgets, electronics)
- Sports (athletics, games, fitness)

### 2. Clustering Mode

Groups similar images together without predefined categories.

```bash
# Auto-determine number of clusters
python advanced_image_sorter.py "C:\Photos" "C:\Clustered" --mode cluster

# Specify number of clusters
python advanced_image_sorter.py "C:\Photos" "C:\Clustered" --mode cluster --clusters 8
```

Features:
- Creates visual similarity groups
- Generates t-SNE visualization
- Saves clustering analysis

### 3. Query Mode

Find images matching specific text descriptions.

```bash
# Find sunset images
python advanced_image_sorter.py "C:\Photos" "C:\Sunsets" --mode query --query "sunset landscape"

# Find portraits with specific number of results
python advanced_image_sorter.py "C:\Photos" "C:\Portraits" --mode query --query "portrait face" --top-k 50
```

## ⚙️ Configuration

### Basic Configuration File (`sorter_config.yaml`)

```yaml
# Similarity threshold (0.0 to 1.0)
similarity_threshold: 0.7

# Custom categories
categories:
  my_pets:
    - dog
    - cat
    - my pet
  vacation_photos:
    - beach
    - travel
    - vacation

# Clustering settings
clustering:
  max_clusters: 10
  min_cluster_size: 3
```

### Advanced Options

```bash
# Use custom config file
python advanced_image_sorter.py "input" "output" --config my_config.yaml

# Move files instead of copying
python advanced_image_sorter.py "input" "output" --mode categories --move

# Combine multiple options
python advanced_image_sorter.py "input" "output" --mode query --query "nature" --top-k 100 --move
```

## 📁 Output Structure

### Category Mode
```
output_folder/
├── portraits/
│   ├── image1.jpg
│   └── image2.jpg
├── landscapes/
│   ├── image3.jpg
│   └── image4.jpg
├── uncategorized/
│   └── unclear_image.jpg
└── categorization_results.json
```

### Cluster Mode
```
output_folder/
├── cluster_01/
├── cluster_02/
├── cluster_03/
├── clustering_results.json
└── cluster_visualization.png
```

### Query Mode
```
output_folder/
├── 01_0.987_sunset1.jpg
├── 02_0.943_sunset2.jpg
├── 03_0.891_sunset3.jpg
└── query_results.json
```

## 📊 Understanding Results

### Similarity Scores
- **0.9-1.0**: Excellent match
- **0.8-0.9**: Very good match  
- **0.7-0.8**: Good match
- **0.6-0.7**: Fair match
- **<0.6**: Poor match

### Result Files
- **JSON files**: Contain detailed analysis and metadata
- **Visualization PNG**: Shows cluster relationships (cluster mode)
- **Numbered files**: Ranked by similarity score (query mode)

## 🔧 Troubleshooting

### Common Issues

**1. "Import torch could not be resolved"**
```bash
pip install torch torchvision
```

**2. "CUDA out of memory"**
- Use CPU mode: Set `device: "cpu"` in config
- Process fewer images at once
- Close other GPU-using applications

**3. "No images found"**
- Check input directory path
- Ensure images have supported extensions (.jpg, .png, .bmp, .tiff, .webp)
- Verify read permissions

**4. Slow processing**
- Enable GPU acceleration
- Reduce image resolution
- Process in smaller batches

### Performance Tips

**For Large Collections (10,000+ images):**
1. Use GPU acceleration
2. Process in batches
3. Enable embedding cache
4. Use SSD storage

**For Better Accuracy:**
1. Adjust similarity threshold
2. Customize categories for your content
3. Use descriptive queries
4. Review and refine results

## 🎯 Best Practices

### Category Customization
```yaml
categories:
  # Be specific for better results
  wedding_photos:
    - wedding
    - bride
    - groom
    - wedding dress
    - wedding ceremony
  
  # Use multiple related terms
  food_photography:
    - food
    - restaurant
    - cooking
    - delicious meal
    - gourmet food
```

### Query Tips
- Use descriptive phrases: "golden hour landscape" vs "landscape"
- Combine concepts: "woman portrait indoor lighting"
- Be specific: "red sports car" vs "car"
- Try different phrasings if results aren't good

### Organization Workflow
1. **Start with clustering** to understand your collection
2. **Use categories** for general organization
3. **Use queries** to find specific content
4. **Customize config** based on your specific needs

## 🔄 Batch Processing

### Processing Multiple Folders
```bash
# Windows batch script example
for /d %%i in (C:\Photos\*) do (
    python advanced_image_sorter.py "%%i" "C:\Sorted\%%~ni" --mode categories
)
```

### Automated Workflows
Create scripts to:
- Monitor folders for new images
- Automatically sort incoming photos
- Generate regular reports
- Backup organized collections

## 📈 Advanced Features

### Custom CLIP Models
```yaml
advanced:
  clip_model: "ViT-L/14"  # Larger, more accurate model
```

### Embedding Cache
```yaml
advanced:
  cache_embeddings: true  # Faster repeated processing
```

### Thumbnail Generation
```yaml
output:
  create_thumbnails: true
  thumbnail_size: 256
```

## 🆘 Getting Help

### Check These First
1. Verify all dependencies are installed
2. Check file paths and permissions
3. Review error messages in terminal
4. Ensure sufficient disk space

### Common Commands for Diagnostics
```bash
# Check Python version
python --version

# Verify installations
python -c "import torch, clip, PIL; print('All imports successful')"

# Test with small dataset first
python advanced_image_sorter.py "test_folder" "test_output" --mode categories
```

### Support Resources
- Check the error logs in output directories
- Review the JSON result files for insights
- Test with different similarity thresholds
- Try processing smaller batches first

## 📝 Example Workflows

### Family Photo Organization
```bash
# 1. First, cluster to see what you have
python advanced_image_sorter.py "Family_Photos" "Clustered" --mode cluster

# 2. Then categorize by content
python advanced_image_sorter.py "Family_Photos" "Categorized" --mode categories

# 3. Find specific events
python advanced_image_sorter.py "Family_Photos" "Birthdays" --mode query --query "birthday party celebration"
```

### Professional Photography
```bash
# Sort by content type
python advanced_image_sorter.py "Photoshoot" "Sorted" --mode categories --config professional_config.yaml

# Find best portraits
python advanced_image_sorter.py "Portraits" "Best_Portraits" --mode query --query "professional portrait studio lighting" --top-k 50
```

---

## 📄 Files in This Package

- `image_content_sorter.py` - Basic version with essential features
- `advanced_image_sorter.py` - Full-featured version with all options
- `sorter_config.yaml` - Configuration file with customizable settings
- `requirements_sorter.txt` - Python dependencies list
- `sort_images.bat` - Windows batch script for easy usage
- `IMAGE_SORTER_GUIDE.md` - This comprehensive guide

Ready to organize your images? Start with the batch script for the easiest experience, or dive into the command line for full control!
