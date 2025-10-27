# Image Content Sorting Tools

This folder contains a complete toolkit for sorting and organizing images based on their visual content using AI (CLIP).

## 📁 Files Overview

### Main Scripts
- **`image_content_sorter.py`** - Basic version with essential sorting features
- **`advanced_image_sorter.py`** - Advanced version with clustering, visualization, and custom queries

### Configuration & Setup
- **`sorter_config.yaml`** - Configuration file for customizing categories and settings
- **`requirements_sorter.txt`** - Python dependencies list
- **`sort_images.bat`** - Windows batch script for easy point-and-click usage

### Documentation
- **`IMAGE_SORTER_GUIDE.md`** - Comprehensive usage guide and documentation

## 🚀 Quick Start

### For Windows Users (Easiest)
1. Double-click `sort_images.bat`
2. Follow the prompts to install dependencies if needed
3. Choose your sorting mode and directories

### For Command Line Users
```bash
# Install dependencies
pip install -r requirements_sorter.txt

# Run basic sorting
python advanced_image_sorter.py "input_folder" "output_folder" --mode categories
```

## 🎯 What Can These Tools Do?

1. **Content-Based Categorization** - Automatically sort images into categories like portraits, landscapes, animals, food, etc.

2. **Smart Clustering** - Group similar images together without predefined categories

3. **Text-Based Search** - Find images that match specific descriptions like "sunset landscape" or "cat portrait"

4. **Batch Processing** - Handle thousands of images efficiently

5. **Custom Configuration** - Define your own categories and adjust settings

## 📖 Need Help?

Read the complete guide in `IMAGE_SORTER_GUIDE.md` for:
- Detailed installation instructions
- Usage examples
- Troubleshooting tips
- Advanced configuration options
- Best practices

## 🔧 System Requirements

- Python 3.7+
- 4GB+ RAM (8GB+ recommended for large collections)
- Optional: CUDA-compatible GPU for faster processing

---

Ready to organize your image collection? Start with the batch script or dive into the documentation!
