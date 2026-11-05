# Double-click-equivalent cold start check (ASCII only).
# Env: TFP_EXE, TFP_DIR, TFP_PNG
# PyInstaller onefile starts a child process; the window belongs to the child,
# so match windows by process image path instead of Process.MainWindowHandle.
# The app writes no log files; page rendering is verified by screenshot pixels.

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class W {
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, int msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr lp);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

try { $null = [W]::SetProcessDpiAwarenessContext([IntPtr](-4)) } catch { }

$exe = $env:TFP_EXE
$dir = $env:TFP_DIR
$png = $env:TFP_PNG
$procName = [IO.Path]::GetFileNameWithoutExtension($exe)

function Find-Window {
  $script:found = [IntPtr]::Zero
  $cb = [W+EnumWindowsProc]{
    param([IntPtr]$h, [IntPtr]$lp)
    if ([W]::IsWindowVisible($h)) {
      $wpid = [uint32]0
      [W]::GetWindowThreadProcessId($h, [ref]$wpid) | Out-Null
      if ($wpid -ne 0) {
        try {
          $proc = Get-Process -Id $wpid -ErrorAction Stop
          if ($proc.Path -eq $exe) {
            $sb = New-Object System.Text.StringBuilder 512
            [W]::GetWindowText($h, $sb, 512) | Out-Null
            if ($sb.Length -gt 0) { $script:found = $h; return $false }
          }
        } catch { }
      }
    }
    return $true
  }
  [W]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
  return $script:found
}

function Wait-Window($timeoutSec) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $h = Find-Window
    if ($h -ne [IntPtr]::Zero) { return $h }
    if (@(Get-Process -Name $procName -ErrorAction SilentlyContinue).Count -eq 0) { return [IntPtr]::Zero }
    Start-Sleep -Milliseconds 250
  }
  return [IntPtr]::Zero
}

function Get-LogPaths {
  return @((Join-Path $dir 'shiji.log'), (Join-Path $env:TEMP 'shiji.log'), (Join-Path $dir 'shiji.log.1'))
}

function Check-NoLog {
  $existing = @()
  foreach ($p in (Get-LogPaths)) {
    if (Test-Path $p) { $existing += $p }
  }
  return @{ ok = ($existing.Count -eq 0); found = ($existing -join ';') }
}

function Close-App {
  Get-Process -Name $procName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}

$null = Close-App
foreach ($p in (Get-LogPaths)) {
  if (Test-Path $p) { Remove-Item $p -Force }
}

# --- launch 1 ---
$t0 = Get-Date
$p1 = Start-Process -FilePath $exe -WorkingDirectory $dir -PassThru
$h1 = Wait-Window 60
$t1 = ((Get-Date) - $t0).TotalSeconds
$running = @(Get-Process -Name $procName -ErrorAction SilentlyContinue).Count
"LAUNCH1 pid=$($p1.Id) window=$($h1.ToInt64()) seconds=$([math]::Round($t1,2)) processes=$running"

if ($h1 -eq [IntPtr]::Zero) { "FAIL launch1 no window"; Close-App; exit 1 }

Start-Sleep -Seconds 3

$r = New-Object W+RECT
$null = [W]::GetWindowRect($h1, [ref]$r)
$w = $r.Right - $r.Left
$hh = $r.Bottom - $r.Top
$hx = $r.Left + [int]($w / 2)
$hy = $r.Top + 14
$lp = [IntPtr]([int64]($hx -band 0xFFFF) -bor ([int64]($hy -band 0xFFFF) -shl 16))
$hit = [W]::SendMessage($h1, 0x0084, [IntPtr]0, $lp)
$bigIcon = [W]::SendMessage($h1, 0x007F, [IntPtr]1, [IntPtr]0)
$smallIcon = [W]::SendMessage($h1, 0x007F, [IntPtr]0, [IntPtr]0)
"WINDOW rect=$($r.Left),$($r.Top) ${w}x${hh} nchittest=$($hit.ToInt64()) bigIcon=$($bigIcon.ToInt64()) smallIcon=$($smallIcon.ToInt64())"

$bmp = New-Object Drawing.Bitmap $w, $hh
$g = [Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($png, [Drawing.Imaging.ImageFormat]::Png)
$colors = @{}
for ($x = 0; $x -lt $w; $x += 8) {
  for ($y = 0; $y -lt $hh; $y += 8) {
    $colors[$bmp.GetPixel($x, $y).ToArgb()] = 1
  }
}
$distinct = $colors.Count
$g.Dispose()
$bmp.Dispose()
"SCREENSHOT $png distinctColors=$distinct"

$null = [W]::PostMessage($h1, 0x0010, [IntPtr]0, [IntPtr]0)
$exited = $p1.WaitForExit(20000)
"CLOSE1 exited=$exited code=$($p1.ExitCode)"
Start-Sleep -Seconds 1

$noLog1 = Check-NoLog
"LOG1 noLog=$($noLog1.ok) found=$($noLog1.found)"

# --- launch 2 ---
$t2 = Get-Date
$p2 = Start-Process -FilePath $exe -WorkingDirectory $dir -PassThru
$h2 = Wait-Window 60
$t3 = ((Get-Date) - $t2).TotalSeconds
"LAUNCH2 pid=$($p2.Id) window=$($h2.ToInt64()) seconds=$([math]::Round($t3,2))"
if ($h2 -eq [IntPtr]::Zero) { "FAIL launch2 no window"; Close-App; exit 1 }
Start-Sleep -Seconds 2
$noLog2 = Check-NoLog
"LOG2 noLog=$($noLog2.ok) found=$($noLog2.found)"

# --- launch 3: single instance ---
$p3 = Start-Process -FilePath $exe -WorkingDirectory $dir -PassThru
Start-Sleep -Seconds 7
$p3.Refresh()
$alive = @(Get-Process -Name $procName -ErrorAction SilentlyContinue).Count
$wins = Find-Window
"INSTANCE thirdExited=$($p3.HasExited) processes=$alive window=$($wins.ToInt64())"
if (-not $p3.HasExited) { Stop-Process -Id $p3.Id -Force -ErrorAction SilentlyContinue }

$null = [W]::PostMessage($wins, 0x0010, [IntPtr]0, [IntPtr]0)
Start-Sleep -Seconds 2
Close-App

$noLog3 = Check-NoLog
"LOG3 noLog=$($noLog3.ok) found=$($noLog3.found)"

if ($distinct -lt 20) { "FAIL page not rendered distinctColors=$distinct"; exit 1 }
if ($hit.ToInt64() -ne 2) { "FAIL nchittest=$($hit.ToInt64())"; exit 1 }
if (-not $p3.HasExited) { "FAIL single instance"; exit 1 }
if (-not $noLog1.ok -or -not $noLog2.ok -or -not $noLog3.ok) { "FAIL log file created"; exit 1 }
"ALL PASS"
exit 0
