# No fallback to a global interpreter: validate before service/config actions.
function Get-IMCPythonPath {
    $root = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'IMC .venv missing. Create Python 3.12 .venv and install requirements.txt plus CORE first.'
    }
    return $python
}
function Get-IMCPython {
    $python = Get-IMCPythonPath
    & $python -I -c 'import sys,numpy,solders,mykonos_meme_scout; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'IMC requires Python 3.12 and installed CORE/numpy/solders in .venv.' }
    return $python
}
