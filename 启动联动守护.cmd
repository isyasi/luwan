@echo off
rem Start pet-DSH guard (background, hidden)
start "" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "& '%~dp0pet-dsh-guard.ps1'"
