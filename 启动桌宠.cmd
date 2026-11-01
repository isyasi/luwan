@echo off
powershell.exe -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$p = Get-ChildItem -LiteralPath '%~dp0' -Filter '*.ps1' -File | Select-Object -First 1; if ($p) { & $p.FullName }"
