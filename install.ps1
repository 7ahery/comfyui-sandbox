# Installs comfyui-sandbox in one line, from the user's own PowerShell (docs/adr/0004):
#
#     irm https://raw.githubusercontent.com/7ahery/comfyui-sandbox/main/install.ps1 | iex
#
# It runs the README's manual steps: install uv with winget if it is missing, then
# `uv tool install` the tagged release, and put uv's tool folder on PATH. The line fetches this
# script from main, so $Version is what pins the release: bump it with each release. Everything runs inside a
# function and stops with `return`, never `exit`, because under `iex` an `exit` closes the user's
# window. Keep it Windows PowerShell 5.1 compatible: that is the PowerShell every Windows has.

function Install-ComfyuiSandbox {
    $Version = 'v1.0.0'
    $Source = "git+https://github.com/7ahery/comfyui-sandbox@$Version"

    # Adds the PATH entries saved for the user and the machine to this window, keeping what it
    # already has, so a program installed a moment ago runs without opening a new window.
    function Update-SessionPath {
        $have = $env:Path -split ';' | Where-Object { $_ }
        $saved = @([Environment]::GetEnvironmentVariable('Path', 'User'), [Environment]::GetEnvironmentVariable('Path', 'Machine')) -join ';'
        $new = $saved -split ';' | Where-Object { $_ -and ($have -notcontains $_) }
        $env:Path = (@($have) + @($new)) -join ';'
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host 'comfyui-sandbox needs uv, and winget is not available to install it.' -ForegroundColor Red
            Write-Host 'Install uv as described at https://docs.astral.sh/uv/, then run this line again.'
            return
        }
        Write-Host 'Installing uv with winget...'
        winget install --id astral-sh.uv --exact --source winget
        if ($LASTEXITCODE -ne 0) {
            Write-Host "winget could not install uv (exit code $LASTEXITCODE)." -ForegroundColor Red
            Write-Host 'Run `winget install astral-sh.uv` to see why, then run this line again.'
            return
        }
        Update-SessionPath
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host 'uv is installed, but this window cannot find it yet.' -ForegroundColor Red
            Write-Host 'Open a new PowerShell window and run this line again.'
            return
        }
    }

    Write-Host "Installing comfyui-sandbox $Version..."
    uv tool install $Source
    if ($LASTEXITCODE -ne 0) {
        Write-Host "uv could not install comfyui-sandbox (exit code $LASTEXITCODE). Its output above says why." -ForegroundColor Red
        return
    }
    uv tool update-shell
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'comfyui-sandbox is installed, but uv could not add its folder to PATH.' -ForegroundColor Red
        Write-Host 'Run `uv tool update-shell` to see why.'
        return
    }
    Update-SessionPath

    Write-Host ''
    Write-Host "comfyui-sandbox $Version is installed. Run it with:" -ForegroundColor Green
    Write-Host '    comfyui-sandbox'
}

Install-ComfyuiSandbox
Remove-Item Function:\Install-ComfyuiSandbox
