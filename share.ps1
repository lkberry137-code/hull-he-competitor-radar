$py = "$env:LOCALAPPDATA\Python\pythoncore-3.15-64\python.exe"
if (Test-Path $py) {
    & $py "$PSScriptRoot\tunnel.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py "$PSScriptRoot\tunnel.py"
} else {
    python "$PSScriptRoot\tunnel.py"
}
