$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
& "C:\Users\User\anaconda3\Scripts\conda.exe" run -n AutoCapCut python -m autocapcut_app
