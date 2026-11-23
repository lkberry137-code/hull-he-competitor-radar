param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList
)

$py = "$env:LOCALAPPDATA\Python\pythoncore-3.15-64\python.exe"
if (Test-Path $py) {
    & $py "$PSScriptRoot\run_audit.py" @ArgsList
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py "$PSScriptRoot\run_audit.py" @ArgsList
} else {
    python "$PSScriptRoot\run_audit.py" @ArgsList
}
