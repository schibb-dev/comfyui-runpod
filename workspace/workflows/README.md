# ComfyUI Workflows Organization

Reorganized on 2025-08-04 16:37:22 to support the new gradual migration pipeline.

## Directory Structure

### Current Workflows (`current/`)
Actively used workflows organized by category:
- **character-generation/** - FaceBlast variants, pose control, character creation
- **video-generation/** - WAN workflows, upscaling, video processing  
- **flux-generation/** - FLUX inpainting, outpainting, image enhancement
- **experimental/** - Testing workflows, general purpose tools

### Legacy Workflows (`legacy/`)
- **archived/** - Older versions and deprecated workflows for reference

## Output Path Configuration

All current workflows have been configured to use the new output structure:
- Character Generation → `output/wip/character-generation/`
- Video Generation → `output/wip/video-generation/`
- FLUX Generation → `output/wip/flux-generation/`
- Experimental → `output/wip/experimental/`

## Migration Pipeline Integration

This organization integrates with the gradual migration pipeline:
1. **Generate** in `output/wip/[category]/`
2. **Review** in `output/staging/[category]/`  
3. **Rate & Sort** in `workshop/[rating]/`
4. **Curate** in `gallery/[category]/`

## Usage Guidelines

1. **Active Development**: Use workflows from `current/` subdirectories
2. **Reference**: Check `legacy/archived/` for older techniques
3. **New Workflows**: Save in appropriate `current/` subdirectory
4. **Output Paths**: Always configure workflows to use category-specific WIP paths

## Workflow Categories

### Character Generation
- Face generation (FaceBlast variants)
- Pose control and character positioning  
- Scene generation and character interaction
- Text-to-image character workflows

### Video Generation  
- WAN (image-to-video) workflows
- Video upscaling and enhancement
- Frame interpolation and smoothing
- Video processing pipelines

### FLUX Generation
- FLUX model inpainting workflows
- Outpainting and image extension
- Image enhancement and refinement
- FLUX-specific text-to-image

### Experimental
- Testing new techniques and models
- General purpose workflows
- Development and R&D workflows
- Utility and helper workflows

Last updated: 2025-08-04 16:37:22
