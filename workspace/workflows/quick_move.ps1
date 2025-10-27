# Quick Workflow Mover
# Usage: .\quick_move.ps1 <workflow_file> <category>

param(
    [Parameter(Mandatory=$true)]
    [string]$WorkflowFile,
    
    [Parameter(Mandatory=$true)]
    [ValidateSet("video-generation", "character-generation", "experimental", "flux-generation", "misc")]
    [string]$Category
)

# Get the workflow file
$file = Get-Item $WorkflowFile -ErrorAction Stop

# Create category directory if it doesn't exist
$categoryDir = "current\$Category"
if (!(Test-Path $categoryDir)) {
    New-Item -ItemType Directory -Path $categoryDir -Force
    Write-Host "Created directory: $categoryDir"
}

# Generate new filename with timestamp
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$newName = "$($file.BaseName)_$timestamp.json"
$destination = Join-Path $categoryDir $newName

# Move the file
Move-Item $file.FullName $destination

Write-Host "✅ Moved '$($file.Name)' to '$destination'" -ForegroundColor Green
Write-Host "📁 Category: $Category" -ForegroundColor Cyan






