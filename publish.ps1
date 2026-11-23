param(
    [string]$Message = "Update University of Hull HE Competitor Radar data"
)

$py = "$env:LOCALAPPDATA\Python\pythoncore-3.15-64\python.exe"
if (Test-Path $py) {
    & $py "$PSScriptRoot\publish_github.py" $Message
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py "$PSScriptRoot\publish_github.py" $Message
} else {
    python "$PSScriptRoot\publish_github.py" $Message
}
