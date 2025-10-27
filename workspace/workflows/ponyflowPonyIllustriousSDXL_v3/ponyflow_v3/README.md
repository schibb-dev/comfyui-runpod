PonyFlow
========

## Installation

Install ComfyUI https://github.com/comfyanonymous/ComfyUI
Install ComfyUI Manager https://github.com/ltdrdata/ComfyUI-Manager

- Open ComfyUI
- Load the workflow
- Manager > Install Missing Custom Nodes
    - Install all of the missing nodes (I use nightly versions for everything)
- Restart ComfyUI
- Install all of the models listed below to their proper places
- Copy the `ponyflow_wildcards.yaml` wildcard file into the `ComfyUI\custom_nodes\comfyui-impact-pack\wildcards` directory.
- Restart ComfyUI
- Trying to run this initially, you may run into an error with the HighRes-Fix Script node. To remedy this, create a new version of the node, make sure the paramters are the same, then reconnect the new one to the KSampler node and delete the old one. No clue why this happens, but it happens consistently the first time...

## Model Downloads

### Checkpoint

Install to `ComfyUI\models\checkpoints`
Any Pony model will work. CyberRealistic Pony is one of the most popular models that works out of the box.
https://civitai.com/models/443821/cyberrealistic-pony?modelVersionId=1346181

### Controlnet
Install to `ComfyUI\models\controlnet` (I renamed mine to controlnet_union_promax.safetensors)
https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/blob/main/diffusion_pytorch_model_promax.safetensors

### Depth Anything
The Depth Anything V2 - Relative node will automatically download and install the needed model.

### Upscale models
Install to `ComfyUI\models\upscale_models`
https://civitai.com/models/147759/remacri

### Loras
Install to `ComfyUI\models\loras`
https://huggingface.co/tianweiy/DMD2/blob/main/dmd2_sdxl_4step_lora.safetensors
https://huggingface.co/tianweiy/DMD2/blob/main/dmd2_sdxl_4step_lora_fp16.safetensors

### CLIP-ViT-H-14-laion2B-s32B-b79K
Install to `ComfyUI\models\clip_vision`
https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K/blob/main/model.safetensors

### IPAdapter ip-adapter-plus_sdxl_vit-h
Install to `ComfyUI\models\ipadapter` (You must manually create this folder)
https://huggingface.co/h94/IP-Adapter/blob/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors

## Usage

This workflow is focused on creating high-quality, unique images for Pony and SDXL.

It contains different features, all controlled by the Fast Groups Bypasser (rgthree) node. The names of the features correspond to the named groups in the workflow. Turning one off will bypass the entire group.

For general text2img, I use:
- Enable Initial Gen
- Enable Highres
- Enable Film Grain (optional)

You can toggle on and off previews if you want to mess with scheduler/ upscaling settings to see differences.

### Skimmed CFG

https://github.com/Extraltodeus/Skimmed_CFG

Many people ask how I make my images. They're hard to replicate solely because of this node. Skimmed CFG makes allows your to turn up CFG values and prompt weights (photorealistic:1.5). Try out 5/12/24/48 CFG while setting Skimmed CFG to 3/5/7!

It allows you to weight your prompts more heavily and develop unique styles. For example, try adding (abstract minimal art:1.9) to your next prompt!

For a baseline, I recommend Skimming CFG at 5.0 and CFG at 12. Please test these values out per-model though because they change the output significantly.

## ImpactWildcardEncode

The positive and negative prompt inputs are wildcard encoders. These process the included yaml file into new unique prompts! This lets you change up pieces of the scene, person, or the entire prompt! The default example shows my normal workflow.

Pick a random person, give them a random body size, and put them in a random scene. You can specify more or less detail, or not use wildcards at all!

There are many techniques to using wildcards that I give examples of in `ponyflow_wildcards.yaml`.

For more information on wildcards, refer to the guide here: https://github.com/ltdrdata/ComfyUI-extension-tutorials/blob/Main/ComfyUI-Impact-Pack/tutorial/ImpactWildcard.md

When making changes to wildcards, refresh them in the ComfyUI menu under Edit > Impact: refresh Wildcard

I probably need to write an entire article on lessons learned using wildcards...

## Film Grain

I like using the post processing film grain node included in the workflow for realistic images. Try out diffferent values for scale, strenght, and saturation as you like.

## Controlnet Depth

Upload an image to the Load Controlnet Image node to use it as a template for your image. This will take the "shape" of an image, and mold your output to the shape of the input image. Enable the "Depth Sample" node to see what the image's influence is.

## Style Transfer

Upload an image to the "Load Style Image" node to copy its color scheme, and artistic qualities.

Adjust "weight_style" and "end_at" parameters to adjust the strength of the style transferred. As a default I like them both at 0.5, but each style image requires tuning.

## Extra VAE

I've included an extra "Load VAE" node in case the model you use does not have its own VAE. You can use the standard SDXL vae, or one of the many avilable on CivitAI