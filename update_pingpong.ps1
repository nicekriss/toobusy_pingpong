param(
    [switch]$CheckOnly,
    [switch]$AcceptedFromApp
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$StashMessage = "pingpong-auto-update-$Stamp"
$DidStash = $false
$OldHead = $null

function Write-Step([string]$Message) {
    Write-Host "`n[업데이트] $Message" -ForegroundColor Cyan
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & git -C $Repo @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 명령이 실패했습니다: git $($Arguments -join ' ')"
    }
}

function Get-DefaultBranch {
    # The updater has to follow the repo's release branch, not whichever branch
    # this folder happens to be sitting on. A stale feature branch has nothing
    # new to pull, so tracking "current branch" reports "already up to date"
    # forever while the real branch moves ahead.
    $ref = & git -C $Repo symbolic-ref --quiet refs/remotes/origin/HEAD
    if ($LASTEXITCODE -eq 0 -and $ref) {
        return ($ref -join "").Trim() -replace '^refs/remotes/origin/', ''
    }
    # origin/HEAD is missing on some clones; ask the remote itself.
    $lines = & git -C $Repo ls-remote --symref origin HEAD
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in $lines) {
            if ($line -match '^ref:\s+refs/heads/(\S+)\s+HEAD') { return $Matches[1] }
        }
    }
    return "main"
}

function Get-CommitCount([string]$Range) {
    $raw = (& git -C $Repo rev-list --count $Range) -join ""
    if ($LASTEXITCODE -ne 0) {
        throw "커밋 수를 확인하지 못했습니다: $Range"
    }
    return [int]$raw.Trim()
}

try {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor DarkGreen
    Write-Host "       Ping-Pong 간편 업데이트" -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor DarkGreen

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git이 설치되어 있지 않습니다. https://git-scm.com 에서 Git for Windows를 설치해 주세요."
    }
    if (-not (Test-Path (Join-Path $Repo ".git"))) {
        throw "이 폴더는 Git으로 받은 핑퐁 폴더가 아닙니다. GitHub에서 clone한 폴더에서 실행해 주세요."
    }

    Write-Step "새 버전을 확인합니다..."
    Invoke-Git fetch --prune origin

    $Target = Get-DefaultBranch
    $OldHead = (& git -C $Repo rev-parse HEAD).Trim()

    & git -C $Repo show-ref --verify --quiet "refs/remotes/origin/$Target"
    if ($LASTEXITCODE -ne 0) {
        throw "원격 저장소에서 '$Target' 브랜치를 찾을 수 없습니다. 인터넷 연결을 확인해 주세요."
    }

    # Empty on a detached HEAD, so don't call .Trim() on it blindly.
    $Branch = (& git -C $Repo branch --show-current) -join ""
    $Branch = $Branch.Trim()

    $Ahead = Get-CommitCount "origin/$Target..HEAD"
    if ($Ahead -gt 0) {
        # Hangul is a valid identifier character, so "$Ahead개" parses as a
        # variable named "Ahead개" and silently interpolates to nothing.
        throw "이 폴더에는 배포본에 없는 로컬 커밋이 $($Ahead)개 있습니다. 제작자에게 문의해 수동으로 병합해 주세요."
    }

    $Behind = Get-CommitCount "HEAD..origin/$Target"
    $OnTarget = ($Branch -eq $Target)

    if ($Behind -eq 0 -and $OnTarget) {
        Write-Host "`n이미 최신 버전입니다." -ForegroundColor Green
        exit 0
    }

    if (-not $OnTarget) {
        if ($Branch) {
            Write-Host "현재 '$Branch' 브랜치에 있습니다. 배포 브랜치 '$Target'으로 되돌립니다." -ForegroundColor Yellow
        } else {
            Write-Host "현재 브랜치가 분리된 상태입니다. 배포 브랜치 '$Target'으로 되돌립니다." -ForegroundColor Yellow
        }
    }
    if ($Behind -gt 0) {
        Write-Host "새 커밋 $($Behind)개가 있습니다."
    }
    if ($CheckOnly) {
        Write-Host "확인 모드이므로 파일은 변경하지 않았습니다." -ForegroundColor Yellow
        exit 0
    }
    if (-not $AcceptedFromApp) {
        $Answer = Read-Host "이 패치를 적용할까요? (y/N)"
        if ($Answer -notin @("y", "Y", "yes", "YES")) {
            Write-Host "사용자가 패치를 취소했습니다." -ForegroundColor Yellow
            exit 0
        }
    }

    $OldRequirements = $null
    $RequirementsPath = Join-Path $Repo "requirements.txt"
    if (Test-Path $RequirementsPath) {
        $OldRequirements = (Get-FileHash $RequirementsPath -Algorithm SHA256).Hash
    }

    $Dirty = & git -C $Repo status --porcelain --untracked-files=normal
    if ($Dirty) {
        Write-Step "로컬 파일을 안전하게 임시 보관합니다..."
        Invoke-Git stash push --include-untracked --message $StashMessage
        $DidStash = $true
    }

    if (-not $OnTarget) {
        Write-Step "'$Target' 브랜치로 전환합니다..."
        Invoke-Git checkout $Target
    }

    Write-Step "프로그램 파일을 받습니다..."
    Invoke-Git merge --ff-only "origin/$Target"

    if ($DidStash) {
        Write-Step "기존 로컬 파일을 복원합니다..."
        & git -C $Repo stash pop
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "업데이트는 완료됐지만 로컬 수정 파일을 자동으로 합치지 못했습니다." -ForegroundColor Yellow
            Write-Host "파일은 삭제되지 않았습니다. 이 창의 내용을 제작자에게 보내 주세요." -ForegroundColor Yellow
            Write-Host "복구용 stash 이름: $StashMessage" -ForegroundColor Yellow
            throw "로컬 수정 파일 자동 복원에 실패했습니다. 원본은 Git stash에 남아 있습니다."
        }
        $DidStash = $false
    }

    $NewRequirements = $null
    if (Test-Path $RequirementsPath) {
        $NewRequirements = (Get-FileHash $RequirementsPath -Algorithm SHA256).Hash
    }
    if ($OldRequirements -ne $NewRequirements -and (Test-Path (Join-Path $Repo ".venv\Scripts\python.exe"))) {
        Write-Step "필요한 Python 패키지를 맞춥니다..."
        & (Join-Path $Repo ".venv\Scripts\python.exe") -m pip install --quiet --upgrade -r $RequirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw "프로그램 업데이트는 됐지만 Python 패키지 설치에 실패했습니다. '설치.bat'을 한 번 실행해 주세요."
        }
    }

    $MigrationScript = Join-Path $Repo "update_manager.py"
    if (Test-Path $MigrationScript) {
        Write-Step "개인 설정을 새 버전에 안전하게 맞춥니다..."
        $PythonExe = Join-Path $Repo ".venv\Scripts\python.exe"
        if (-not (Test-Path $PythonExe)) {
            $PythonExe = (Get-Command python -ErrorAction Stop).Source
        }
        & $PythonExe $MigrationScript migrate
        if ($LASTEXITCODE -ne 0) {
            throw "프로그램은 갱신됐지만 개인 설정 마이그레이션에 실패했습니다. 백업은 그대로 보존됩니다."
        }
    }

    Write-Host "`n업데이트가 완료됐습니다. 개인 설정과 생성 파일은 그대로 유지됩니다." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "`n[업데이트 실패] $($_.Exception.Message)" -ForegroundColor Red
    if ($AcceptedFromApp -and $OldHead) {
        $CurrentHead = (& git -C $Repo rev-parse HEAD 2>$null).Trim()
        if ($CurrentHead -and $CurrentHead -ne $OldHead) {
            Write-Host "적용 전 프로그램 버전으로 되돌립니다..." -ForegroundColor Yellow
            & git -C $Repo reset --hard $OldHead | Out-Host
        }
    }
    if ($DidStash) {
        Write-Host "로컬 파일은 Git stash에 보관되어 있습니다: $StashMessage" -ForegroundColor Yellow
    }
    exit 1
}
