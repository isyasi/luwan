param([switch]$Check)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$framesRoot = Join-Path $root '桌宠帧'
$idle = @('idle_v2','idle_read_v2','idle_fish_v1','eat_rice_v1','eat_spin_v1_loop_preview')

function Get-Frames([string]$name) {
    $path = Join-Path $framesRoot $name
    $files = @(Get-ChildItem -LiteralPath $path -Filter '*.png' -File | Sort-Object Name | ForEach-Object FullName)
    if ($files.Count -eq 0) { throw "缺少动画帧：$path" }
    return ,$files
}
if ($Check) {
    foreach ($name in $idle + @('speak_v1')) { "$name : $((Get-Frames $name).Count) 帧" }
    exit 0
}
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
$window = New-Object Windows.Window
$window.Title = '桌宠'
$window.WindowStyle = 'None'
$window.AllowsTransparency = $true
$window.Background = [Windows.Media.Brushes]::Transparent
$window.Topmost = $true
$window.ShowInTaskbar = $true
$window.Width = 330
$window.Height = 570
$window.Left = 100
$window.Top = 100
$grid = New-Object Windows.Controls.Grid
$grid.RowDefinitions.Add((New-Object Windows.Controls.RowDefinition))
$grid.RowDefinitions.Add((New-Object Windows.Controls.RowDefinition))
$grid.RowDefinitions[0].Height = [Windows.GridLength]::new(72)
$grid.RowDefinitions[1].Height = [Windows.GridLength]::new(1,[Windows.GridUnitType]::Star)
$bubble = New-Object Windows.Controls.Border
$bubble.Background = [Windows.Media.Brushes]::White
$bubble.CornerRadius = [Windows.CornerRadius]::new(12)
$bubble.Padding = [Windows.Thickness]::new(10)
$bubble.Margin = [Windows.Thickness]::new(8,4,8,4)
$bubble.Visibility = 'Collapsed'
$caption = New-Object Windows.Controls.TextBlock
$caption.TextWrapping = 'Wrap'
$caption.MaxHeight = 55
$caption.Foreground = [Windows.Media.Brushes]::Black
$bubble.Child = $caption
[Windows.Controls.Grid]::SetRow($bubble,0)
$grid.Children.Add($bubble) | Out-Null
$picture = New-Object Windows.Controls.Image
$picture.Stretch = 'Uniform'
[Windows.Media.RenderOptions]::SetBitmapScalingMode($picture,[Windows.Media.BitmapScalingMode]::HighQuality)
[Windows.Controls.Grid]::SetRow($picture,1)
$grid.Children.Add($picture) | Out-Null
$window.Content = $grid
$window.Add_MouseLeftButtonDown({ if ($_.ButtonState -eq 'Pressed') { $window.DragMove() } })

$menu = New-Object Windows.Controls.ContextMenu
$menu.Resources = [Windows.Markup.XamlReader]::Load([Xml.XmlReader]::Create((Join-Path $root '菜单样式.xaml')))
$menu.SetResourceReference([Windows.FrameworkElement]::StyleProperty,[Windows.Controls.ContextMenu])
$heading = New-Object Windows.Controls.MenuItem
$heading.Header = "小鲸鱼  ·  桌面伙伴"
$heading.IsEnabled = $false
$heading.FontSize = 14
$heading.FontWeight = 'SemiBold'
$menu.Items.Add($heading) | Out-Null
$menu.Items.Add((New-Object Windows.Controls.Separator)) | Out-Null
$listen = New-Object Windows.Controls.MenuItem
$listen.Header = '开始听我说话'
$listen.IsCheckable = $true
$listen.ToolTip = '识别麦克风语音，并自动发送到已保存的 DSH 对话。'
$speechConfigFile = Join-Path $root '语音设置.json'
if (Test-Path -LiteralPath $speechConfigFile) {
    $speechConfig = Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $listen.IsChecked = [bool]$speechConfig.vad_enabled
}
$listen.Add_Click({
    try {
        $speechConfig = if (Test-Path -LiteralPath $speechConfigFile) { Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
        $speechConfig | Add-Member -NotePropertyName vad_enabled -NotePropertyValue ([bool]$listen.IsChecked) -Force
        [IO.File]::WriteAllText($speechConfigFile,($speechConfig | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        if ($listen.IsChecked) {
            if (-not $speechConfig.conversation_id) { throw '请先在语音设置中选择 DSH 对话并保存。' }
            Start-Process -FilePath 'C:\ProgramData\miniconda3\pythonw.exe' -ArgumentList ('"' + (Join-Path $root '语音链路.py') + '"') -WorkingDirectory $root
        }
    } catch {
        $listen.IsChecked = -not $listen.IsChecked
        $speechConfig | Add-Member -NotePropertyName vad_enabled -NotePropertyValue ([bool]$listen.IsChecked) -Force
        [IO.File]::WriteAllText($speechConfigFile,($speechConfig | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        [Windows.MessageBox]::Show($_.Exception.Message,'保存监听设置失败') | Out-Null
    }
})
$menu.Items.Add($listen) | Out-Null
if ($listen.IsChecked -and $speechConfig.conversation_id) {
    Start-Process -FilePath 'C:\ProgramData\miniconda3\pythonw.exe' -ArgumentList ('"' + (Join-Path $root '语音链路.py') + '"') -WorkingDirectory $root
}
$vision = New-Object Windows.Controls.MenuItem
$vision.Header = '屏幕视觉'
$vision.IsCheckable = $true
$vision.ToolTip = '定时截取最新一帧，经 Flash 眼睛写入 screen_state，并在值得说时主动开口。'
if (Test-Path -LiteralPath $speechConfigFile) {
    $speechConfig = Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $vision.IsChecked = [bool]$speechConfig.vision_enabled
}
function Start-WhaleVision {
    $existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -like ('*"' + (Join-Path $root '屏幕视觉.py') + '"*')
    }
    if (-not $existing) {
        Start-Process -FilePath 'C:\ProgramData\miniconda3\pythonw.exe' -ArgumentList ('"' + (Join-Path $root '屏幕视觉.py') + '"') -WorkingDirectory $root
    }
}
function Stop-WhaleVision {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -like ('*"' + (Join-Path $root '屏幕视觉.py') + '"*')
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
$vision.Add_Click({
    try {
        $speechConfig = if (Test-Path -LiteralPath $speechConfigFile) { Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { [pscustomobject]@{} }
        $speechConfig | Add-Member -NotePropertyName vision_enabled -NotePropertyValue ([bool]$vision.IsChecked) -Force
        if (-not $speechConfig.PSObject.Properties['vision_interval_sec']) { $speechConfig | Add-Member -NotePropertyName vision_interval_sec -NotePropertyValue 1 -Force }
        if (-not $speechConfig.PSObject.Properties['vision_cooldown_sec']) { $speechConfig | Add-Member -NotePropertyName vision_cooldown_sec -NotePropertyValue 8 -Force }
        [IO.File]::WriteAllText($speechConfigFile,($speechConfig | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        if ($vision.IsChecked) { Start-WhaleVision } else { Stop-WhaleVision }
    } catch {
        $vision.IsChecked = -not $vision.IsChecked
        $speechConfig | Add-Member -NotePropertyName vision_enabled -NotePropertyValue ([bool]$vision.IsChecked) -Force
        [IO.File]::WriteAllText($speechConfigFile,($speechConfig | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        [Windows.MessageBox]::Show($_.Exception.Message,'保存屏幕视觉设置失败') | Out-Null
    }
})
$menu.Items.Add($vision) | Out-Null
if ($vision.IsChecked) { Start-WhaleVision }
$audioSettings = New-Object Windows.Controls.MenuItem

$audioSettings.Header = '⚙   语音与对话设置'
$audioSettings.Add_Click({
    Start-Process -FilePath 'C:\ProgramData\miniconda3\pythonw.exe' -ArgumentList ('"' + (Join-Path $root '语音设置.py') + '"') -WorkingDirectory $root
})
$menu.Items.Add($audioSettings) | Out-Null
$speak = New-Object Windows.Controls.MenuItem
$speak.Header = '▷   播放说话动作'
$speak.Add_Click({ $script:clip = 'speak_v1'; $script:frame = 0; $script:loops = 0 })
$menu.Items.Add($speak) | Out-Null
$sizeHeading = New-Object Windows.Controls.MenuItem
$sizeHeading.Header = '桌宠大小'
$sizeHeading.IsEnabled = $false
$menu.Items.Add($sizeHeading) | Out-Null
$sizeFile = Join-Path $root '桌宠尺寸.txt'
$savedSize = 1.0
if (Test-Path -LiteralPath $sizeFile) {
    $parsed = 0.0
    if ([double]::TryParse((Get-Content -LiteralPath $sizeFile -Raw),[ref]$parsed)) {
        $savedSize = [Math]::Max(0.5,[Math]::Min(2.0,$parsed))
    }
}
$sizePanel = New-Object Windows.Controls.StackPanel
$sizePanel.Orientation = 'Horizontal'
$minLabel = New-Object Windows.Controls.TextBlock
$minLabel.Text = '50%'
$minLabel.Margin = [Windows.Thickness]::new(4)
$sizePanel.Children.Add($minLabel) | Out-Null
$sizeSlider = New-Object Windows.Controls.Slider
$sizeSlider.Minimum = 0.5
$sizeSlider.Maximum = 2.0
$sizeSlider.Width = 150
$sizeSlider.Foreground = [Windows.Media.BrushConverter]::new().ConvertFromString('#329BBC')
$sizeSlider.Value = $savedSize
$sizeSlider.Margin = [Windows.Thickness]::new(4)
$sizeSlider.IsSnapToTickEnabled = $false
$sizePanel.Children.Add($sizeSlider) | Out-Null
$maxLabel = New-Object Windows.Controls.TextBlock
$maxLabel.Text = '200%'
$maxLabel.Margin = [Windows.Thickness]::new(4)
$sizePanel.Children.Add($maxLabel) | Out-Null
$sizeItem = New-Object Windows.Controls.MenuItem
$sizeItem.Header = $sizePanel
$sizeItem.StaysOpenOnClick = $true
$menu.Items.Add($sizeItem) | Out-Null
$window.Width = 330 * $savedSize
$window.Height = 570 * $savedSize
$sizeSlider.Add_ValueChanged({
    $window.Width = 330 * $sizeSlider.Value
    $window.Height = 570 * $sizeSlider.Value
    [IO.File]::WriteAllText($sizeFile,$sizeSlider.Value.ToString([Globalization.CultureInfo]::InvariantCulture))
})
$quit = New-Object Windows.Controls.MenuItem
$menu.Items.Add((New-Object Windows.Controls.Separator)) | Out-Null
$quit.Header = '×   退出桌宠'
$quit.Foreground = [Windows.Media.BrushConverter]::new().ConvertFromString('#AD5364')
$quit.Add_Click({ $window.Close() })
$menu.Items.Add($quit) | Out-Null
$grid.ContextMenu = $menu

$script:allFrames = @{}
foreach ($name in $idle + @('speak_v1')) { $script:allFrames[$name] = Get-Frames $name }
$script:clipIndex = 0
$script:clip = $idle[0]
$script:frame = 0
$script:loops = 0
$captionFile = Join-Path $root '字幕.txt'
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(83)
$timer.Add_Tick({
    $files = $script:allFrames[$script:clip]
    $bitmap = New-Object Windows.Media.Imaging.BitmapImage
    $bitmap.BeginInit()
    $bitmap.CacheOption = 'OnLoad'
    $bitmap.UriSource = [Uri]$files[$script:frame]
    $bitmap.EndInit()
    $bitmap.Freeze()
    $picture.Source = $bitmap
    $script:frame++
    if ($script:frame -ge $files.Count) {
        $script:frame = 0
        $script:loops++
        if ($script:loops -ge 4) {
            $script:loops = 0
            if ($script:clip -eq 'speak_v1') { $script:clip = $idle[$script:clipIndex] }
            else { $script:clipIndex = ($script:clipIndex + 1) % $idle.Count; $script:clip = $idle[$script:clipIndex] }
        }
    }
})
$captionTimer = New-Object Windows.Threading.DispatcherTimer
$captionTimer.Interval = [TimeSpan]::FromMilliseconds(500)
$captionTimer.Add_Tick({
    $statusFile = Join-Path $root '监听状态.json'
    if (Test-Path -LiteralPath $statusFile) {
        try {
            $audioState = Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $isSpeaking = $audioState.status -eq '正在播报回复'
            if ($isSpeaking -and $script:clip -ne 'speak_v1') {
                $script:clip = 'speak_v1'; $script:frame = 0; $script:loops = 0
            } elseif (-not $isSpeaking -and $script:wasSpeaking -and $script:clip -eq 'speak_v1') {
                $script:clip = $idle[$script:clipIndex]; $script:frame = 0; $script:loops = 0
            }
            $script:wasSpeaking = $isSpeaking
        } catch {}
    }
    $value = if (Test-Path -LiteralPath $captionFile) { (Get-Content -LiteralPath $captionFile -Raw -Encoding UTF8).Trim() } else { '' }
    $screenFile = Join-Path $root 'screen_state.json'
    $visionOn = $false
    if (Test-Path -LiteralPath $speechConfigFile) {
        try {
            $cfgNow = Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $visionOn = [bool]$cfgNow.vision_enabled
        } catch {}
    }
    if (-not $value -and $visionOn -and (Test-Path -LiteralPath $screenFile)) {
        try {
            $vs = Get-Content -LiteralPath $screenFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($vs.ok -and $vs.summary) { $value = '看到：' + [string]$vs.summary }
            else { $value = '注视中' }
        } catch { $value = '注视中' }
    }
    if ($value.Length -gt 100) { $value = $value.Substring(0,100) }
    $caption.Text = $value
    $bubble.Visibility = if ($value) { 'Visible' } else { 'Collapsed' }
})
$script:ttsProcess = $null
# 百炼/云端 TTS 时不拉本地 SoVITS：听环(vad)与 TTS 提供商解耦；本地音色才需要 9880。
$needLocalTts = $false
try {
    if (Test-Path -LiteralPath $speechConfigFile) {
        $bootSpeech = Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $needLocalTts = [string]$bootSpeech.tts_provider -like '本地*'
    }
} catch { $needLocalTts = $false }
if ($needLocalTts) {
    $ttsListener = Get-NetTCPConnection -LocalPort 9880 -State Listen -ErrorAction SilentlyContinue
    if (-not $ttsListener) {
        try {
            $ttsRoot = 'E:\ai\dshzbj'
            $ttsPython = Join-Path $ttsRoot 'rvc-python\Scripts\python.exe'
            $ttsApi = Join-Path $ttsRoot 'gpt-sovits\api_v2.py'
            $ttsConfig = Join-Path $ttsRoot 'runtime\paimon-tts.yaml'
            foreach ($required in @($ttsPython,$ttsApi,$ttsConfig)) {
                if (-not (Test-Path -LiteralPath $required)) { throw "缺少本地朗读服务文件：$required" }
            }
            $script:ttsProcess = Start-Process -FilePath $ttsPython -ArgumentList @(
                '-X','utf8',('"' + $ttsApi + '"'),
                '-a','127.0.0.1','-p','9880','-c',('"' + $ttsConfig + '"')
            ) -WorkingDirectory (Join-Path $ttsRoot 'gpt-sovits') -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $root 'runtime\pet-tts-out.log') `
                -RedirectStandardError (Join-Path $root 'runtime\pet-tts-err.log')
        } catch {
            [Windows.MessageBox]::Show($_.Exception.Message,'本地朗读服务启动失败') | Out-Null
        }
    }
}
$window.Add_Closed({
    $timer.Stop(); $captionTimer.Stop()
    try {
        if ($script:ttsProcess -and -not $script:ttsProcess.HasExited) {
            Stop-Process -Id $script:ttsProcess.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    try {
        if (Test-Path -LiteralPath $speechConfigFile) {
            $closingConfig = Get-Content -LiteralPath $speechConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $closingConfig | Add-Member -NotePropertyName vad_enabled -NotePropertyValue $false -Force
            $closingConfig | Add-Member -NotePropertyName vision_enabled -NotePropertyValue $false -Force
            [IO.File]::WriteAllText($speechConfigFile,($closingConfig | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        }
        Get-CimInstance Win32_Process | Where-Object {
            $_.Name -in @('python.exe','pythonw.exe') -and
            ($_.CommandLine -like ('*"' + (Join-Path $root '语音设置.py') + '"*') -or $_.CommandLine -like ('*"' + (Join-Path $root '语音链路.py') + '"*') -or $_.CommandLine -like ('*"' + (Join-Path $root '屏幕视觉.py') + '"*') -or $_.CommandLine -match 'asr_server\.py' -or $_.CommandLine -match 'gpt-sovits\\api_v2\.py' -or $_.CommandLine -match 'gpt-sovits/api_v2\.py')
        } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        # 退出桌宠时一并停本地 ASR 常驻（饭耳托盘仍在，需要时可再点启动）
        $stopAsr = 'C:\asr\stop_asr.ps1'
        if (Test-Path -LiteralPath $stopAsr) {
            Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$stopAsr) -WindowStyle Hidden -Wait -ErrorAction SilentlyContinue
        }
    } catch {}
})
$timer.Start()
$captionTimer.Start()
$window.ShowDialog() | Out-Null
