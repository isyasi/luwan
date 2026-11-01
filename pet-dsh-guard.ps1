# 桌宠联动守护：DSH 启动 → 桌宠启动；DSH 关闭 → 桌宠关闭
# 用法：后台常驻运行本脚本（见 启动联动守护.cmd）
# 逻辑：每 3 秒检查一次 DeepSeek Harness 主进程（UI 壳，命令行不含 --type=）是否存在。

$ErrorActionPreference = 'SilentlyContinue'

$root = 'E:\小鲸鱼'
$petScript = Join-Path $root '桌宠变声.ps1'
$dshProcessName = 'DeepSeek Harness.exe'
$checkInterval = 3   # 秒

# 判断 DSH 是否在运行：存在名为 DeepSeek Harness.exe 且命令行不含 --type= 的进程（UI 主壳）
function Test-DshRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name='$dshProcessName'"
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -notlike '*--type=*') {
            return $true
        }
    }
    return $false
}

# 查找正在运行的桌宠进程（命令行里含 桌宠变声.ps1 的 powershell）
function Get-PetProcess {
    $procs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'"
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -like '*桌宠变声.ps1*') {
            return $p
        }
    }
    return $null
}

# 启动桌宠
function Start-Pet {
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-STA','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',('"' + $petScript + '"')) -WorkingDirectory $root
    } catch {}
}

# 关闭桌宠：结束桌宠进程，并顺带结束它拉起的语音链路 python
function Stop-Pet {
    $pet = Get-PetProcess
    if ($pet) {
        Stop-Process -Id $pet.ProcessId -Force
    }
    # 结束语音链路 / 语音设置 的 python 进程
    $pyProcs = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -in @('python.exe','pythonw.exe')) -and
        ($_.CommandLine -like '*语音链路.py*' -or $_.CommandLine -like '*语音设置.py*')
    }
    foreach ($p in $pyProcs) {
        Stop-Process -Id $p.ProcessId -Force
    }
}

# 主循环
$wasRunning = $null
while ($true) {
    $dshRunning = Test-DshRunning

    # 只在状态变化时动作，避免重复启动/关闭
    if ($wasRunning -ne $null -and $dshRunning -eq $wasRunning) {
        Start-Sleep -Seconds $checkInterval
        continue
    }

    if ($dshRunning) {
        # DSH 刚启动：确保桌宠在跑
        if (-not (Get-PetProcess)) {
            Start-Pet
        }
    } else {
        # DSH 已关闭：关闭桌宠
        Stop-Pet
    }

    $wasRunning = $dshRunning
    Start-Sleep -Seconds $checkInterval
}
