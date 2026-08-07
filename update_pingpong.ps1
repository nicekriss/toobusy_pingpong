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

    $Branch = (& git -C $Repo branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $Branch) {
        throw "현재 Git 브랜치를 확인할 수 없습니다."
    }
    $OldHead = (& git -C $Repo rev-parse HEAD).Trim()

    & git -C $Repo show-ref --verify --quiet "refs/remotes/origin/$Branch"
    if ($LASTEXITCODE -ne 0) {
        throw "원격 저장소에 '$Branch' 브랜치가 없습니다. 일반 사용자는 main 브랜치에서 실행해 주세요."
    }

    $Behind = [int]((& git -C $Repo rev-list --count "HEAD..origin/$Branch").Trim())
    $Ahead = [int]((& git -C $Repo rev-list --count "origin/$Branch..HEAD").Trim())
    if ($Ahead -gt 0) {
        throw "이 폴더에는 배포본에 없는 로컬 커밋이 $Ahead개 있습니다. 제작자에게 문의해 수동으로 병합해 주세요."
    }
    if ($Behind -eq 0) {
        Write-Host "`n이미 최신 버전입니다." -ForegroundColor Green
        exit 0
    }

    if (-not $CheckOnly -and -not $AcceptedFromApp) {
        $Answer = Read-Host "이 패치를 적용할까요? (y/N)"
        if ($Answer -notin @("y", "Y", "yes", "YES")) {
            Write-Host "사용자가 패치를 취소했습니다." -ForegroundColor Yellow
            exit 0
        }
    }

    Write-Host "새 커밋 $Behind개가 있습니다."
    if ($CheckOnly) {
        Write-Host "확인 모드이므로 파일은 변경하지 않았습니다." -ForegroundColor Yellow
        exit 0
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

    Write-Step "프로그램 파일을 받습니다..."
    Invoke-Git merge --ff-only "origin/$Branch"

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
